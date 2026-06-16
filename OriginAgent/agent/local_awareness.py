"""P5B local awareness helpers for controlled device discovery and media capture."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PNG_1X1_TRANSPARENT = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
DEFAULT_SERVICE_PORTS = [
    22,
    53,
    80,
    443,
    445,
    554,
    1883,
    1900,
    5353,
    5683,
    8008,
    8080,
    8123,
    8883,
    9000,
]
_IPV4_RE = re.compile(r"\b(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\b")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _workspace_rel(path: Path, *, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _list_text(values: Any, *, limit: int = 16, max_chars: int = 240) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    out: list[str] = []
    for item in values:
        text = _safe_text(item, max_chars=max_chars)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _private_ipv4(value: Any) -> str | None:
    try:
        addr = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address):
        return None
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return None
    if not addr.is_private:
        return None
    return str(addr)


def _device_id_for(*parts: Any) -> str:
    material = "|".join(_safe_text(part, max_chars=200).lower() for part in parts if _safe_text(part))
    digest = hashlib.sha1((material or uuid.uuid4().hex).encode("utf-8")).hexdigest()[:16]
    return f"dev_{digest}"


def _identity_key_for_device(device: dict[str, Any]) -> str:
    mac = _safe_text(device.get("mac_address"), max_chars=80).lower()
    if mac:
        return f"mac:{mac}"
    hostname = _safe_text(device.get("hostname"), max_chars=120).lower()
    vendor = _safe_text(device.get("vendor"), max_chars=120).lower()
    if hostname and vendor:
        return f"host_vendor:{hostname}|{vendor}"
    ips = sorted(str(ip).strip() for ip in device.get("ip_addresses") or [] if str(ip).strip())
    if ips:
        return f"ip:{','.join(ips)}"
    name = _safe_text(device.get("name"), max_chars=120).lower()
    return f"name:{name or device.get('device_id') or 'unknown'}"


@dataclass(frozen=True)
class DiscoveredDevice:
    device_id: str
    kind: str = "unknown"
    name: str | None = None
    hostname: str | None = None
    ip_addresses: list[str] = field(default_factory=list)
    mac_address: str | None = None
    vendor: str | None = None
    protocols: list[str] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.35
    controllable: bool = False
    last_seen_at: str = field(default_factory=_utcnow_iso)
    evidence: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    identity_key: str | None = None
    authorized_capabilities: list[str] = field(default_factory=list)
    binding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["controllable"] = False
        data["identity_key"] = data.get("identity_key") or _identity_key_for_device(data)
        data["authorized_capabilities"] = list(data.get("authorized_capabilities") or [])
        data["binding"] = data.get("binding") if isinstance(data.get("binding"), dict) else None
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> "DiscoveredDevice | None":
        if not isinstance(raw, dict):
            return None
        ips = [_private_ipv4(value) or _safe_text(value) for value in raw.get("ip_addresses") or raw.get("addresses") or []]
        ips = [ip for ip in ips if ip]
        mac = _safe_text(raw.get("mac_address") or raw.get("mac")) or None
        name = _safe_text(raw.get("name")) or None
        hostname = _safe_text(raw.get("hostname")) or None
        services = raw.get("services") if isinstance(raw.get("services"), list) else []
        protocols = _list_text(raw.get("protocols"))
        if not protocols:
            protocols = _protocols_from_services(services)
        device_id = _safe_text(raw.get("device_id")) or _device_id_for(mac, hostname, ",".join(ips), name)
        kind = _safe_text(raw.get("kind")) or "unknown"
        try:
            confidence = float(raw.get("confidence", 0.35))
        except (TypeError, ValueError):
            confidence = 0.35
        return cls(
            device_id=device_id,
            kind=kind,
            name=name,
            hostname=hostname,
            ip_addresses=ips,
            mac_address=mac,
            vendor=_safe_text(raw.get("vendor")) or None,
            protocols=protocols,
            services=[dict(item) for item in services if isinstance(item, dict)],
            confidence=confidence,
            controllable=False,
            last_seen_at=_safe_text(raw.get("last_seen_at")) or _utcnow_iso(),
            evidence=_list_text(raw.get("evidence"), limit=24, max_chars=240),
            risk_notes=_list_text(raw.get("risk_notes"), limit=12, max_chars=240),
            identity_key=_safe_text(raw.get("identity_key")) or None,
            authorized_capabilities=_list_text(raw.get("authorized_capabilities"), limit=8, max_chars=80),
            binding=dict(raw.get("binding")) if isinstance(raw.get("binding"), dict) else None,
        )


@dataclass(frozen=True)
class DeviceMap:
    devices: list[DiscoveredDevice] = field(default_factory=list)
    generated_at: str = field(default_factory=_utcnow_iso)
    scan_boundaries: dict[str, Any] = field(default_factory=dict)
    skipped_reasons: list[str] = field(default_factory=list)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        devices = [device.to_dict() for device in self.devices]
        return {
            "status": self.status,
            "generated_at": self.generated_at,
            "devices": devices,
            "device_count": len(devices),
            "scan_boundaries": dict(self.scan_boundaries),
            "skipped_reasons": list(self.skipped_reasons),
            "summary": summarize_device_map({"devices": devices, "generated_at": self.generated_at}),
        }


def summarize_device_map(device_map: Any) -> dict[str, Any]:
    raw_devices = []
    if isinstance(device_map, dict):
        raw_devices = list(device_map.get("devices") or [])
    devices = [device for device in raw_devices if isinstance(device, dict)]
    kind_counts: dict[str, int] = {}
    protocols: set[str] = set()
    unknown_count = 0
    local_count = 0
    lan_count = 0
    latest_seen = ""
    for device in devices:
        kind = _safe_text(device.get("kind")) or "unknown"
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind == "unknown":
            unknown_count += 1
        if device.get("ip_addresses"):
            lan_count += 1
        else:
            local_count += 1
        for protocol in device.get("protocols") or []:
            text = _safe_text(protocol)
            if text:
                protocols.add(text)
        seen = _safe_text(device.get("last_seen_at"))
        if seen and seen > latest_seen:
            latest_seen = seen
    return {
        "device_count": len(devices),
        "local_device_count": local_count,
        "lan_device_count": lan_count,
        "unknown_device_count": unknown_count,
        "kind_counts": kind_counts,
        "protocols": sorted(protocols),
        "last_seen_at": latest_seen or _safe_text((device_map or {}).get("generated_at") if isinstance(device_map, dict) else ""),
    }


def _protocols_from_services(services: Any) -> list[str]:
    protocols: list[str] = []
    for service in services or []:
        if not isinstance(service, dict):
            continue
        for value in (service.get("protocol"), service.get("name"), service.get("service_type")):
            text = _safe_text(value)
            if text and text not in protocols:
                protocols.append(text)
    return protocols[:16]


def _merge_devices(devices: list[DiscoveredDevice]) -> list[DiscoveredDevice]:
    merged: dict[str, dict[str, Any]] = {}
    key_by_ip: dict[str, str] = {}
    key_by_mac: dict[str, str] = {}
    for device in devices:
        raw = device.to_dict()
        key = raw["device_id"]
        mac = _safe_text(raw.get("mac_address")).lower()
        if mac and mac in key_by_mac:
            key = key_by_mac[mac]
        else:
            for ip in raw.get("ip_addresses") or []:
                if ip in key_by_ip:
                    key = key_by_ip[ip]
                    break
        current = merged.setdefault(key, raw)
        for ip in raw.get("ip_addresses") or []:
            if ip and ip not in current["ip_addresses"]:
                current["ip_addresses"].append(ip)
            key_by_ip[ip] = key
        if mac:
            current["mac_address"] = current.get("mac_address") or raw.get("mac_address")
            key_by_mac[mac] = key
        for field_name in ("name", "hostname", "vendor"):
            current[field_name] = current.get(field_name) or raw.get(field_name)
        current["protocols"] = _list_text([*current.get("protocols", []), *raw.get("protocols", [])], limit=24)
        current["services"] = _merge_services(current.get("services", []), raw.get("services", []))
        current["evidence"] = _list_text([*current.get("evidence", []), *raw.get("evidence", [])], limit=32)
        current["risk_notes"] = _list_text([*current.get("risk_notes", []), *raw.get("risk_notes", [])], limit=16)
        current["last_seen_at"] = max(_safe_text(current.get("last_seen_at")), _safe_text(raw.get("last_seen_at")))
        current["kind"], current["confidence"] = _fingerprint_kind(current)
        current["controllable"] = False
    out: list[DiscoveredDevice] = []
    for value in merged.values():
        device = DiscoveredDevice.from_dict(value)
        if device is not None:
            out.append(device)
    return out


def _merge_services(left: Any, right: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for service in [*(left or []), *(right or [])]:
        if not isinstance(service, dict):
            continue
        item = dict(service)
        key = (item.get("port"), item.get("protocol"), item.get("name") or item.get("service_type"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:32]


def _fingerprint_kind(device: dict[str, Any]) -> tuple[str, float]:
    haystack = " ".join(
        [
            _safe_text(device.get("name")),
            _safe_text(device.get("hostname")),
            _safe_text(device.get("vendor")),
            " ".join(_safe_text(item) for item in device.get("protocols") or []),
            " ".join(_safe_text(item) for item in device.get("evidence") or []),
            " ".join(_safe_text(service.get("name") or service.get("service_type") or service.get("banner")) for service in device.get("services") or [] if isinstance(service, dict)),
        ]
    ).lower()
    ports = {int(service.get("port")) for service in device.get("services") or [] if isinstance(service, dict) and str(service.get("port") or "").isdigit()}
    rules: list[tuple[str, float, bool]] = [
        ("home_assistant", 0.92, 8123 in ports or "home assistant" in haystack or "homeassistant" in haystack),
        ("mqtt", 0.82, bool({1883, 8883} & ports) or "mqtt" in haystack),
        ("camera", 0.78, 554 in ports or "rtsp" in haystack or "camera" in haystack or "onvif" in haystack),
        ("printer", 0.78, "printer" in haystack or "ipp" in haystack or "airprint" in haystack),
        ("nas", 0.74, 445 in ports and ("nas" in haystack or "samba" in haystack or "synology" in haystack or "qnap" in haystack)),
        ("router", 0.74, 53 in ports and ({80, 443} & ports or "router" in haystack or "gateway" in haystack)),
        ("tv", 0.7, "chromecast" in haystack or "google cast" in haystack or "dlna" in haystack or "roku" in haystack or "tv" in haystack),
        ("speaker", 0.68, "speaker" in haystack or "sonos" in haystack or "airplay" in haystack),
        ("robot", 0.72, "unitree" in haystack or "g1" in haystack or "robot" in haystack),
        ("computer", 0.62, bool({22, 445, 3389} & ports) or "windows" in haystack or "linux" in haystack or "macbook" in haystack),
        ("phone", 0.58, "iphone" in haystack or "android" in haystack or "phone" in haystack),
    ]
    for kind, confidence, matched in rules:
        if matched:
            return kind, confidence
    return _safe_text(device.get("kind")) or "unknown", float(device.get("confidence") or 0.35)


@dataclass(frozen=True)
class LocalAwarenessSummary:
    enabled: bool = False
    device_discovery_enabled: bool = False
    lan_discovery_enabled: bool = False
    camera_enabled: bool = False
    screen_enabled: bool = False
    audio_input_enabled: bool = False
    audio_output_enabled: bool = False
    media_inspection_enabled: bool = False
    media_scan_enabled: bool = False
    transcription_enabled: bool = False
    tts_enabled: bool = False
    last_discovery: dict[str, Any] = field(default_factory=dict)
    last_capture: dict[str, Any] = field(default_factory=dict)
    last_audio: dict[str, Any] = field(default_factory=dict)
    last_media_inspection: dict[str, Any] = field(default_factory=dict)
    last_media_scan: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalAwarenessBackend:
    """Safe, dependency-free backend used by P5B tools.

    Real camera/audio/screen implementations can replace this via loop overrides later.
    Until then, capture writes auditable placeholder media only when the matching
    config gate is explicitly enabled.
    """

    def __init__(self, *, command_runner: Any | None = None) -> None:
        self._command_runner = command_runner or self._run_command

    def discover_local_devices(self, *, config: Any) -> dict[str, Any]:
        hardware = _hardware_config(config)
        if bool(getattr(hardware, "enabled", False)):
            return self._discover_local_devices_real(config=config, hardware=hardware)
        return {
            "status": "ok",
            "generated_at": _utcnow_iso(),
            "local_devices": {
                "cameras": [],
                "microphones": [],
                "speakers": [],
                "screens": [],
            },
            "capabilities": {
                "camera_capture": bool(getattr(getattr(config, "camera", None), "enabled", False)),
                "screen_capture": bool(getattr(getattr(config, "screen", None), "enabled", False)),
                "audio_record": bool(getattr(getattr(config, "audio", None), "input_enabled", False)),
                "audio_output": bool(getattr(getattr(config, "audio", None), "output_enabled", False)),
            },
            "reason": "dependency_free_backend_no_hardware_probe",
        }

    def discover_lan_devices(self, *, config: Any) -> dict[str, Any]:
        if not bool(getattr(config, "lan_discovery_enabled", False)):
            return {
                "status": "disabled",
                "devices": [],
                "reason": "lan_discovery_disabled",
            }
        hardware = _hardware_config(config)
        if bool(getattr(hardware, "enabled", False)):
            return self._discover_lan_devices_real(config=config, hardware=hardware)
        host = socket.gethostname()
        addresses: list[str] = []
        try:
            addresses = sorted({
                item[4][0]
                for item in socket.getaddrinfo(host, None)
                if item and item[4] and item[4][0]
            })
        except OSError:
            addresses = []
        return {
            "status": "ok",
            "generated_at": _utcnow_iso(),
            "devices": [
                {
                    "kind": "local_host",
                    "hostname": host,
                    "addresses": addresses,
                }
            ],
            "method": "local_hostname_only",
        }

    def _discover_local_devices_real(self, *, config: Any, hardware: Any) -> dict[str, Any]:
        generated_at = _utcnow_iso()
        local_devices = {
            "cameras": [],
            "microphones": [],
            "speakers": [],
            "screens": [],
            "usb": [],
            "bluetooth": [],
            "network_adapters": [],
        }
        skipped: list[str] = []
        devices: list[DiscoveredDevice] = []
        if os.name == "nt":
            try:
                local_devices = self._windows_local_hardware()
            except Exception as exc:
                skipped.append(f"windows_hardware_probe_failed:{type(exc).__name__}")
        else:
            skipped.append("local_hardware_probe_only_implemented_for_windows")
        for category, items in local_devices.items():
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                name = _safe_text(item.get("name") or item.get("caption"))
                pnp_id = _safe_text(item.get("pnp_device_id") or item.get("device_id"))
                devices.append(
                    DiscoveredDevice(
                        device_id=_device_id_for("local", category, pnp_id, name),
                        kind=_local_category_kind(category),
                        name=name or category,
                        protocols=["local_pnp"],
                        confidence=0.72 if name else 0.45,
                        evidence=[f"local_hardware:{category}", *_list_text([pnp_id], limit=1)],
                        risk_notes=["discovered_only_not_captured"],
                        last_seen_at=generated_at,
                    )
                )
        device_map = DeviceMap(
            devices=_merge_devices(devices),
            generated_at=generated_at,
            status="partial" if skipped else "ok",
            scan_boundaries={
                "scope": "local_host",
                "active_probe_enabled": False,
                "capture_performed": False,
            },
            skipped_reasons=skipped,
        ).to_dict()
        return {
            "status": "partial" if skipped else "ok",
            "generated_at": generated_at,
            "local_devices": {
                "cameras": list(local_devices.get("cameras") or []),
                "microphones": list(local_devices.get("microphones") or []),
                "speakers": list(local_devices.get("speakers") or []),
                "screens": list(local_devices.get("screens") or []),
            },
            "hardware_devices": local_devices,
            "device_map": device_map,
            "device_count": device_map["device_count"],
            "capabilities": {
                "camera_capture": bool(getattr(getattr(config, "camera", None), "enabled", False)),
                "screen_capture": bool(getattr(getattr(config, "screen", None), "enabled", False)),
                "audio_record": bool(getattr(getattr(config, "audio", None), "input_enabled", False)),
                "audio_output": bool(getattr(getattr(config, "audio", None), "output_enabled", False)),
            },
            "skipped_reasons": skipped,
            "reason": "hardware_discovery_enabled",
        }

    def _discover_lan_devices_real(self, *, config: Any, hardware: Any) -> dict[str, Any]:
        generated_at = _utcnow_iso()
        skipped: list[str] = []
        boundaries = self._scan_boundaries(hardware)
        targets = self._select_scan_targets(hardware, skipped=skipped)
        devices: list[DiscoveredDevice] = []
        devices.extend(self._discover_from_arp(generated_at=generated_at, skipped=skipped))
        devices.extend(self._discover_from_hostname(generated_at=generated_at, skipped=skipped))
        devices.extend(self._discover_from_ssdp(hardware=hardware, generated_at=generated_at, skipped=skipped))
        devices.extend(self._discover_from_mdns(hardware=hardware, generated_at=generated_at, skipped=skipped))
        if bool(getattr(hardware, "active_probe_enabled", True)):
            devices.extend(
                self._active_tcp_probe(
                    targets=targets,
                    hardware=hardware,
                    generated_at=generated_at,
                    skipped=skipped,
                )
            )
        else:
            skipped.append("active_probe_disabled")
        merged = _merge_devices(devices)
        device_map = DeviceMap(
            devices=merged,
            generated_at=generated_at,
            status="partial" if skipped else "ok",
            scan_boundaries={**boundaries, "target_count": len(targets), "targets": targets[:16]},
            skipped_reasons=skipped,
        ).to_dict()
        return {
            "status": "partial" if skipped else "ok",
            "generated_at": generated_at,
            "devices": [device.to_dict() for device in merged],
            "device_map": device_map,
            "device_count": device_map["device_count"],
            "scan_boundaries": device_map["scan_boundaries"],
            "skipped_reasons": skipped,
            "method": "arp_ssdp_mdns_tcp_connect",
        }

    def _windows_local_hardware(self) -> dict[str, list[dict[str, Any]]]:
        script = r"""
$classes = @(
  @{ key = 'cameras'; class = 'Win32_PnPEntity'; filter = "PNPClass='Camera' OR Name LIKE '%camera%' OR Name LIKE '%webcam%'" },
  @{ key = 'microphones'; class = 'Win32_PnPEntity'; filter = "Name LIKE '%microphone%' OR Name LIKE '%mic%'" },
  @{ key = 'speakers'; class = 'Win32_PnPEntity'; filter = "Name LIKE '%speaker%' OR Name LIKE '%audio%'" },
  @{ key = 'screens'; class = 'Win32_DesktopMonitor'; filter = $null },
  @{ key = 'usb'; class = 'Win32_PnPEntity'; filter = "PNPClass='USB' OR DeviceID LIKE 'USB%'" },
  @{ key = 'bluetooth'; class = 'Win32_PnPEntity'; filter = "PNPClass='Bluetooth' OR Name LIKE '%Bluetooth%'" },
  @{ key = 'network_adapters'; class = 'Win32_NetworkAdapter'; filter = "PhysicalAdapter=True" }
)
$out = @{}
foreach ($entry in $classes) {
  $items = if ($entry.filter) { Get-CimInstance -ClassName $entry.class -Filter $entry.filter -ErrorAction SilentlyContinue } else { Get-CimInstance -ClassName $entry.class -ErrorAction SilentlyContinue }
  $out[$entry.key] = @($items | Select-Object Name, Caption, DeviceID, PNPDeviceID, Manufacturer, NetConnectionID, MACAddress)
}
$out | ConvertTo-Json -Depth 5 -Compress
"""
        result = self._command_runner(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=8,
        )
        data = json.loads(result or "{}")
        out: dict[str, list[dict[str, Any]]] = {}
        for key in ("cameras", "microphones", "speakers", "screens", "usb", "bluetooth", "network_adapters"):
            value = data.get(key) if isinstance(data, dict) else []
            if isinstance(value, dict):
                value = [value]
            out[key] = [_normalize_hardware_item(item) for item in value or [] if isinstance(item, dict)]
        return out

    def _discover_from_arp(self, *, generated_at: str, skipped: list[str]) -> list[DiscoveredDevice]:
        try:
            output = self._command_runner(["arp", "-a"], timeout=5)
        except Exception as exc:
            skipped.append(f"arp_failed:{type(exc).__name__}")
            return []
        devices: list[DiscoveredDevice] = []
        for match in re.finditer(r"(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s+(?P<mac>(?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})", output or ""):
            ip = _private_ipv4(match.group("ip"))
            if not ip:
                continue
            mac = match.group("mac").replace("-", ":").lower()
            devices.append(
                DiscoveredDevice(
                    device_id=_device_id_for(mac, ip),
                    kind="unknown",
                    ip_addresses=[ip],
                    mac_address=mac,
                    protocols=["arp"],
                    confidence=0.45,
                    last_seen_at=generated_at,
                    evidence=["arp_table"],
                )
            )
        return devices

    def _discover_from_hostname(self, *, generated_at: str, skipped: list[str]) -> list[DiscoveredDevice]:
        host = socket.gethostname()
        addresses: list[str] = []
        try:
            addresses = sorted({
                _private_ipv4(item[4][0]) or ""
                for item in socket.getaddrinfo(host, None)
                if item and item[4] and item[4][0]
            })
        except OSError as exc:
            skipped.append(f"hostname_resolution_failed:{type(exc).__name__}")
        addresses = [item for item in addresses if item]
        if not addresses:
            return []
        return [
            DiscoveredDevice(
                device_id=_device_id_for("local_host", host, ",".join(addresses)),
                kind="computer",
                name=host,
                hostname=host,
                ip_addresses=addresses,
                protocols=["hostname"],
                confidence=0.62,
                last_seen_at=generated_at,
                evidence=["local_hostname"],
            )
        ]

    def _discover_from_ssdp(self, *, hardware: Any, generated_at: str, skipped: list[str]) -> list[DiscoveredDevice]:
        timeout = max(0.05, int(getattr(hardware, "timeout_ms", 500) or 500) / 1000)
        request = "\r\n".join(
            [
                "M-SEARCH * HTTP/1.1",
                "HOST: 239.255.255.250:1900",
                'MAN: "ssdp:discover"',
                "MX: 1",
                "ST: ssdp:all",
                "",
                "",
            ]
        ).encode("ascii")
        responses: list[bytes] = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
                sock.settimeout(timeout)
                sock.sendto(request, ("239.255.255.250", 1900))
                while True:
                    try:
                        data, _addr = sock.recvfrom(4096)
                    except socket.timeout:
                        break
                    responses.append(data)
        except OSError as exc:
            skipped.append(f"ssdp_failed:{type(exc).__name__}")
            return []
        devices: list[DiscoveredDevice] = []
        for data in responses[:64]:
            text = data.decode("utf-8", errors="ignore")
            headers = _parse_httpu_headers(text)
            location = _safe_text(headers.get("location"))
            service_type = _safe_text(headers.get("st") or headers.get("nt") or headers.get("usn"))
            ips = [_private_ipv4(value) for value in _IPV4_RE.findall(location)]
            ips = [ip for ip in ips if ip]
            devices.append(
                DiscoveredDevice(
                    device_id=_device_id_for("ssdp", headers.get("usn"), location, service_type),
                    kind="unknown",
                    name=_safe_text(headers.get("server") or service_type) or "SSDP device",
                    ip_addresses=ips,
                    protocols=["ssdp"],
                    services=[{"protocol": "ssdp", "port": 1900, "service_type": service_type, "location": location}],
                    confidence=0.55,
                    last_seen_at=generated_at,
                    evidence=_list_text([service_type, headers.get("server"), location], limit=4),
                )
            )
        return devices

    def _discover_from_mdns(self, *, hardware: Any, generated_at: str, skipped: list[str]) -> list[DiscoveredDevice]:
        timeout = max(0.05, int(getattr(hardware, "timeout_ms", 500) or 500) / 1000)
        query = _build_mdns_query("_services._dns-sd._udp.local")
        responses: list[bytes] = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
                sock.settimeout(timeout)
                sock.sendto(query, ("224.0.0.251", 5353))
                while True:
                    try:
                        data, _addr = sock.recvfrom(4096)
                    except socket.timeout:
                        break
                    responses.append(data)
        except OSError as exc:
            skipped.append(f"mdns_failed:{type(exc).__name__}")
            return []
        devices: list[DiscoveredDevice] = []
        for data in responses[:64]:
            names = _parse_mdns_names(data)
            service_names = [name for name in names if ".local" in name.lower()]
            for name in service_names[:12]:
                devices.append(
                    DiscoveredDevice(
                        device_id=_device_id_for("mdns", name),
                        kind="unknown",
                        name=name,
                        protocols=["mdns"],
                        services=[{"protocol": "mdns", "port": 5353, "service_type": name}],
                        confidence=0.5,
                        last_seen_at=generated_at,
                        evidence=[name],
                    )
                )
        return devices

    def _active_tcp_probe(
        self,
        *,
        targets: list[str],
        hardware: Any,
        generated_at: str,
        skipped: list[str],
    ) -> list[DiscoveredDevice]:
        ports = _service_ports(hardware)
        timeout = max(0.05, int(getattr(hardware, "timeout_ms", 500) or 500) / 1000)
        max_workers = max(1, min(128, int(getattr(hardware, "max_concurrency", 64) or 64)))
        if not targets:
            skipped.append("no_private_scan_targets")
            return []
        results: list[tuple[str, dict[str, Any]]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._probe_tcp_service, ip, port, timeout)
                for ip in targets
                for port in ports
            ]
            for future in concurrent.futures.as_completed(futures):
                service = future.result()
                if service:
                    results.append((str(service.pop("ip")), service))
        by_ip: dict[str, list[dict[str, Any]]] = {}
        for ip, service in results:
            by_ip.setdefault(ip, []).append(service)
        devices: list[DiscoveredDevice] = []
        for ip, services in by_ip.items():
            device = {
                "ip_addresses": [ip],
                "services": services,
                "protocols": _protocols_from_services(services) or ["tcp_connect"],
                "evidence": [f"tcp:{service.get('port')} open" for service in services],
            }
            kind, confidence = _fingerprint_kind(device)
            devices.append(
                DiscoveredDevice(
                    device_id=_device_id_for("tcp", ip),
                    kind=kind,
                    ip_addresses=[ip],
                    protocols=device["protocols"],
                    services=services,
                    confidence=max(confidence, 0.5),
                    last_seen_at=generated_at,
                    evidence=device["evidence"],
                    risk_notes=["tcp_connect_only_no_login_no_payload_execution"],
                )
            )
        return devices

    def _probe_tcp_service(self, ip: str, port: int, timeout: float) -> dict[str, Any] | None:
        try:
            with socket.create_connection((ip, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                service: dict[str, Any] = {"ip": ip, "port": port, "protocol": _port_protocol(port), "name": _port_protocol(port)}
                if port in {80, 443, 8008, 8080, 8123, 9000}:
                    service.update(_probe_http_title(sock=sock, host=ip, port=port))
                return service
        except OSError:
            return None

    def _select_scan_targets(self, hardware: Any, *, skipped: list[str]) -> list[str]:
        cidrs = _list_text(getattr(hardware, "allowed_cidrs", None), limit=16)
        if not cidrs:
            cidrs = self._auto_private_cidrs(skipped=skipped)
        max_hosts = max(1, int(getattr(hardware, "max_hosts", 256) or 256))
        targets: list[str] = []
        for cidr in cidrs:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                skipped.append(f"invalid_cidr:{cidr}")
                continue
            if not isinstance(network, ipaddress.IPv4Network):
                skipped.append(f"non_ipv4_cidr_skipped:{cidr}")
                continue
            if not network.is_private:
                skipped.append(f"public_cidr_skipped:{cidr}")
                continue
            for host in network.hosts():
                ip = _private_ipv4(host)
                if not ip:
                    continue
                targets.append(ip)
                if len(targets) >= max_hosts:
                    return targets
        return targets

    def _auto_private_cidrs(self, *, skipped: list[str]) -> list[str]:
        addresses: set[str] = set()
        try:
            host = socket.gethostname()
            for item in socket.getaddrinfo(host, None):
                if item and item[4] and item[4][0]:
                    ip = _private_ipv4(item[4][0])
                    if ip:
                        addresses.add(ip)
        except OSError as exc:
            skipped.append(f"interface_resolution_failed:{type(exc).__name__}")
        cidrs: list[str] = []
        for ip in sorted(addresses):
            interface_name = self._interface_name_for_ip(ip)
            if _is_virtual_interface(interface_name):
                skipped.append(f"virtual_interface_skipped:{interface_name or ip}")
                continue
            cidrs.append(f"{ip}/24")
        return cidrs

    def _interface_name_for_ip(self, ip: str) -> str:
        if os.name != "nt":
            return ""
        try:
            result = self._command_runner(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"Get-NetIPAddress -IPAddress '{ip}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty InterfaceAlias",
                ],
                timeout=4,
            )
        except Exception:
            return ""
        return _safe_text(result)

    def _scan_boundaries(self, hardware: Any) -> dict[str, Any]:
        return {
            "scope": "private_ipv4_only",
            "posture": _safe_text(getattr(hardware, "posture", "strong")) or "strong",
            "allowed_cidrs": _list_text(getattr(hardware, "allowed_cidrs", None)),
            "max_hosts": int(getattr(hardware, "max_hosts", 256) or 256),
            "max_concurrency": int(getattr(hardware, "max_concurrency", 64) or 64),
            "timeout_ms": int(getattr(hardware, "timeout_ms", 500) or 500),
            "active_probe_enabled": bool(getattr(hardware, "active_probe_enabled", True)),
            "service_ports": _service_ports(hardware),
            "no_login": True,
            "no_control": True,
        }

    @staticmethod
    def _run_command(command: list[str], *, timeout: int = 8) -> str:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(_safe_text(completed.stderr or completed.stdout or completed.returncode))
        return completed.stdout

    def capture_camera_frame(
        self,
        *,
        workspace: Path,
        save_dir: str,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        return self._write_placeholder_image(
            workspace=workspace,
            save_dir=save_dir,
            prefix="camera",
            source="local_awareness.camera",
            device_id=device_id,
        )

    def capture_screen(
        self,
        *,
        workspace: Path,
        save_dir: str,
        screen_id: str | None = None,
    ) -> dict[str, Any]:
        return self._write_placeholder_image(
            workspace=workspace,
            save_dir=save_dir,
            prefix="screen",
            source="local_awareness.screen",
            device_id=screen_id,
        )

    def record_audio_sample(
        self,
        *,
        workspace: Path,
        save_dir: str,
        seconds: int,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        dest_dir = self._safe_output_dir(workspace=workspace, save_dir=save_dir)
        filename = f"audio_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}.wav"
        dest = dest_dir / filename
        # Minimal RIFF/WAVE header with no samples. This is an auditable placeholder,
        # not a real microphone capture.
        dest.write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt "
            b"\x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        return {
            "status": "ok",
            "media_path": _workspace_rel(dest, workspace=workspace),
            "absolute_path": str(dest),
            "kind": "audio",
            "source": "local_awareness.audio",
            "device_id": _safe_text(device_id),
            "seconds": int(seconds),
            "captured_at": _utcnow_iso(),
            "placeholder": True,
        }

    def speak_text(self, *, text: str, voice: str | None = None) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "reason": "audio_output_backend_not_connected",
            "text_preview": _safe_text(text),
            "voice": _safe_text(voice),
            "is_real_output": False,
            "backend_kind": "local_awareness_placeholder",
        }

    def _write_placeholder_image(
        self,
        *,
        workspace: Path,
        save_dir: str,
        prefix: str,
        source: str,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        dest_dir = self._safe_output_dir(workspace=workspace, save_dir=save_dir)
        filename = f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        dest = dest_dir / filename
        dest.write_bytes(_PNG_1X1_TRANSPARENT)
        return {
            "status": "ok",
            "media_path": _workspace_rel(dest, workspace=workspace),
            "absolute_path": str(dest),
            "kind": "image",
            "source": source,
            "device_id": _safe_text(device_id),
            "captured_at": _utcnow_iso(),
            "placeholder": True,
        }

    @staticmethod
    def _safe_output_dir(*, workspace: Path, save_dir: str) -> Path:
        workspace = Path(workspace).resolve()
        relative = str(save_dir or "uploads/perception").strip().replace("\\", "/")
        relative = relative.lstrip("/")
        dest = (workspace / relative).resolve()
        if workspace not in dest.parents and dest != workspace:
            raise ValueError("save_dir must stay inside workspace")
        dest.mkdir(parents=True, exist_ok=True)
        return dest


def _hardware_config(config: Any) -> Any:
    return getattr(config, "hardware_discovery", None) or getattr(config, "hardwareDiscovery", None) or object()


def _service_ports(hardware: Any) -> list[int]:
    raw = getattr(hardware, "service_ports", None) or DEFAULT_SERVICE_PORTS
    ports: list[int] = []
    for value in raw:
        try:
            port = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports or list(DEFAULT_SERVICE_PORTS)


def _normalize_hardware_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _safe_text(raw.get("Name") or raw.get("name") or raw.get("Caption") or raw.get("caption")),
        "caption": _safe_text(raw.get("Caption") or raw.get("caption")),
        "device_id": _safe_text(raw.get("DeviceID") or raw.get("device_id")),
        "pnp_device_id": _safe_text(raw.get("PNPDeviceID") or raw.get("pnp_device_id")),
        "manufacturer": _safe_text(raw.get("Manufacturer") or raw.get("manufacturer")),
        "net_connection_id": _safe_text(raw.get("NetConnectionID") or raw.get("net_connection_id")),
        "mac_address": _safe_text(raw.get("MACAddress") or raw.get("mac_address")),
    }


def _local_category_kind(category: str) -> str:
    return {
        "cameras": "camera",
        "microphones": "microphone",
        "speakers": "speaker",
        "screens": "display",
        "network_adapters": "network_adapter",
    }.get(category, "local_device")


def _is_virtual_interface(name: str) -> bool:
    text = _safe_text(name).lower()
    if not text:
        return False
    markers = (
        "loopback",
        "docker",
        "bridge",
        "vmware",
        "virtualbox",
        "hyper-v",
        "hyper v",
        "wsl",
        "tailscale",
        "zerotier",
        "npcap",
        "bluetooth",
    )
    return any(marker in text for marker in markers)


def _port_protocol(port: int) -> str:
    return {
        22: "ssh",
        53: "dns",
        80: "http",
        443: "https",
        445: "smb",
        554: "rtsp",
        1883: "mqtt",
        1900: "ssdp",
        5353: "mdns",
        5683: "coap",
        8008: "http",
        8080: "http",
        8123: "home_assistant",
        8883: "mqtt_tls",
        9000: "http",
    }.get(int(port), "tcp")


def _parse_httpu_headers(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def _probe_http_title(*, sock: socket.socket, host: str, port: int) -> dict[str, Any]:
    try:
        request = f"HEAD / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: OriginAgent-Discovery\r\nConnection: close\r\n\r\n"
        sock.sendall(request.encode("ascii", errors="ignore"))
        data = sock.recv(2048)
    except OSError:
        return {"probe": "tcp_connect_only"}
    text = data.decode("utf-8", errors="ignore")
    headers = _parse_httpu_headers(text)
    evidence = []
    if headers.get("server"):
        evidence.append(f"server:{_safe_text(headers['server'], max_chars=120)}")
    title = ""
    if not headers:
        try:
            request = f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: OriginAgent-Discovery\r\nConnection: close\r\n\r\n"
            sock.sendall(request.encode("ascii", errors="ignore"))
            body = sock.recv(4096).decode("utf-8", errors="ignore")
            match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
            if match:
                title = " ".join(match.group(1).split())
        except OSError:
            title = ""
    if title:
        evidence.append(f"title:{_safe_text(title, max_chars=120)}")
    return {
        "probe": "http_minimal",
        "server": _safe_text(headers.get("server")),
        "title": _safe_text(title),
        "evidence": evidence,
        "name": _port_protocol(port),
    }


def _build_mdns_query(name: str) -> bytes:
    labels = [label for label in name.strip(".").split(".") if label]
    payload = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for label in labels:
        encoded = label.encode("utf-8")[:63]
        payload += bytes([len(encoded)]) + encoded
    # PTR IN
    return payload + b"\x00\x00\x0c\x00\x01"


def _parse_mdns_names(data: bytes) -> list[str]:
    names: list[str] = []

    def read_name(offset: int, depth: int = 0) -> tuple[str, int]:
        if depth > 8 or offset >= len(data):
            return "", offset
        labels: list[str] = []
        original = offset
        jumped = False
        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if length & 0xC0 == 0xC0 and offset + 1 < len(data):
                pointer = ((length & 0x3F) << 8) | data[offset + 1]
                label, _ = read_name(pointer, depth + 1)
                if label:
                    labels.append(label)
                offset += 2
                jumped = True
                break
            offset += 1
            label_bytes = data[offset: offset + length]
            try:
                labels.append(label_bytes.decode("utf-8", errors="ignore"))
            except Exception:
                pass
            offset += length
        return ".".join(label for label in labels if label), (original + 2 if jumped else offset)

    try:
        if len(data) < 12:
            return []
        qd = int.from_bytes(data[4:6], "big")
        an = int.from_bytes(data[6:8], "big")
        ns = int.from_bytes(data[8:10], "big")
        ar = int.from_bytes(data[10:12], "big")
        offset = 12
        for _ in range(qd):
            _name, offset = read_name(offset)
            offset += 4
        for _ in range(an + ns + ar):
            name, offset = read_name(offset)
            if name:
                names.append(name)
            if offset + 10 > len(data):
                break
            rtype = int.from_bytes(data[offset: offset + 2], "big")
            offset += 8
            rdlen = int.from_bytes(data[offset: offset + 2], "big")
            offset += 2
            if rtype in {12, 33}:
                target_offset = offset + (6 if rtype == 33 and rdlen >= 6 else 0)
                target, _ = read_name(target_offset)
                if target:
                    names.append(target)
            offset += rdlen
    except Exception:
        return names
    return _list_text(names, limit=64, max_chars=200)


def normalize_local_awareness_summary(
    config: Any | None,
    *,
    backend: Any | None = None,
    cached: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a stable local-awareness observability shape."""

    raw = dict(cached or {})
    camera = getattr(config, "camera", None)
    screen = getattr(config, "screen", None)
    audio = getattr(config, "audio", None)
    media_inspection = getattr(config, "media_inspection", None)
    media = getattr(config, "media", None)
    def _flag(config_value: bool, cached_key: str) -> bool:
        if config is None:
            return bool(raw.get(cached_key, False))
        return bool(config_value)

    device_map = raw.get("device_map")
    if not isinstance(device_map, dict):
        discovery = raw.get("last_discovery")
        if isinstance(discovery, dict) and isinstance(discovery.get("device_map"), dict):
            device_map = discovery.get("device_map")
    raw_device_map_summary = raw.get("device_map_summary")
    device_map_summary = (
        summarize_device_map(device_map)
        if isinstance(device_map, dict)
        else dict(raw_device_map_summary)
        if isinstance(raw_device_map_summary, dict)
        else {}
    )
    summary = LocalAwarenessSummary(
        enabled=_flag(bool(getattr(config, "enabled", False)), "enabled"),
        device_discovery_enabled=_flag(
            bool(getattr(config, "device_discovery_enabled", False)),
            "device_discovery_enabled",
        ),
        lan_discovery_enabled=_flag(
            bool(getattr(config, "lan_discovery_enabled", False)),
            "lan_discovery_enabled",
        ),
        camera_enabled=_flag(bool(getattr(camera, "enabled", False)), "camera_enabled"),
        screen_enabled=_flag(bool(getattr(screen, "enabled", False)), "screen_enabled"),
        audio_input_enabled=_flag(bool(getattr(audio, "input_enabled", False)), "audio_input_enabled"),
        audio_output_enabled=_flag(bool(getattr(audio, "output_enabled", False)), "audio_output_enabled"),
        media_inspection_enabled=_flag(
            bool(getattr(media_inspection, "enabled", False)),
            "media_inspection_enabled",
        ),
        media_scan_enabled=_flag(bool(getattr(media, "enabled", False)), "media_scan_enabled"),
        transcription_enabled=_flag(bool(getattr(audio, "transcription_enabled", False)), "transcription_enabled"),
        tts_enabled=_flag(bool(getattr(audio, "tts_enabled", False)), "tts_enabled"),
        last_discovery=dict(raw.get("last_discovery") or {}),
        last_capture=dict(raw.get("last_capture") or {}),
        last_audio=dict(raw.get("last_audio") or {}),
        last_media_inspection=dict(raw.get("last_media_inspection") or {}),
        last_media_scan=dict(raw.get("last_media_scan") or {}),
        updated_at=str(raw.get("updated_at") or "") or None,
    ).to_dict()
    summary["backend_kind"] = (
        getattr(backend, "__class__", type(backend)).__name__
        if backend is not None
        else raw.get("backend_kind")
    )
    if isinstance(device_map, dict):
        summary["device_map"] = dict(device_map)
    if device_map_summary:
        summary["device_map_summary"] = device_map_summary
        summary["device_count"] = int(device_map_summary.get("device_count", 0) or 0)
    for key in ("device_events_summary", "device_bindings_summary", "device_permissions_summary"):
        value = raw.get(key)
        if isinstance(value, dict):
            summary[key] = dict(value)
    for key in ("media_queue_summary", "recent_media_events", "last_scene_inspection", "audio_status"):
        value = raw.get(key)
        if isinstance(value, dict):
            summary[key] = dict(value)
        elif isinstance(value, list):
            summary[key] = list(value)
    events_summary = summary.get("device_events_summary") if isinstance(summary.get("device_events_summary"), dict) else {}
    bindings_summary = summary.get("device_bindings_summary") if isinstance(summary.get("device_bindings_summary"), dict) else {}
    permissions_summary = summary.get("device_permissions_summary") if isinstance(summary.get("device_permissions_summary"), dict) else {}
    summary["device_event_count"] = int(
        events_summary.get("device_event_count", raw.get("device_event_count", 0)) or 0
    )
    summary["recent_device_events"] = list(
        events_summary.get("recent_device_events") or raw.get("recent_device_events") or []
    )
    summary["bound_device_count"] = int(
        bindings_summary.get("bound_device_count", raw.get("bound_device_count", 0)) or 0
    )
    summary["granted_permission_count"] = int(
        permissions_summary.get("granted_permission_count", raw.get("granted_permission_count", 0)) or 0
    )
    summary["pending_permission_count"] = int(
        permissions_summary.get("pending_permission_count", raw.get("pending_permission_count", 0)) or 0
    )
    media_queue = summary.get("media_queue_summary") if isinstance(summary.get("media_queue_summary"), dict) else {}
    summary["media_count"] = int(media_queue.get("media_count", raw.get("media_count", 0)) or 0)
    summary["uninspected_media_count"] = int(media_queue.get("uninspected_count", raw.get("uninspected_media_count", 0)) or 0)
    recent_media_events = summary.get("recent_media_events") if isinstance(summary.get("recent_media_events"), list) else []
    summary["media_event_count"] = int(raw.get("media_event_count", len(recent_media_events)) or len(recent_media_events))
    return summary

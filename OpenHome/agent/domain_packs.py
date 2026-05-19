"""Domain pack discovery and prompt summaries."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


BUILTIN_DOMAIN_PACKS_DIR = Path(__file__).parent.parent / "domain_packs"
_DOMAIN_ID_RE = re.compile(r"^[a-z0-9_-]+$")

DomainPackStatus = Literal["available", "unavailable", "invalid"]


@dataclass(frozen=True)
class DomainPackRequires:
    bins: tuple[str, ...] = ()
    env: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPack:
    id: str
    name: str
    version: str
    path: Path
    source: str
    status: DomainPackStatus
    active: bool = False
    description: str = ""
    capabilities: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    requires: DomainPackRequires = field(default_factory=DomainPackRequires)
    unavailable_reason: str = ""
    capabilities_path: Path | None = None
    capabilities_content: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class DomainPackRuntimeConfig:
    enabled: bool = True
    disabled: tuple[str, ...] = ()
    active: tuple[str, ...] = ()
    max_capability_chars: int = 4000

    @classmethod
    def from_config(cls, config: Any | None) -> "DomainPackRuntimeConfig":
        if config is None:
            return cls()
        max_capability_chars = getattr(config, "max_capability_chars", 4000)
        if max_capability_chars is None:
            max_capability_chars = 4000
        return cls(
            enabled=bool(getattr(config, "enabled", True)),
            disabled=tuple(str(item) for item in getattr(config, "disabled", []) or []),
            active=tuple(str(item) for item in getattr(config, "active", []) or []),
            max_capability_chars=int(max_capability_chars),
        )


class DomainPackManager:
    """Discover local domain packs and render their agent-facing capability state."""

    def __init__(
        self,
        workspace: Path,
        *,
        config: Any | None = None,
        builtin_dir: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.workspace_dir = self.workspace / "domain_packs"
        self.builtin_dir = builtin_dir or BUILTIN_DOMAIN_PACKS_DIR
        self.config = DomainPackRuntimeConfig.from_config(config)
        self._packs: dict[str, DomainPack] | None = None

    def list_packs(self) -> list[DomainPack]:
        if not self.config.enabled:
            return []
        if self._packs is None:
            self._packs = self._discover()
        return sorted(self._packs.values(), key=lambda pack: (pack.id, pack.source))

    def get_pack(self, pack_id: str) -> DomainPack | None:
        if not self.config.enabled:
            return None
        normalized = str(pack_id).strip()
        if not normalized:
            return None
        if self._packs is None:
            self._packs = self._discover()
        return self._packs.get(normalized)

    def build_summary(self) -> str:
        packs = self.list_packs()
        if not packs:
            return ""
        lines = ["# Domain Packs", ""]
        for pack in packs:
            marker = "active" if pack.active else pack.status
            reason = f"; {pack.unavailable_reason}" if pack.unavailable_reason else ""
            desc = f" — {pack.description}" if pack.description else ""
            lines.append(
                f"- `{pack.id}` [{pack.source}] "
                f"{pack.name} v{pack.version}: {marker}{reason}{desc}"
            )
            if pack.capabilities:
                lines.append(f"  Capabilities: {', '.join(pack.capabilities)}")
        return "\n".join(lines)

    def build_active_context(self) -> str:
        parts: list[str] = []
        limit = max(0, self.config.max_capability_chars)
        for pack in self.list_packs():
            if not pack.active:
                continue
            content = pack.capabilities_content.strip()
            if not content:
                continue
            if limit and len(content) > limit:
                content = content[:limit].rstrip() + "\n\n[Domain capabilities truncated]"
            parts.append(f"## Domain Pack: {pack.id}\n\n{content}")
        return "\n\n---\n\n".join(parts)

    def _discover(self) -> dict[str, DomainPack]:
        packs: dict[str, DomainPack] = {}
        # Built-ins are loaded first so workspace packs with the same id override them.
        for source, root in (("builtin", self.builtin_dir), ("workspace", self.workspace_dir)):
            for pack_dir in self._pack_dirs(root):
                pack = self._load_pack(pack_dir, source)
                packs[pack.id] = pack
        return packs

    @staticmethod
    def _pack_dirs(root: Path | None) -> list[Path]:
        if root is None or not root.exists() or not root.is_dir():
            return []
        return sorted([path for path in root.iterdir() if path.is_dir()], key=lambda path: path.name)

    def _load_pack(self, pack_dir: Path, source: str) -> DomainPack:
        manifest_path = pack_dir / "domain_pack.yaml"
        if not manifest_path.exists():
            return self._invalid(pack_dir.name, pack_dir, source, "missing domain_pack.yaml")

        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return self._invalid(pack_dir.name, pack_dir, source, f"invalid domain_pack.yaml: {exc}")

        if not isinstance(raw, dict):
            return self._invalid(pack_dir.name, pack_dir, source, "domain_pack.yaml must be a mapping")

        pack_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        version = str(raw.get("version") or "").strip()
        if not pack_id:
            return self._invalid(pack_dir.name, pack_dir, source, "missing required field: id", raw)
        if not _DOMAIN_ID_RE.fullmatch(pack_id):
            return self._invalid(pack_id, pack_dir, source, "id must match ^[a-z0-9_-]+$", raw)
        missing = [field_name for field_name, value in (("name", name), ("version", version)) if not value]
        if missing:
            return self._invalid(pack_id, pack_dir, source, "missing required field(s): " + ", ".join(missing), raw)

        capabilities_path = pack_dir / "CAPABILITIES.md"
        capabilities_content = ""
        status: DomainPackStatus = "available"
        reason = ""

        if not capabilities_path.exists():
            status = "unavailable"
            reason = "missing CAPABILITIES.md"
        else:
            try:
                capabilities_content = capabilities_path.read_text(encoding="utf-8")
            except OSError as exc:
                status = "unavailable"
                reason = f"cannot read CAPABILITIES.md: {exc}"

        if raw.get("enabled") is False:
            status = "unavailable"
            reason = "disabled by manifest"

        disabled = set(self.config.disabled)
        if pack_id in disabled:
            status = "unavailable"
            reason = "disabled by config"

        requires = self._parse_requires(raw.get("requires"))
        if status == "available":
            missing_requirements = self._missing_requirements(requires)
            if missing_requirements:
                status = "unavailable"
                reason = "missing requirement(s): " + ", ".join(missing_requirements)

        active = pack_id in set(self.config.active) and status == "available"
        return DomainPack(
            id=pack_id,
            name=name,
            version=version,
            path=pack_dir,
            source=source,
            status=status,
            active=active,
            description=str(raw.get("description") or "").strip(),
            capabilities=tuple(_string_list(raw.get("capabilities"))),
            triggers=tuple(_string_list(_activation_triggers(raw.get("activation")))),
            requires=requires,
            unavailable_reason=reason,
            capabilities_path=capabilities_path,
            capabilities_content=capabilities_content,
            manifest=raw,
        )

    @staticmethod
    def _parse_requires(raw: Any) -> DomainPackRequires:
        if not isinstance(raw, dict):
            return DomainPackRequires()
        return DomainPackRequires(
            bins=tuple(_string_list(raw.get("bins"))),
            env=tuple(_string_list(raw.get("env"))),
        )

    @staticmethod
    def _missing_requirements(requires: DomainPackRequires) -> list[str]:
        missing: list[str] = []
        missing.extend(f"CLI: {command}" for command in requires.bins if not shutil.which(command))
        missing.extend(f"ENV: {name}" for name in requires.env if not os.environ.get(name))
        return missing

    @staticmethod
    def _invalid(
        pack_id: str,
        pack_dir: Path,
        source: str,
        reason: str,
        manifest: dict[str, Any] | None = None,
    ) -> DomainPack:
        safe_id = pack_id if pack_id and _DOMAIN_ID_RE.fullmatch(pack_id) else pack_dir.name
        return DomainPack(
            id=safe_id,
            name=safe_id,
            version="",
            path=pack_dir,
            source=source,
            status="invalid",
            unavailable_reason=reason,
            manifest=manifest or {},
        )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float, bool))]


def _activation_triggers(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    return value.get("triggers")

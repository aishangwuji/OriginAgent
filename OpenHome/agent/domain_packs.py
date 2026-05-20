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
_TOOL_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_TOOL_PERMISSIONS = frozenset(
    {
        "read_files",
        "write_files",
        "exec",
        "send_cross_target",
        "create_cron",
        "spawn",
        "mcp:read",
    }
)

DomainPackStatus = Literal["available", "unavailable", "invalid"]
DomainDeclarationStatus = Literal["available", "skipped"]
DomainToolRuntimeStatus = Literal["registered", "skipped"]


@dataclass(frozen=True)
class DomainPackRequires:
    bins: tuple[str, ...] = ()
    env: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainSkillDeclaration:
    id: str
    virtual_id: str
    path: Path | None = None
    status: DomainDeclarationStatus = "available"
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class DomainToolDeclaration:
    id: str
    module: str
    class_name: str
    permissions: tuple[str, ...] = ()
    audit: Literal["minimal", "security"] = "minimal"
    module_path: Path | None = None
    status: DomainDeclarationStatus = "available"
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class DomainToolRuntimeRecord:
    pack_id: str
    tool_id: str
    status: DomainToolRuntimeStatus
    reason: str = ""


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
    skills: tuple[DomainSkillDeclaration, ...] = ()
    tools: tuple[DomainToolDeclaration, ...] = ()
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
        self._domain_tool_runtime: dict[tuple[str, str], DomainToolRuntimeRecord] = {}

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
            if pack.active:
                skill_summary = _declaration_summary(pack.skills)
                tool_summary = _declaration_summary(pack.tools)
                if skill_summary:
                    lines.append(f"  Skills: {skill_summary}")
                if tool_summary:
                    lines.append(f"  Tools: {tool_summary}")
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

    def active_skill_entries(self) -> list[dict[str, str]]:
        """Return SkillsLoader-compatible entries for active domain pack skills."""
        entries: list[dict[str, str]] = []
        for pack in self.list_packs():
            if not pack.active:
                continue
            for skill in pack.skills:
                if not skill.available or skill.path is None:
                    continue
                entries.append(
                    {
                        "name": skill.virtual_id,
                        "path": str(skill.path),
                        "source": f"domain:{pack.id}",
                    }
                )
        return entries

    def get_active_skill_path(self, pack_id: str, skill_id: str) -> Path | None:
        pack = self.get_pack(pack_id)
        if pack is None or not pack.active:
            return None
        for skill in pack.skills:
            if skill.id == skill_id and skill.available:
                return skill.path
        return None

    def active_tool_declarations(self) -> list[tuple[DomainPack, DomainToolDeclaration]]:
        """Return tool declarations that active domain packs may try to register."""
        pairs: list[tuple[DomainPack, DomainToolDeclaration]] = []
        for pack in self.list_packs():
            if not pack.active:
                continue
            pairs.extend((pack, tool) for tool in pack.tools)
        return pairs

    def clear_domain_tool_runtime(self) -> None:
        self._domain_tool_runtime.clear()

    def record_domain_tool_runtime(
        self,
        pack_id: str,
        tool_id: str,
        status: DomainToolRuntimeStatus,
        reason: str = "",
    ) -> None:
        self._domain_tool_runtime[(pack_id, tool_id)] = DomainToolRuntimeRecord(
            pack_id=pack_id,
            tool_id=tool_id,
            status=status,
            reason=reason,
        )

    def domain_tool_runtime_records(self, pack_id: str | None = None) -> list[DomainToolRuntimeRecord]:
        records = list(self._domain_tool_runtime.values())
        if pack_id is not None:
            records = [record for record in records if record.pack_id == pack_id]
        return sorted(records, key=lambda record: (record.pack_id, record.tool_id))

    def domain_tool_runtime_counts(self) -> dict[str, int]:
        counts = {"registered": 0, "skipped": 0}
        for record in self._domain_tool_runtime.values():
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts

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
            skills=tuple(self._parse_skills(raw.get("skills"), pack_dir, pack_id)),
            tools=tuple(self._parse_tools(raw.get("tools"), pack_dir, pack_id)),
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

    def _parse_skills(
        self,
        raw: Any,
        pack_dir: Path,
        pack_id: str,
    ) -> list[DomainSkillDeclaration]:
        declarations: list[DomainSkillDeclaration] = []
        if raw is None:
            return declarations
        if not isinstance(raw, list):
            return [
                DomainSkillDeclaration(
                    id="skills",
                    virtual_id="",
                    status="skipped",
                    unavailable_reason="skills must be a list",
                )
            ]
        for index, item in enumerate(raw):
            skill_id = ""
            if isinstance(item, str):
                skill_id = item.strip()
            elif isinstance(item, dict):
                skill_id = str(item.get("id") or item.get("name") or "").strip()
            label = skill_id or f"skill[{index}]"
            virtual_id = f"domain:{pack_id}/{skill_id}" if skill_id else ""
            if not skill_id or not _DOMAIN_ID_RE.fullmatch(skill_id):
                declarations.append(
                    DomainSkillDeclaration(
                        id=label,
                        virtual_id=virtual_id,
                        status="skipped",
                        unavailable_reason="skill id must match ^[a-z0-9_-]+$",
                    )
                )
                continue
            skill_path = pack_dir / "skills" / skill_id / "SKILL.md"
            if not skill_path.exists():
                declarations.append(
                    DomainSkillDeclaration(
                        id=skill_id,
                        virtual_id=virtual_id,
                        path=skill_path,
                        status="skipped",
                        unavailable_reason="missing SKILL.md",
                    )
                )
                continue
            declarations.append(
                DomainSkillDeclaration(id=skill_id, virtual_id=virtual_id, path=skill_path)
            )
        return declarations

    def _parse_tools(
        self,
        raw: Any,
        pack_dir: Path,
        pack_id: str,
    ) -> list[DomainToolDeclaration]:
        declarations: list[DomainToolDeclaration] = []
        if raw is None:
            return declarations
        if not isinstance(raw, list):
            return [
                DomainToolDeclaration(
                    id="tools",
                    module="",
                    class_name="",
                    status="skipped",
                    unavailable_reason="tools must be a list",
                )
            ]
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                declarations.append(
                    DomainToolDeclaration(
                        id=f"tool[{index}]",
                        module="",
                        class_name="",
                        status="skipped",
                        unavailable_reason="tool declaration must be a mapping",
                    )
                )
                continue
            declarations.append(self._parse_tool(item, pack_dir, pack_id, index))
        return declarations

    def _parse_tool(
        self,
        raw: dict[str, Any],
        pack_dir: Path,
        pack_id: str,
        index: int,
    ) -> DomainToolDeclaration:
        tool_id = str(raw.get("id") or "").strip()
        module = str(raw.get("module") or "").strip()
        class_name = str(raw.get("class") or raw.get("class_name") or "").strip()
        label = tool_id or f"tool[{index}]"
        permissions_present = "permissions" in raw
        permissions = tuple(_string_list(raw.get("permissions")))
        audit = str(raw.get("audit") or "minimal").strip()
        module_path = _domain_tool_module_path(pack_dir, module)

        reason = ""
        if not tool_id:
            reason = "missing required field: id"
        elif not _TOOL_ID_RE.fullmatch(tool_id):
            reason = "tool id must match ^[a-z0-9_]{1,64}$"
        elif not module:
            reason = "missing required field: module"
        elif not _valid_domain_tool_module(module):
            reason = "module must be a dotted path under tools"
        elif module_path is None or not module_path.exists():
            reason = "missing tool module file"
        elif not class_name:
            reason = "missing required field: class"
        elif not _CLASS_RE.fullmatch(class_name):
            reason = "class must be a valid Python identifier"
        elif not permissions_present:
            reason = "missing permissions"
        elif any(not _is_allowed_tool_permission(permission) for permission in permissions):
            reason = "unsupported permission(s): " + ", ".join(
                permission for permission in permissions if not _is_allowed_tool_permission(permission)
            )
        elif audit not in {"minimal", "security"}:
            reason = "audit must be minimal or security"

        if reason:
            return DomainToolDeclaration(
                id=label,
                module=module,
                class_name=class_name,
                permissions=permissions,
                audit="security" if audit == "security" else "minimal",
                module_path=module_path,
                status="skipped",
                unavailable_reason=reason,
            )
        return DomainToolDeclaration(
            id=tool_id,
            module=module,
            class_name=class_name,
            permissions=permissions,
            audit="security" if audit == "security" else "minimal",
            module_path=module_path,
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


def _declaration_summary(items: tuple[DomainSkillDeclaration | DomainToolDeclaration, ...]) -> str:
    if not items:
        return ""
    available = [item.id for item in items if item.status == "available"]
    skipped = [item.id for item in items if item.status == "skipped"]
    parts: list[str] = []
    if available:
        parts.append(", ".join(f"`{item}`" for item in available[:8]))
        if len(available) > 8:
            parts.append(f"+{len(available) - 8} more")
    if skipped:
        parts.append(f"{len(skipped)} skipped")
    return "; ".join(parts)


def _valid_domain_tool_module(module: str) -> bool:
    if not module.startswith("tools."):
        return False
    if any(part in module for part in ("/", "\\", "..")):
        return False
    return bool(_MODULE_RE.fullmatch(module))


def _domain_tool_module_path(pack_dir: Path, module: str) -> Path | None:
    if not _valid_domain_tool_module(module):
        return None
    candidate = pack_dir / Path(*module.split(".")).with_suffix(".py")
    try:
        resolved_candidate = candidate.resolve()
        resolved_pack = pack_dir.resolve()
    except OSError:
        return None
    try:
        resolved_candidate.relative_to(resolved_pack)
    except ValueError:
        return None
    return candidate


def _is_allowed_tool_permission(permission: str) -> bool:
    if permission in _ALLOWED_TOOL_PERMISSIONS:
        return True
    return bool(re.fullmatch(r"device:[a-z0-9_-]+", permission))

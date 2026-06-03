"""Domain pack discovery, validation, and prompt summaries."""

from __future__ import annotations

import re
import importlib.util
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from OriginAgent.agent.domain_pack_schema import DomainPackManifest, DomainPackManifestError

BUILTIN_DOMAIN_PACKS_DIR = Path(__file__).parent.parent / "domain_packs"
_IGNORED_PACK_DIR_NAMES = {"__pycache__"}
_DOMAIN_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_TOOL_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DomainPackStatus = Literal["available", "unavailable", "invalid"]
DomainDeclarationStatus = Literal["available", "skipped"]
DomainToolRuntimeStatus = Literal["registered", "skipped"]
DomainPackSourceKind = Literal["builtin", "local_copy"]


@dataclass(frozen=True)
class DomainPackRequires:
    bins: tuple[str, ...] = ()
    env: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPackDependencies:
    packs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPackSourceInfo:
    kind: DomainPackSourceKind = "local_copy"
    installed_from: str = ""
    installed_at: str = ""


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
class DomainWorkflowDeclaration:
    id: str
    path: Path | None = None
    status: DomainDeclarationStatus = "available"
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class DomainFileDeclaration:
    id: str
    path: Path | None = None
    status: DomainDeclarationStatus = "available"
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class DomainEvalDeclaration:
    id: str
    kind: str
    target: str = ""
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
class DomainRuntimeDeclaration:
    module: str
    factory: str = "build_runtime_contribution"
    module_path: Path | None = None
    status: DomainDeclarationStatus = "available"
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class DomainRuntimeBuildContext:
    pack: "DomainPack"
    workspace: Path
    config: Any
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainRuntimeContribution:
    tool_context: dict[str, Any] = field(default_factory=dict)
    safety_gates: tuple[Any, ...] = ()
    permission_resolvers: tuple[Any, ...] = ()
    context_fragments: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPack:
    id: str
    name: str
    version: str
    path: Path
    source: str
    status: DomainPackStatus
    active: bool = False
    enabled: bool = True
    active_requested: bool = False
    description: str = ""
    capabilities: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    requires: DomainPackRequires = field(default_factory=DomainPackRequires)
    dependencies: DomainPackDependencies = field(default_factory=DomainPackDependencies)
    unavailable_reason: str = ""
    validation_summary: str = ""
    capabilities_path: Path | None = None
    capabilities_content: str = ""
    skills: tuple[DomainSkillDeclaration, ...] = ()
    workflows: tuple[DomainWorkflowDeclaration, ...] = ()
    policies: tuple[DomainFileDeclaration, ...] = ()
    schemas: tuple[DomainFileDeclaration, ...] = ()
    tools: tuple[DomainToolDeclaration, ...] = ()
    runtime: DomainRuntimeDeclaration | None = None
    evals: tuple[DomainEvalDeclaration, ...] = ()
    manifest: dict[str, Any] = field(default_factory=dict)
    verification_status: str = "unknown"
    source_info: DomainPackSourceInfo = field(default_factory=DomainPackSourceInfo)
    overrides_builtin: bool = False

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


class DomainPackValidator:
    """Validate one local domain pack directory into a deterministic record."""

    def __init__(
        self,
        *,
        runtime_config: DomainPackRuntimeConfig | None = None,
        strict_declarations: bool = False,
    ) -> None:
        self.runtime_config = runtime_config or DomainPackRuntimeConfig()
        self.strict_declarations = strict_declarations

    def validate_pack(self, pack_dir: Path, *, source: str) -> DomainPack:
        pack_dir = Path(pack_dir)
        manifest_path = pack_dir / "domain_pack.yaml"
        if not manifest_path.exists():
            return self._invalid(pack_dir.name, pack_dir, source, "missing domain_pack.yaml")

        try:
            manifest = DomainPackManifest.from_yaml(manifest_path)
        except DomainPackManifestError as exc:
            return self._invalid(pack_dir.name, pack_dir, source, str(exc))

        errors: list[str] = []
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

        source_info = DomainPackSourceInfo(
            kind=("builtin" if source == "builtin" else "local_copy"),
            installed_from=str(manifest.source.installed_from if manifest.source else ""),
            installed_at=str(manifest.source.installed_at if manifest.source else ""),
        )
        verification_status = manifest.verification_status
        requires = DomainPackRequires(
            bins=tuple(manifest.requires.bins),
            env=tuple(manifest.requires.env),
        )
        dependencies = DomainPackDependencies(packs=tuple(manifest.dependencies.packs))
        skills, skill_errors = self._validate_skills(manifest, pack_dir)
        workflows, workflow_errors = self._validate_workflows(manifest, pack_dir)
        policies, policy_errors = self._validate_file_list(manifest.policies, pack_dir, "policies")
        schemas, schema_errors = self._validate_file_list(manifest.schemas, pack_dir, "schemas")
        tools, tool_errors = self._validate_tools(manifest, pack_dir)
        runtime, runtime_errors = self._validate_runtime(manifest, pack_dir)
        evals, eval_errors = self._validate_evals(
            manifest,
            skills=skills,
            workflows=workflows,
            tools=tools,
        )
        errors.extend(skill_errors)
        errors.extend(workflow_errors)
        errors.extend(policy_errors)
        errors.extend(schema_errors)
        errors.extend(tool_errors)
        errors.extend(runtime_errors)
        errors.extend(eval_errors)

        if not manifest.enabled:
            status = "unavailable"
            reason = "disabled by manifest"

        enabled = manifest.id not in set(self.runtime_config.disabled)
        if not enabled:
            status = "unavailable"
            reason = "disabled by config"

        if status == "available":
            missing_requirements = self._missing_requirements(manifest)
            if missing_requirements:
                status = "unavailable"
                reason = "missing requirement(s): " + ", ".join(missing_requirements)

        if errors and self.strict_declarations:
            status = "invalid"
            reason = errors[0]

        active_requested = manifest.id in set(self.runtime_config.active)
        active = active_requested and status == "available"
        validation_summary = "Domain pack is valid." if not errors else "; ".join(errors[:5])
        return DomainPack(
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            path=pack_dir,
            source=source,
            status=status,
            active=active,
            enabled=enabled,
            active_requested=active_requested,
            description=manifest.description,
            capabilities=tuple(manifest.capabilities),
            triggers=tuple(manifest.activation.triggers),
            requires=requires,
            dependencies=dependencies,
            unavailable_reason=reason,
            validation_summary=validation_summary,
            capabilities_path=capabilities_path,
            capabilities_content=capabilities_content,
            skills=tuple(skills),
            workflows=tuple(workflows),
            policies=tuple(policies),
            schemas=tuple(schemas),
            tools=tuple(tools),
            runtime=runtime,
            evals=tuple(evals),
            manifest=manifest.model_dump(),
            verification_status=verification_status,
            source_info=source_info,
        )

    @staticmethod
    def _missing_requirements(manifest: DomainPackManifest) -> list[str]:
        import os, shutil

        missing: list[str] = []
        missing.extend(f"CLI: {cmd}" for cmd in manifest.requires.bins if not shutil.which(cmd))
        missing.extend(f"ENV: {name}" for name in manifest.requires.env if not os.environ.get(name))
        return missing

    def _validate_skills(
        self,
        manifest: DomainPackManifest,
        pack_dir: Path,
    ) -> tuple[list[DomainSkillDeclaration], list[str]]:
        declarations: list[DomainSkillDeclaration] = []
        errors: list[str] = []
        for skill_id in manifest.skills:
            stripped = skill_id.strip()
            virtual_id = f"domain:{manifest.id}/{stripped}" if stripped else ""
            if not stripped or not _DOMAIN_ID_RE.fullmatch(stripped):
                errors.append(f"{stripped or 'skills'}: skill id must match ^[a-z0-9_-]+$")
                declarations.append(DomainSkillDeclaration(
                    id=stripped or f"skill[{len(declarations)}]",
                    virtual_id=virtual_id,
                    status="skipped",
                    unavailable_reason="skill id must match ^[a-z0-9_-]+$",
                ))
                continue
            skill_path = pack_dir / "skills" / stripped / "SKILL.md"
            if not skill_path.exists():
                errors.append(f"{stripped}: missing SKILL.md")
                declarations.append(DomainSkillDeclaration(
                    id=stripped, virtual_id=virtual_id, path=skill_path,
                    status="skipped", unavailable_reason="missing SKILL.md",
                ))
                continue
            declarations.append(DomainSkillDeclaration(
                id=stripped, virtual_id=virtual_id, path=skill_path,
            ))
        return declarations, errors

    def _validate_tools(
        self,
        manifest: DomainPackManifest,
        pack_dir: Path,
    ) -> tuple[list[DomainToolDeclaration], list[str]]:
        declarations: list[DomainToolDeclaration] = []
        errors: list[str] = []
        for tool_decl in manifest.tools:
            module_path = _domain_tool_module_path(pack_dir, tool_decl.module)
            tool_id = tool_decl.id
            if not _TOOL_ID_RE.fullmatch(tool_id):
                declarations.append(DomainToolDeclaration(
                    id=tool_id, module=tool_decl.module, class_name=tool_decl.class_name,
                    module_path=module_path, permissions=tuple(tool_decl.permissions),
                    audit=tool_decl.audit, status="skipped",
                    unavailable_reason="tool id must match ^[a-z0-9_]{1,64}$",
                ))
                errors.append(f"{tool_id}: tool id must match ^[a-z0-9_]{1,64}$")
                continue
            if module_path is None or not module_path.exists():
                declarations.append(DomainToolDeclaration(
                    id=tool_id, module=tool_decl.module, class_name=tool_decl.class_name,
                    module_path=module_path, permissions=tuple(tool_decl.permissions),
                    audit=tool_decl.audit, status="skipped",
                    unavailable_reason="missing tool module file",
                ))
                errors.append(f"{tool_id}: missing tool module file")
                continue
            declarations.append(DomainToolDeclaration(
                id=tool_id, module=tool_decl.module, class_name=tool_decl.class_name,
                module_path=module_path, permissions=tuple(tool_decl.permissions),
                audit=tool_decl.audit,
            ))
        return declarations, errors

    def _validate_runtime(
        self,
        manifest: DomainPackManifest,
        pack_dir: Path,
    ) -> tuple[DomainRuntimeDeclaration | None, list[str]]:
        if manifest.runtime is None:
            return None, []
        module_path = _domain_runtime_module_path(pack_dir, manifest.runtime.module)
        if module_path is None or not module_path.exists():
            return (
                DomainRuntimeDeclaration(
                    module=manifest.runtime.module, factory=manifest.runtime.factory,
                    module_path=module_path, status="skipped",
                    unavailable_reason="missing runtime module file",
                ),
                ["runtime: missing runtime module file"],
            )
        return (
            DomainRuntimeDeclaration(
                module=manifest.runtime.module, factory=manifest.runtime.factory,
                module_path=module_path,
            ),
            [],
        )

    def _validate_workflows(
        self,
        manifest: DomainPackManifest,
        pack_dir: Path,
    ) -> tuple[list[DomainWorkflowDeclaration], list[str]]:
        from OriginAgent.agent.workflow_artifacts import validate_workflow_artifact_dir

        declarations: list[DomainWorkflowDeclaration] = []
        errors: list[str] = []
        for wf_id in manifest.workflows:
            stripped = wf_id.strip()
            if not stripped or not _DOMAIN_ID_RE.fullmatch(stripped):
                errors.append(f"{stripped or 'workflows'}: workflow id must match ^[a-z0-9_-]+$")
                declarations.append(DomainWorkflowDeclaration(
                    id=stripped or f"workflow[{len(declarations)}]",
                    status="skipped",
                    unavailable_reason="workflow id must match ^[a-z0-9_-]+$",
                ))
                continue
            wf_path = pack_dir / "workflows" / stripped / "workflow.yaml"
            if not wf_path.exists():
                errors.append(f"{stripped}: missing workflow.yaml")
                declarations.append(DomainWorkflowDeclaration(
                    id=stripped, path=wf_path, status="skipped",
                    unavailable_reason="missing workflow.yaml",
                ))
                continue
            valid, msg = validate_workflow_artifact_dir(
                wf_path.parent, workspace=pack_dir, expected_name=stripped,
            )
            if not valid:
                errors.append(f"{stripped}: {msg}")
                declarations.append(DomainWorkflowDeclaration(
                    id=stripped, path=wf_path, status="skipped", unavailable_reason=msg,
                ))
                continue
            declarations.append(DomainWorkflowDeclaration(id=stripped, path=wf_path))
        return declarations, errors

    def _validate_file_list(
        self,
        file_ids: list[str],
        pack_dir: Path,
        section: str,
    ) -> tuple[list[DomainFileDeclaration], list[str]]:
        declarations: list[DomainFileDeclaration] = []
        errors: list[str] = []
        for file_id in file_ids:
            stripped = file_id.strip()
            if not stripped or not _DOMAIN_ID_RE.fullmatch(stripped):
                errors.append(f"{stripped or section}: {section} id must match ^[a-z0-9_-]+$")
                declarations.append(DomainFileDeclaration(
                    id=stripped or f"{section}[{len(declarations)}]",
                    status="skipped",
                    unavailable_reason=f"{section} id must match ^[a-z0-9_-]+$",
                ))
                continue
            path = _pack_relative_path(pack_dir, f"{section}/{stripped}")
            if path is None:
                declarations.append(DomainFileDeclaration(
                    id=stripped, status="skipped",
                    unavailable_reason=f"{section} path must stay inside the pack root",
                ))
                errors.append(f"{stripped}: {section} path must stay inside the pack root")
                continue
            if not path.exists():
                declarations.append(DomainFileDeclaration(
                    id=stripped, path=path, status="skipped",
                    unavailable_reason=f"missing declared {section.rstrip('s')} path",
                ))
                errors.append(f"{stripped}: missing declared {section.rstrip('s')} path")
                continue
            declarations.append(DomainFileDeclaration(id=stripped, path=path))
        return declarations, errors

    def _validate_evals(
        self,
        manifest: DomainPackManifest,
        *,
        skills: list[DomainSkillDeclaration],
        workflows: list[DomainWorkflowDeclaration],
        tools: list[DomainToolDeclaration],
    ) -> tuple[list[DomainEvalDeclaration], list[str]]:
        declarations: list[DomainEvalDeclaration] = []
        errors: list[str] = []
        allowed_kinds = {"manifest", "skill", "tool", "workflow"}
        known_targets = {
            "skill": {s.id for s in skills},
            "workflow": {w.id for w in workflows},
            "tool": {t.id for t in tools},
        }
        for eval_decl in manifest.evals:
            eval_id = eval_decl.get("id", "")
            kind = eval_decl.get("kind", "").lower()
            target = eval_decl.get("target", "")
            if not eval_id or not _DOMAIN_ID_RE.fullmatch(eval_id):
                errors.append(f"{eval_id or 'evals'}: eval id must match ^[a-z0-9_-]+$")
                declarations.append(DomainEvalDeclaration(
                    id=eval_id or f"eval[{len(declarations)}]",
                    kind=kind, status="skipped",
                    unavailable_reason="eval id must match ^[a-z0-9_-]+$",
                ))
                continue
            if kind not in allowed_kinds:
                errors.append(f"{eval_id}: eval kind must be one of manifest, skill, tool, workflow")
                declarations.append(DomainEvalDeclaration(
                    id=eval_id, kind=kind, status="skipped",
                    unavailable_reason="eval kind must be one of manifest, skill, tool, workflow",
                ))
                continue
            if target and kind in known_targets and target not in known_targets[kind]:
                errors.append(f"{eval_id}: eval target `{target}` was not declared under {kind}s")
                declarations.append(DomainEvalDeclaration(
                    id=eval_id, kind=kind, target=target, status="skipped",
                    unavailable_reason=f"eval target `{target}` was not declared under {kind}s",
                ))
                continue
            declarations.append(DomainEvalDeclaration(id=eval_id, kind=kind, target=target))
        return declarations, errors

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
            validation_summary=reason,
            manifest=manifest or {},
            source_info=DomainPackSourceInfo(kind="builtin" if source == "builtin" else "local_copy"),
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
        self._validator = DomainPackValidator(runtime_config=self.config, strict_declarations=False)

    def refresh(self) -> None:
        self._validator = DomainPackValidator(runtime_config=self.config, strict_declarations=False)
        self._packs = None

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

    def active_runtime_contributions(
        self,
        *,
        workspace: Path,
        config: Any,
        overrides: dict[str, Any] | None = None,
    ) -> list[DomainRuntimeContribution]:
        contributions: list[DomainRuntimeContribution] = []
        for pack in self.list_packs():
            if not pack.active or pack.runtime is None or not pack.runtime.available:
                continue
            contribution = self._load_runtime_contribution(
                pack,
                DomainRuntimeBuildContext(
                    pack=pack,
                    workspace=Path(workspace),
                    config=config,
                    overrides=dict(overrides or {}),
                ),
            )
            if contribution is not None:
                contributions.append(contribution)
        return contributions

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
        builtin_packs: dict[str, DomainPack] = {}
        for pack_dir in self._pack_dirs(self.builtin_dir):
            pack = self._validator.validate_pack(pack_dir, source="builtin")
            builtin_packs[pack.id] = pack
            packs[pack.id] = pack
        for pack_dir in self._pack_dirs(self.workspace_dir):
            pack = self._validator.validate_pack(pack_dir, source="workspace")
            if pack.id in builtin_packs:
                pack = replace(pack, overrides_builtin=True)
            packs[pack.id] = pack
        return packs

    def _load_runtime_contribution(
        self,
        pack: DomainPack,
        context: DomainRuntimeBuildContext,
    ) -> DomainRuntimeContribution | None:
        declaration = pack.runtime
        if declaration is None or declaration.module_path is None:
            return None
        try:
            module_file = declaration.module_path.resolve()
            module_file.relative_to(pack.path.resolve())
            spec = importlib.util.spec_from_file_location(
                _runtime_module_name(pack.id, declaration),
                module_file,
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            factory = getattr(module, declaration.factory, None)
            if not callable(factory):
                return None
            raw = factory(context)
        except Exception:
            return None
        if raw is None:
            return None
        if isinstance(raw, DomainRuntimeContribution):
            return raw
        if isinstance(raw, dict):
            return DomainRuntimeContribution(**raw)
        return None

    @staticmethod
    def _pack_dirs(root: Path | None) -> list[Path]:
        if root is None or not root.exists() or not root.is_dir():
            return []
        return sorted(
            [
                path
                for path in root.iterdir()
                if path.is_dir() and path.name not in _IGNORED_PACK_DIR_NAMES
            ],
            key=lambda path: path.name,
        )


def _declaration_summary(
    items: tuple[
        DomainSkillDeclaration
        | DomainWorkflowDeclaration
        | DomainFileDeclaration
        | DomainToolDeclaration
        | DomainEvalDeclaration,
        ...,
    ]
) -> str:
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


def _valid_domain_runtime_module(module: str) -> bool:
    if not module.startswith("runtime."):
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


def _domain_runtime_module_path(pack_dir: Path, module: str) -> Path | None:
    if not _valid_domain_runtime_module(module):
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


def _runtime_module_name(pack_id: str, declaration: DomainRuntimeDeclaration) -> str:
    safe_pack = pack_id.replace("-", "_")
    safe_runtime = declaration.module.replace(".", "_")
    return f"_originagent_domain_pack_{safe_pack}_{safe_runtime}"


def _pack_relative_path(pack_dir: Path, relative_path: str) -> Path | None:
    candidate = pack_dir / relative_path
    try:
        resolved_candidate = candidate.resolve()
        resolved_pack = pack_dir.resolve()
        resolved_candidate.relative_to(resolved_pack)
    except (OSError, ValueError):
        return None
    return candidate



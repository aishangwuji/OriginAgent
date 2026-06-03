"""Pydantic models for domain_pack.yaml manifest validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class DomainPackManifestError(Exception):
    """Raised when a domain_pack.yaml is invalid."""


ALLOWED_TOOL_PERMISSIONS: frozenset[str] = frozenset(
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


class ToolDeclaration(BaseModel, extra="forbid"):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9_]{1,64}$")
    module: str
    class_name: str = Field(alias="class")
    permissions: list[str]
    audit: Literal["minimal", "security"] = "minimal"

    @field_validator("module")
    @classmethod
    def _module_must_start_with_tools(cls, v: str) -> str:
        if not v.startswith("tools."):
            raise ValueError("module must start with 'tools.'")
        return v

    @field_validator("permissions")
    @classmethod
    def _validate_permissions(cls, v: list[str]) -> list[str]:
        import re

        allowed = ALLOWED_TOOL_PERMISSIONS
        device_re = re.compile(r"^device:[a-z0-9_-]+$")
        for perm in v:
            if perm not in allowed and not device_re.fullmatch(perm):
                raise ValueError(f"unsupported permission: {perm}")
        return v


class RuntimeDeclaration(BaseModel, extra="forbid"):
    module: str = Field(min_length=1)
    factory: str = "build_runtime_contribution"

    @field_validator("module")
    @classmethod
    def _module_must_start_with_runtime(cls, v: str) -> str:
        if not v.startswith("runtime."):
            raise ValueError("module must start with 'runtime.'")
        return v


class ActivationConfig(BaseModel, extra="forbid"):
    triggers: list[str] = []


class RequiresConfig(BaseModel, extra="forbid"):
    bins: list[str] = []
    env: list[str] = []


class DependenciesConfig(BaseModel, extra="forbid"):
    packs: list[str] = []


class SourceInfoConfig(BaseModel, extra="forbid"):
    kind: Literal["builtin", "local_copy"] = "local_copy"
    installed_from: str = ""
    installed_at: str = ""


class DomainPackManifest(BaseModel, extra="forbid"):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+$")
    enabled: bool = True
    description: str = ""
    capabilities: list[str] = []
    activation: ActivationConfig = ActivationConfig()
    requires: RequiresConfig = RequiresConfig()
    dependencies: DependenciesConfig = DependenciesConfig()
    skills: list[str] = []
    tools: list[ToolDeclaration] = []
    runtime: RuntimeDeclaration | None = None
    source: SourceInfoConfig | None = None
    verification_status: str = "unknown"
    workflows: list[str] = []
    policies: list[str] = []
    schemas: list[str] = []
    evals: list[dict[str, str]] = []

    @field_validator("skills", mode="before")
    @classmethod
    def _coerce_skills(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        result: list[str] = []
        for item in v:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                sid = item.get("id") or item.get("name") or ""
                result.append(str(sid).strip())
        return result

    @field_validator("workflows", "policies", "schemas", mode="before")
    @classmethod
    def _coerce_str_lists(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        result: list[str] = []
        for item in v:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                sid = str(item.get("id") or item.get("name") or "").strip()
                if sid:
                    result.append(sid)
        return result

    @field_validator("evals", mode="before")
    @classmethod
    def _coerce_evals(cls, v: object) -> list[dict[str, str]]:
        if not isinstance(v, list):
            return []
        result: list[dict[str, str]] = []
        for item in v:
            if isinstance(item, dict):
                result.append({str(k): str(v) for k, v in item.items()})
        return result

    @field_validator("verification_status")
    @classmethod
    def _validate_verification_status(cls, v: str) -> str:
        if v not in {"unknown", "unverified", "verified"}:
            raise ValueError("verification_status must be unknown, unverified, or verified")
        return v

    @classmethod
    def from_yaml(cls, path: Path) -> "DomainPackManifest":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise DomainPackManifestError(f"YAML parse error: {exc}") from exc
        if not isinstance(raw, dict):
            raise DomainPackManifestError("domain_pack.yaml must be a mapping")
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "DomainPackManifest":
        try:
            return cls.model_validate(raw)
        except Exception as exc:
            msg = str(exc)
            raise DomainPackManifestError(msg) from exc

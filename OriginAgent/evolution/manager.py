"""Local evolution module staging manager."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from filelock import FileLock

from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.ledger import EvolutionLedger, canonical_dump
from OriginAgent.evolution.package import (
    EvolutionPackage,
    copy_artifact,
    load_package,
)

STAGING_SCHEMA_VERSION = "originagent.evolution.staging.v1"
StageStatus = Literal["staged", "already_staged", "failed"]


@dataclass(frozen=True)
class EvolutionStageResult:
    ok: bool
    status: StageStatus
    module_id: str = ""
    module_type: str = ""
    module_version: str = ""
    artifact_digest: str = ""
    staging_path: str = ""
    events: tuple[EvolutionEvent, ...] = ()
    error: str = ""


class EvolutionModuleManager:
    """Stage local evolution module packages without activating or executing them."""

    def __init__(
        self,
        workspace: Path,
        ledger: EvolutionLedger | None = None,
        lock_path: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.ledger = ledger or EvolutionLedger(self.workspace)
        memory_dir = self.workspace / "memory"
        self.staging_root = memory_dir / "evolution_staging"
        self._lock_path = Path(lock_path) if lock_path is not None else memory_dir / ".evolution_manager.lock"
        with self._locked():
            self._cleanup_stale_tmp_dirs_unlocked()

    def stage(self, source_path: str | Path, *, actor: str = "user") -> EvolutionStageResult:
        """Validate and copy a local module package into the staging store."""

        source_name = _source_name(source_path)
        relative_staging_path = ""
        events: list[EvolutionEvent] = []
        package: EvolutionPackage | None = None
        try:
            with self._locked():
                package = load_package(source_path)
                source_name = package.source_name
                manifest = package.manifest
                target_dir = self.staging_root / package.artifact_digest
                relative_staging_path = target_dir.relative_to(self.workspace).as_posix()
                result_base = {
                    "source_name": source_name,
                    "staging_path": relative_staging_path,
                }
                common = {
                    "actor": actor,
                    "module_id": manifest.module_id,
                    "module_version": manifest.version,
                    "module_type": manifest.module_type,
                    "artifact_digest": package.artifact_digest,
                }
                proposed = self.ledger.append(
                    EvolutionEvent.new(
                        EventType.MODULE_PROPOSED,
                        **common,
                        result={**result_base, "status": "proposed"},
                    )
                )
                events.append(proposed)
                validated = self.ledger.append(
                    EvolutionEvent.new(
                        EventType.MODULE_MANIFEST_VALIDATED,
                        **common,
                        result={**result_base, "status": "validated"},
                    )
                )
                events.append(validated)

                existing = self._has_valid_staging_unlocked(package.artifact_digest)
                status: StageStatus = "already_staged" if existing else "staged"
                installed_event = EvolutionEvent.new(
                    EventType.MODULE_INSTALLED_STAGING,
                    **common,
                    result={**result_base, "status": status},
                )

                if existing:
                    installed = self.ledger.append(installed_event)
                    events.append(installed)
                    return _stage_result(
                        ok=True,
                        status="already_staged",
                        package=package,
                        staging_path=relative_staging_path,
                        events=events,
                    )

                if target_dir.exists():
                    _cleanup_path(target_dir)
                self._stage_package_unlocked(
                    package,
                    target_dir,
                    ledger_event_id=installed_event.event_id,
                )
                try:
                    installed = self.ledger.append(installed_event)
                except Exception:
                    _cleanup_path(target_dir)
                    raise
                events.append(installed)
                return _stage_result(
                    ok=True,
                    status="staged",
                    package=package,
                    staging_path=relative_staging_path,
                    events=events,
                )
        except Exception as exc:
            public_error = _safe_error(exc, source_path, self.workspace)
            failed = self.ledger.append(
                EvolutionEvent.new(
                    EventType.MODULE_FAILED,
                    actor=actor,
                    module_id=package.manifest.module_id if package is not None else "",
                    module_version=package.manifest.version if package is not None else "",
                    module_type=package.manifest.module_type if package is not None else "",
                    artifact_digest=package.artifact_digest if package is not None else "",
                    result={
                        "source_name": source_name,
                        "staging_path": relative_staging_path,
                        "status": "failed",
                        "error": public_error,
                    },
                )
            )
            events.append(failed)
            return EvolutionStageResult(
                ok=False,
                status="failed",
                module_id=package.manifest.module_id if package is not None else "",
                module_type=package.manifest.module_type if package is not None else "",
                module_version=package.manifest.version if package is not None else "",
                artifact_digest=package.artifact_digest if package is not None else "",
                events=tuple(events),
                error=public_error,
            )

    def _locked(self) -> FileLock:
        self.staging_root.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def _cleanup_stale_tmp_dirs_unlocked(self) -> None:
        if not self.staging_root.exists():
            return
        for child in self.staging_root.iterdir():
            if child.name.startswith(".tmp-"):
                _cleanup_path(child)

    def _has_valid_staging_unlocked(self, artifact_digest: str) -> bool:
        target_dir = self.staging_root / artifact_digest
        if not target_dir.exists():
            return False
        staging_json = target_dir / "staging.json"
        if not staging_json.exists():
            _cleanup_path(target_dir)
            return False
        try:
            data = json.loads(staging_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _cleanup_path(target_dir)
            return False
        if data.get("artifact_digest") != artifact_digest:
            _cleanup_path(target_dir)
            return False
        if not (target_dir / "artifact").is_dir():
            _cleanup_path(target_dir)
            return False
        return True

    def _stage_package_unlocked(
        self,
        package: EvolutionPackage,
        target_dir: Path,
        *,
        ledger_event_id: str,
    ) -> None:
        self.staging_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.staging_root / f".tmp-{uuid.uuid4().hex}"
        _cleanup_path(tmp_dir)
        try:
            copy_artifact(package.source_path, tmp_dir / "artifact")
            metadata = {
                "schema_version": STAGING_SCHEMA_VERSION,
                "module_id": package.manifest.module_id,
                "module_type": package.manifest.module_type,
                "version": package.manifest.version,
                "artifact_digest": package.artifact_digest,
                "staged_at": datetime.now(timezone.utc).isoformat(),
                "ledger_event_id": ledger_event_id,
            }
            (tmp_dir / "staging.json").write_bytes(canonical_dump(metadata) + b"\n")
            tmp_dir.rename(target_dir)
        except Exception:
            _cleanup_path(tmp_dir)
            raise


def _stage_result(
    *,
    ok: bool,
    status: StageStatus,
    package: EvolutionPackage,
    staging_path: str,
    events: list[EvolutionEvent],
) -> EvolutionStageResult:
    return EvolutionStageResult(
        ok=ok,
        status=status,
        module_id=package.manifest.module_id,
        module_type=package.manifest.module_type,
        module_version=package.manifest.version,
        artifact_digest=package.artifact_digest,
        staging_path=staging_path,
        events=tuple(events),
    )


def _source_name(source_path: str | Path) -> str:
    return Path(source_path).expanduser().resolve(strict=False).name


def _safe_error(exc: Exception, source_path: str | Path, workspace: Path) -> str:
    message = str(exc)
    for sensitive in (
        str(Path(source_path).expanduser().resolve(strict=False)),
        str(Path(workspace).expanduser().resolve(strict=False)),
    ):
        if sensitive:
            message = message.replace(sensitive, "<path>")
    return message


def _cleanup_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

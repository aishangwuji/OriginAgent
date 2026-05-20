"""Deterministic curator proposals for reviewed workspace artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from OpenHome.agent.background_review import ReviewProposal, ReviewProposalStore
from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.facts import CONFLICT_CATEGORIES, FactStore, normalize_fact_content
from OpenHome.agent.memory import redact_memory_text
from OpenHome.agent.skill_lifecycle import _read_skill_markdown
from OpenHome.agent.skills import SkillsLoader
from OpenHome.agent.workflow_artifacts import validate_workflow_artifact_dir

CURATOR_ORIGIN = "curator"
_CURATOR_SESSION_KEY = "curator:system"
_BODY_MAX_CHARS = 2400
_RATIONALE_MAX_CHARS = 1200
_TITLE_MAX_CHARS = 160
_EVIDENCE_MAX_CHARS = 500
_MAX_EVIDENCE = 5
_NORMALIZE_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CuratorResult:
    """Runtime outcome for curator proposal generation."""

    status: str
    proposals_written: int = 0
    reason: str = ""


class CuratorService:
    """Generate deterministic curator proposals into the shared review store."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: Any | None = None,
        config_loader: Any | None = None,
        domain_pack_manager: DomainPackManager | None = None,
        store: ReviewProposalStore | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self._config = config
        self._config_loader = config_loader
        self.domain_pack_manager = domain_pack_manager
        self.store = store or ReviewProposalStore(self.workspace)
        self._running = 0
        self._last_result: CuratorResult | None = None

    def refresh_config(self) -> None:
        if self._config_loader is None:
            return
        try:
            self._config = self._config_loader()
        except Exception:
            logger.exception("Failed to refresh curator config")

    @property
    def config(self) -> Any:
        if self._config is None:
            from OpenHome.config.schema import CuratorConfig

            self._config = CuratorConfig()
        return self._config

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    def runtime_status(self) -> dict[str, Any]:
        stats = self.store.stats(origin=CURATOR_ORIGIN)
        return {
            "curator_enabled": self.enabled,
            "curator_running_count": self._running,
            "curator_proposal_count": stats["proposal_count"],
            "curator_pending_count": stats["pending_count"],
            "curator_last_created_at": stats["last_created_at"],
            "curator_last_result": asdict(self._last_result) if self._last_result is not None else None,
            "curator_type_counts": self.store.type_counts(origin=CURATOR_ORIGIN),
        }

    async def review_workspace(
        self,
        *,
        session_key: str,
        turn_id: str,
    ) -> CuratorResult:
        self.refresh_config()
        if not self.enabled:
            return self._remember(CuratorResult(status="skipped", reason="disabled"))
        if self._running > 0:
            return self._remember(CuratorResult(status="skipped", reason="concurrency_limit"))

        self._running += 1
        try:
            proposals = self._build_proposals(session_key=session_key, turn_id=turn_id)
            written = await asyncio.to_thread(self.store.append_many, proposals)
            return self._remember(CuratorResult(status="ok", proposals_written=written))
        except Exception as exc:
            logger.exception("Curator review failed")
            return self._remember(CuratorResult(status="error", reason=str(exc)))
        finally:
            self._running = max(0, self._running - 1)

    def _remember(self, result: CuratorResult) -> CuratorResult:
        self._last_result = result
        return result

    def _build_proposals(self, *, session_key: str, turn_id: str) -> list[ReviewProposal]:
        now = datetime.now(timezone.utc).isoformat()
        limit = max(1, int(getattr(self.config, "max_proposals_per_run", 12) or 12))
        existing = self.store.iter_all()
        review_lookup = {
            str(record.get("id") or ""): record
            for record in existing
            if str(record.get("id") or "")
        }
        deduper = _ProposalDeduper(existing)
        proposals: list[ReviewProposal] = []

        def add_all(items: list[ReviewProposal]) -> None:
            for item in items:
                if len(proposals) >= limit:
                    return
                if deduper.should_add(item):
                    proposals.append(item)
                    deduper.track(item)

        skill_records = self._workspace_skill_records(review_lookup)
        add_all(self._skill_proposals(skill_records, session_key=session_key, turn_id=turn_id, created_at=now))
        if len(proposals) >= limit:
            return proposals

        workflow_records = self._workflow_records(review_lookup)
        add_all(self._workflow_proposals(workflow_records, session_key=session_key, turn_id=turn_id, created_at=now))
        if len(proposals) >= limit:
            return proposals

        add_all(self._fact_conflict_proposals(session_key=session_key, turn_id=turn_id, created_at=now))
        return proposals

    def _workspace_skill_records(self, review_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        loader = SkillsLoader(self.workspace, domain_pack_manager=self.domain_pack_manager)
        records: list[dict[str, Any]] = []
        for record in loader.list_skill_records(filter_unavailable=False):
            if str(record.get("source") or "") != "workspace":
                continue
            path = Path(str(record.get("path") or ""))
            frontmatter, body = _read_skill_markdown(path)
            frontmatter_name = str(frontmatter.get("name") or record.get("name") or "").strip()
            proposal_id = str(record.get("review_proposal_id") or "").strip()
            records.append({
                **record,
                "frontmatter_name": frontmatter_name or str(record.get("name") or ""),
                "description": str(frontmatter.get("description") or record.get("description") or record.get("name") or "").strip(),
                "body": body.strip(),
                "content_hash": _skill_content_hash(
                    frontmatter_name or str(record.get("name") or ""),
                    str(frontmatter.get("description") or record.get("description") or ""),
                    body,
                ),
                "created_at": str(review_lookup.get(proposal_id, {}).get("created_at") or ""),
                "subject_path": f"skills/{record.get('name')}/SKILL.md",
            })
        return records

    def _skill_proposals(
        self,
        records: list[dict[str, Any]],
        *,
        session_key: str,
        turn_id: str,
        created_at: str,
    ) -> list[ReviewProposal]:
        proposals: list[ReviewProposal] = []
        by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in records:
            group_key = (str(record.get("domain_id") or "core"), str(record.get("content_hash") or ""))
            by_group.setdefault(group_key, []).append(record)

        duplicate_info: dict[str, dict[str, Any]] = {}
        for group in by_group.values():
            if len(group) <= 1:
                continue
            ordered = sorted(group, key=_skill_priority_key)
            ambiguous = _skill_group_ambiguous(ordered)
            canonical = ordered[0]
            duplicate_info[str(canonical.get("name") or "")] = {
                "canonical": True,
                "ambiguous": ambiguous,
            }
            for record in ordered[1:]:
                duplicate_info[str(record.get("name") or "")] = {
                    "canonical": False,
                    "ambiguous": ambiguous,
                    "canonical_record": canonical,
                }
            if ambiguous:
                names = [str(item.get("name") or "") for item in ordered]
                proposals.append(self._proposal(
                    session_key=session_key,
                    turn_id=turn_id,
                    created_at=created_at,
                    proposal_type="merge_skill",
                    domain_id=str(canonical.get("domain_id") or "core"),
                    title=f"Review duplicate workspace skills: {', '.join(names[:2])}",
                    content=(
                        f"Multiple workspace skills in domain `{canonical.get('domain_id') or 'core'}` "
                        "have the same normalized content and cannot be safely auto-deprecated."
                    ),
                    rationale=(
                        "The duplicate skill group has matching lifecycle priority and creation ordering, "
                        "so curator cannot safely choose a single survivor in P10."
                    ),
                    evidence=[
                        f"{item.get('name')}: {item.get('subject_path')}" for item in ordered[:_MAX_EVIDENCE]
                    ],
                    payload={
                        "subject_type": "skill_group",
                        "subject_id": ",".join(names),
                        "subject_path": ", ".join(str(item.get("subject_path") or "") for item in ordered[:2]),
                        "curator_key": f"merge-skill:{canonical.get('domain_id') or 'core'}:{canonical.get('content_hash') or ''}",
                        "target_state_hash": _stable_hash(names),
                        "suggested_action": "merge_skill",
                        "impact_summary": "Manual skill merge review required.",
                        "skill_names": names,
                    },
                    confidence=0.78,
                ))

        for record in records:
            name = str(record.get("name") or "")
            lifecycle = str(record.get("lifecycle_status") or "")
            verification = str(record.get("verification_status") or "")
            created_by = str(record.get("created_by") or "")
            domain_id = str(record.get("domain_id") or "core")
            duplicate = duplicate_info.get(name)

            if (
                created_by == "background_review"
                and lifecycle == "proposed"
                and verification == "verified"
                and not (duplicate and not duplicate.get("canonical"))
                and not (duplicate and duplicate.get("ambiguous"))
            ):
                proposals.append(self._proposal(
                    session_key=session_key,
                    turn_id=turn_id,
                    created_at=created_at,
                    proposal_type="promote_skill",
                    domain_id=domain_id,
                    title=f"Promote verified skill `{name}`",
                    content=(
                        f"Workspace skill `{name}` is verified but still proposed. "
                        "Curator recommends activating it for normal discovery."
                    ),
                    rationale=(
                        "The skill is already verified and is not blocked by a stronger duplicate candidate."
                    ),
                    evidence=[
                        f"skill={name}",
                        f"lifecycle={lifecycle}",
                        f"verification={verification}",
                        str(record.get("subject_path") or ""),
                    ],
                    payload={
                        "subject_type": "skill",
                        "subject_id": name,
                        "subject_path": str(record.get("subject_path") or ""),
                        "curator_key": f"promote-skill:{name}",
                        "target_state_hash": _stable_hash([
                            name,
                            lifecycle,
                            verification,
                            str(record.get("last_lifecycle_event_id") or ""),
                        ]),
                        "suggested_action": "promote_skill",
                        "impact_summary": "Skill will become active/verified for default discovery.",
                        "skill_name": name,
                    },
                    confidence=0.84,
                ))

            if duplicate and not duplicate.get("canonical") and not duplicate.get("ambiguous"):
                canonical = dict(duplicate.get("canonical_record") or {})
                proposals.append(self._proposal(
                    session_key=session_key,
                    turn_id=turn_id,
                    created_at=created_at,
                    proposal_type="deprecate_skill",
                    domain_id=domain_id,
                    title=f"Deprecate duplicate skill `{name}`",
                    content=(
                        f"Workspace skill `{name}` duplicates `{canonical.get('name')}` "
                        "and is the weaker copy in this normalized skill group."
                    ),
                    rationale=(
                        "Curator selected a stronger canonical skill using lifecycle status, "
                        "verification status, proposal creation ordering, and skill name."
                    ),
                    evidence=[
                        f"duplicate={name}",
                        f"canonical={canonical.get('name')}",
                        str(record.get("subject_path") or ""),
                        str(canonical.get("subject_path") or ""),
                    ],
                    payload={
                        "subject_type": "skill",
                        "subject_id": name,
                        "subject_path": str(record.get("subject_path") or ""),
                        "curator_key": f"deprecate-skill:{name}",
                        "target_state_hash": _stable_hash([
                            name,
                            canonical.get("name") or "",
                            lifecycle,
                            verification,
                            str(record.get("last_lifecycle_event_id") or ""),
                        ]),
                        "suggested_action": "deprecate_skill",
                        "impact_summary": f"Skill `{name}` will be marked deprecated.",
                        "skill_name": name,
                        "canonical_skill_name": str(canonical.get("name") or ""),
                    },
                    confidence=0.88,
                ))

            if (
                lifecycle == "active"
                and verification == "verified"
                and domain_id
                and domain_id != "core"
                and self._domain_pack_available(domain_id)
            ):
                proposals.append(self._proposal(
                    session_key=session_key,
                    turn_id=turn_id,
                    created_at=created_at,
                    proposal_type="move_to_domain",
                    domain_id=domain_id,
                    title=f"Consider moving skill `{name}` into domain pack `{domain_id}`",
                    content=(
                        f"Workspace skill `{name}` is active and verified, and its domain pack "
                        f"`{domain_id}` is available."
                    ),
                    rationale=(
                        "This skill appears mature enough for domain-level governance, "
                        "but P10 keeps the move as a reviewed proposal only."
                    ),
                    evidence=[
                        str(record.get("subject_path") or ""),
                        f"domain={domain_id}",
                        f"lifecycle={lifecycle}",
                        f"verification={verification}",
                    ],
                    payload={
                        "subject_type": "skill",
                        "subject_id": name,
                        "subject_path": str(record.get("subject_path") or ""),
                        "curator_key": f"move-to-domain-skill:{name}:{domain_id}",
                        "target_state_hash": _stable_hash([
                            name,
                            domain_id,
                            lifecycle,
                            verification,
                        ]),
                        "suggested_action": "move_to_domain",
                        "impact_summary": f"Future phase could migrate `{name}` into domain pack `{domain_id}`.",
                        "skill_name": name,
                    },
                    confidence=0.73,
                ))
        return proposals

    def _workflow_records(self, review_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        root = self.workspace / "workflows"
        records: list[dict[str, Any]] = []
        try:
            children = sorted(root.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            return []
        except OSError:
            logger.exception("Failed to read workflow artifacts for curator")
            return []

        for child in children:
            if not child.is_dir():
                continue
            relative_path = f"workflows/{child.name}/workflow.yaml"
            valid, message = validate_workflow_artifact_dir(child, workspace=self.workspace)
            record: dict[str, Any] = {
                "name": child.name,
                "subject_path": relative_path,
                "valid": valid,
                "validation": message,
                "domain_id": "core",
                "created_by": "",
                "review_proposal_id": "",
                "created_at": "",
                "body": "",
                "steps": [],
                "content_hash": "",
            }
            if not valid:
                records.append(record)
                continue
            try:
                raw = yaml.safe_load((child / "workflow.yaml").read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                records.append(record)
                continue
            if not isinstance(raw, dict):
                records.append(record)
                continue
            metadata = raw.get("metadata")
            openhome = metadata.get("OpenHome") if isinstance(metadata, dict) else {}
            if not isinstance(openhome, dict):
                openhome = {}
            proposal_id = str(openhome.get("review_proposal_id") or "")
            body = str(raw.get("body") or "")
            steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
            record.update({
                "name": str(raw.get("name") or child.name),
                "domain_id": str(openhome.get("domain_id") or "core"),
                "created_by": str(openhome.get("created_by") or ""),
                "review_proposal_id": proposal_id,
                "created_at": str(review_lookup.get(proposal_id, {}).get("created_at") or ""),
                "body": body,
                "steps": steps,
                "content_hash": _workflow_content_hash(body, steps),
            })
            records.append(record)
        return records

    def _workflow_proposals(
        self,
        records: list[dict[str, Any]],
        *,
        session_key: str,
        turn_id: str,
        created_at: str,
    ) -> list[ReviewProposal]:
        proposals: list[ReviewProposal] = []
        valid_records = [record for record in records if record.get("valid")]
        for record in records:
            if record.get("valid"):
                continue
            name = str(record.get("name") or "unknown-workflow")
            proposals.append(self._proposal(
                session_key=session_key,
                turn_id=turn_id,
                created_at=created_at,
                proposal_type="archive_workflow",
                domain_id=str(record.get("domain_id") or "core"),
                title=f"Archive invalid workflow `{name}`",
                content=f"Workflow `{name}` no longer passes artifact validation.",
                rationale=(
                    "Curator found an invalid workspace workflow artifact. "
                    "P10 keeps this as a manual archive review."
                ),
                evidence=[
                    str(record.get("subject_path") or ""),
                    str(record.get("validation") or ""),
                ],
                payload={
                    "subject_type": "workflow",
                    "subject_id": name,
                    "subject_path": str(record.get("subject_path") or ""),
                    "curator_key": f"archive-workflow-invalid:{name}",
                    "target_state_hash": _stable_hash([
                        name,
                        str(record.get("validation") or ""),
                    ]),
                    "suggested_action": "archive_workflow",
                    "impact_summary": "Manual archive review for an invalid workflow artifact.",
                    "workflow_name": name,
                },
                confidence=0.83,
            ))

        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in valid_records:
            groups.setdefault(
                (str(record.get("domain_id") or "core"), str(record.get("content_hash") or "")),
                [],
            ).append(record)
        for group in groups.values():
            if len(group) <= 1:
                continue
            ordered = sorted(group, key=_workflow_priority_key)
            canonical = ordered[0]
            for record in ordered[1:]:
                name = str(record.get("name") or "")
                proposals.append(self._proposal(
                    session_key=session_key,
                    turn_id=turn_id,
                    created_at=created_at,
                    proposal_type="archive_workflow",
                    domain_id=str(record.get("domain_id") or "core"),
                    title=f"Archive duplicate workflow `{name}`",
                    content=(
                        f"Workflow `{name}` duplicates `{canonical.get('name')}` "
                        "and is not the canonical artifact in this normalized group."
                    ),
                    rationale=(
                        "Curator found multiple workflow artifacts with the same normalized "
                        "manual guide content."
                    ),
                    evidence=[
                        str(record.get("subject_path") or ""),
                        str(canonical.get("subject_path") or ""),
                    ],
                    payload={
                        "subject_type": "workflow",
                        "subject_id": name,
                        "subject_path": str(record.get("subject_path") or ""),
                        "curator_key": f"archive-workflow-duplicate:{name}",
                        "target_state_hash": _stable_hash([
                            name,
                            str(canonical.get("name") or ""),
                            str(record.get("content_hash") or ""),
                        ]),
                        "suggested_action": "archive_workflow",
                        "impact_summary": f"Workflow `{name}` appears redundant next to `{canonical.get('name')}`.",
                        "workflow_name": name,
                    },
                    confidence=0.8,
                ))

        for record in valid_records:
            domain_id = str(record.get("domain_id") or "core")
            if domain_id and domain_id != "core" and self._domain_pack_available(domain_id):
                name = str(record.get("name") or "")
                proposals.append(self._proposal(
                    session_key=session_key,
                    turn_id=turn_id,
                    created_at=created_at,
                    proposal_type="move_to_domain",
                    domain_id=domain_id,
                    title=f"Consider moving workflow `{name}` into domain pack `{domain_id}`",
                    content=(
                        f"Workflow `{name}` belongs to domain `{domain_id}` and that domain pack is available."
                    ),
                    rationale=(
                        "This workflow could later be governed by the domain pack ecosystem, "
                        "but P10 only records the suggestion."
                    ),
                    evidence=[
                        str(record.get("subject_path") or ""),
                        f"domain={domain_id}",
                    ],
                    payload={
                        "subject_type": "workflow",
                        "subject_id": name,
                        "subject_path": str(record.get("subject_path") or ""),
                        "curator_key": f"move-to-domain-workflow:{name}:{domain_id}",
                        "target_state_hash": _stable_hash([
                            name,
                            domain_id,
                            str(record.get("content_hash") or ""),
                        ]),
                        "suggested_action": "move_to_domain",
                        "impact_summary": f"Future phase could move `{name}` into domain pack `{domain_id}`.",
                        "workflow_name": name,
                    },
                    confidence=0.7,
                ))
        return proposals

    def _fact_conflict_proposals(
        self,
        *,
        session_key: str,
        turn_id: str,
        created_at: str,
    ) -> list[ReviewProposal]:
        store = FactStore(self.workspace)
        groups: dict[tuple[str, str, str], list[Any]] = {}
        for record in store.read_all():
            if record.status in {"deprecated", "contradicted"}:
                continue
            if record.category not in CONFLICT_CATEGORIES:
                continue
            groups.setdefault((record.scope, record.owner, record.category), []).append(record)

        proposals: list[ReviewProposal] = []
        for (scope, owner, category), records in groups.items():
            if len(records) <= 1:
                continue
            normalized = {normalize_fact_content(record.content) for record in records}
            if len(normalized) <= 1:
                continue
            ids = {record.fact_id for record in records}
            if any(record.supersedes_fact_id in ids for record in records if record.supersedes_fact_id):
                continue
            summary = ", ".join(record.fact_id for record in records[:3])
            proposals.append(self._proposal(
                session_key=session_key,
                turn_id=turn_id,
                created_at=created_at,
                proposal_type="fact_conflict",
                domain_id="core",
                title=f"Review conflicting facts for {category}/{scope}",
                content=(
                    f"OpenHome found multiple active facts for `{category}` in scope `{scope}` "
                    "with conflicting normalized content."
                ),
                rationale=(
                    "Curator detected a conflict-prone fact group without an explicit supersedes relationship."
                ),
                evidence=[
                    f"{record.fact_id}: {redact_memory_text(record.content)}"[:_EVIDENCE_MAX_CHARS]
                    for record in records[:_MAX_EVIDENCE]
                ],
                payload={
                    "subject_type": "fact_group",
                    "subject_id": f"{scope}|{owner}|{category}",
                    "subject_path": "memory/facts.jsonl",
                    "curator_key": f"fact-conflict:{scope}:{owner}:{category}",
                    "target_state_hash": _stable_hash([
                        scope,
                        owner,
                        category,
                        *sorted(ids),
                        *sorted(normalized),
                    ]),
                    "suggested_action": "fact_conflict",
                    "impact_summary": f"Manual fact conflict review for {summary}.",
                    "fact_ids": sorted(ids),
                },
                confidence=0.82,
            ))
        return proposals

    def _proposal(
        self,
        *,
        session_key: str,
        turn_id: str,
        created_at: str,
        proposal_type: str,
        domain_id: str,
        title: str,
        content: str,
        rationale: str,
        evidence: list[str],
        payload: dict[str, Any],
        confidence: float,
    ) -> ReviewProposal:
        return ReviewProposal(
            id=f"review_{uuid.uuid4().hex}",
            origin=CURATOR_ORIGIN,
            created_at=created_at,
            session_key=session_key or _CURATOR_SESSION_KEY,
            turn_id=turn_id,
            proposal_type=proposal_type,
            domain_id=domain_id or "core",
            title=_clean_text(title, _TITLE_MAX_CHARS),
            content=_clean_text(content, _BODY_MAX_CHARS),
            rationale=_clean_text(rationale, _RATIONALE_MAX_CHARS),
            confidence=confidence,
            evidence=_clean_evidence(evidence),
            payload=_redact_payload(payload),
        )

    def _domain_pack_available(self, domain_id: str) -> bool:
        if not domain_id or domain_id == "core" or self.domain_pack_manager is None:
            return False
        pack = self.domain_pack_manager.get_pack(domain_id)
        return bool(pack is not None and pack.available)


class _ProposalDeduper:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._pending: set[tuple[str, str]] = set()
        self._terminal: set[tuple[str, str, str]] = set()
        for record in records:
            if str(record.get("origin") or "background_review") != CURATOR_ORIGIN:
                continue
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            curator_key = str(payload.get("curator_key") or "").strip()
            target_state_hash = str(payload.get("target_state_hash") or "").strip()
            proposal_type = str(record.get("proposal_type") or record.get("type") or "").strip().lower()
            if not proposal_type or not curator_key:
                continue
            if str(record.get("status") or "pending") == "pending":
                self._pending.add((proposal_type, curator_key))
            elif target_state_hash:
                self._terminal.add((proposal_type, curator_key, target_state_hash))

    def should_add(self, proposal: ReviewProposal) -> bool:
        payload = proposal.payload if isinstance(proposal.payload, dict) else {}
        curator_key = str(payload.get("curator_key") or "").strip()
        target_state_hash = str(payload.get("target_state_hash") or "").strip()
        proposal_type = str(proposal.proposal_type or "").strip().lower()
        if not proposal_type or not curator_key or not target_state_hash:
            return False
        if (proposal_type, curator_key) in self._pending:
            return False
        if (proposal_type, curator_key, target_state_hash) in self._terminal:
            return False
        return True

    def track(self, proposal: ReviewProposal) -> None:
        payload = proposal.payload if isinstance(proposal.payload, dict) else {}
        curator_key = str(payload.get("curator_key") or "").strip()
        proposal_type = str(proposal.proposal_type or "").strip().lower()
        if proposal_type and curator_key:
            self._pending.add((proposal_type, curator_key))


def _clean_text(text: str, max_chars: int) -> str:
    return redact_memory_text(str(text or "").strip())[:max_chars].strip()


def _clean_evidence(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        text = _clean_text(item, _EVIDENCE_MAX_CHARS)
        if text:
            cleaned.append(text)
        if len(cleaned) >= _MAX_EVIDENCE:
            break
    return cleaned


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_memory_text(value)
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    return value


def _skill_content_hash(name: str, description: str, body: str) -> str:
    payload = "\0".join([
        _normalize_text(name),
        _normalize_text(description),
        _normalize_text(body),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _workflow_content_hash(body: str, steps: list[Any]) -> str:
    normalized_steps = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        normalized_steps.append({
            "title": _normalize_text(str(step.get("title") or "")),
            "instruction": _normalize_text(str(step.get("instruction") or "")),
            "risk": _normalize_text(str(step.get("risk") or "")),
            "confirmation_required": bool(step.get("confirmation_required")),
        })
    payload = json.dumps(
        {
            "body": _normalize_text(body),
            "steps": normalized_steps,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    redacted = redact_memory_text(text or "")
    return _NORMALIZE_SPACE_RE.sub(" ", redacted.strip().casefold())


def _stable_hash(values: list[Any]) -> str:
    payload = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _skill_priority_key(record: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        _skill_rank(str(record.get("lifecycle_status") or ""), str(record.get("verification_status") or "")),
        1 if not str(record.get("created_at") or "") else 0,
        str(record.get("created_at") or ""),
        str(record.get("name") or ""),
    )


def _skill_group_ambiguous(ordered: list[dict[str, Any]]) -> bool:
    if len(ordered) < 2:
        return False
    first = ordered[0]
    second = ordered[1]
    return (
        _skill_rank(str(first.get("lifecycle_status") or ""), str(first.get("verification_status") or ""))
        == _skill_rank(str(second.get("lifecycle_status") or ""), str(second.get("verification_status") or ""))
        and str(first.get("created_at") or "") == str(second.get("created_at") or "")
    )


def _skill_rank(lifecycle: str, verification: str) -> int:
    if lifecycle == "active" and verification == "verified":
        return 0
    if lifecycle == "proposed" and verification == "verified":
        return 1
    if lifecycle == "proposed" and verification == "unverified":
        return 2
    if lifecycle == "deprecated":
        return 3
    if lifecycle == "rejected":
        return 4
    return 5


def _workflow_priority_key(record: dict[str, Any]) -> tuple[int, str, str]:
    return (
        1 if not str(record.get("created_at") or "") else 0,
        str(record.get("created_at") or ""),
        str(record.get("name") or ""),
    )

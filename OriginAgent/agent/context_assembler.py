"""Phase 1 context assembler for continuity-aware prompt construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from OriginAgent.utils.helpers import estimate_message_tokens
from OriginAgent.utils.tracing import log_event


@dataclass(frozen=True)
class ContextAssemblyResult:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)


class ContextAssemblerV2:
    """Thin continuity orchestration and audit layer over ContextBuilder."""

    CONTRACT_VERSION = "continuity.v1.freeze"
    ASSEMBLY_ORDER = [
        "system_prompt",
        "runtime_state",
        "recovered_continuity_checkpoint",
        "continuity_blocks",
        "reference_blocks",
        "internal_event",
        "current_user_message",
    ]

    def __init__(self, builder: Any) -> None:
        self._builder = builder

    def assemble(
        self,
        *,
        current_message: str | None,
        media: list[str] | None,
        channel: str | None,
        chat_id: str | None,
        sender_id: str | None,
        session_summary: str | None,
        session_metadata: dict[str, Any] | None,
        internal_event: tuple[str, str] | None,
        runtime_context: Any | None,
        session_key: str | None,
        recovered_continuity_block: dict[str, Any] | None = None,
        include_current_message: bool = True,
    ) -> ContextAssemblyResult:
        user_content = (
            self._builder.build_user_content(current_message, media)
            if include_current_message
            else []
        )

        runtime_extra_lines = None
        if runtime_context is not None:
            runtime_extra_lines = [
                f"Identity user_id: {runtime_context.user_id}",
                f"Identity session_id: {runtime_context.session_id or ''}",
                f"Identity device_id: {runtime_context.device_id or ''}",
                f"Default scope: {runtime_context.default_scope}",
                f"Trigger: {runtime_context.trigger}",
            ]

        runtime_block = self._builder.build_runtime_context_block(
            channel,
            chat_id,
            self._builder.timezone,
            sender_id=sender_id,
            session_metadata=session_metadata,
            extra_lines=runtime_extra_lines,
        )
        prewarm_bundle = self._builder.prepare_prewarm_bundle(
            session_key,
            runtime_context,
        )
        continuity_blocks = self._builder.build_phase1_continuity_blocks(
            session_key=session_key,
            runtime_context=runtime_context,
            current_message=current_message,
            prewarm_bundle=prewarm_bundle,
        )
        reference_blocks = self._builder.build_reference_context_blocks(
            session_summary=session_summary,
            session_key=session_key,
            runtime_context=runtime_context,
            current_message=current_message,
            prewarm_bundle=prewarm_bundle,
        )

        merged: list[dict[str, Any]] = [
            runtime_block,
            *([recovered_continuity_block] if recovered_continuity_block is not None else []),
            *continuity_blocks,
            *reference_blocks,
        ]
        if internal_event is not None:
            source, content = internal_event
            if content:
                merged.append(self._builder.build_internal_event_block(source, content))
        merged.extend(user_content)

        # 通过公共接口收集审计数据,不再直接访问 builder 的私有属性(封装边界修复)
        audit_artifacts = self._builder.collect_assembly_audit()
        retrieval = audit_artifacts["retrieval_fusion"]
        governance = audit_artifacts["governance"]
        prewarm = audit_artifacts["prewarm"]

        audit = {
            "enabled": True,
            "contract_version": self.CONTRACT_VERSION,
            "assembler_role": "thin_orchestration_audit",
            "assembly_order": list(self.ASSEMBLY_ORDER),
            "session_key": session_key,
            "current_message_included": include_current_message,
            "current_message_preview": str(current_message or "").strip()[:200],
            "block_kinds": [
                block.get("_meta", {}).get("kind")
                for block in merged
                if isinstance(block, dict)
            ],
            "reference_sources": [
                block.get("_meta", {}).get("source")
                for block in reference_blocks
                if isinstance(block, dict)
            ],
            "continuity_block_kinds": [
                block.get("_meta", {}).get("kind")
                for block in continuity_blocks
                if isinstance(block, dict)
            ],
            "recovered_continuity_included": recovered_continuity_block is not None,
            "internal_event_included": internal_event is not None and bool(internal_event[1]),
            "retrieval_fusion_enabled": True,
            "retrieval_sources_used": list(retrieval.get("sources_used", [])),
            "retrieval_source_counts": dict(retrieval.get("source_counts", {})),
            "retrieval_deduped_count": int(retrieval.get("deduped_count", 0) or 0),
            "retrieval_trimmed_count": int(retrieval.get("trimmed_count", 0) or 0),
            "retrieval_hits": dict(retrieval.get("hits", {})),
            "internal_event_source": internal_event[0] if internal_event is not None else None,
            "governance_enabled": audit_artifacts["governance_enabled"],
            "promotion_candidates": list(governance.get("promotion_candidates", [])),
            "promotion_applied_count": int(governance.get("promotion_applied_count", 0) or 0),
            "promotion_conflict_count": int(governance.get("promotion_conflict_count", 0) or 0),
            "forgetting_actions": list(governance.get("forgetting_actions", [])),
            "prewarm_enabled": audit_artifacts["prewarm_enabled"],
            "prewarm_empty": bool(prewarm.get("prewarm_empty", True)),
            "prewarm_reason": prewarm.get("prewarm_reason"),
            "prewarm_sources": list(prewarm.get("prewarm_sources", [])),
            "prewarm_seeded_items": dict(prewarm.get("prewarm_seeded_items", {})),
            "prewarm_seed_counts": dict(prewarm.get("prewarm_seed_counts", {})),
            "runtime_context": (
                {
                    "actor_id": runtime_context.actor_id,
                    "user_id": runtime_context.user_id,
                    "session_id": runtime_context.session_id,
                    "device_id": runtime_context.device_id,
                    "trigger": runtime_context.trigger,
                    "source": runtime_context.source,
                    "scope": runtime_context.default_scope,
                }
                if runtime_context is not None
                else {}
            ),
            "working_memory_included": any(
                block.get("_meta", {}).get("kind") == self._builder.WORKING_MEMORY_CONTEXT_KIND
                for block in continuity_blocks
                if isinstance(block, dict)
            ),
            "world_state_included": any(
                block.get("_meta", {}).get("kind") == self._builder.WORLD_STATE_CONTEXT_KIND
                for block in continuity_blocks
                if isinstance(block, dict)
            ),
            "task_state_included": any(
                block.get("_meta", {}).get("kind") == self._builder.TASK_STATE_CONTEXT_KIND
                for block in continuity_blocks
                if isinstance(block, dict)
            ),
            "media": audit_artifacts["media"],
            "blocks": [
                self._block_trace(
                    block,
                    index=index,
                    reason="assembly_included",
                )
                for index, block in enumerate(merged)
                if isinstance(block, dict)
            ],
        }
        if runtime_context is not None and self._builder.world_state is not None and session_key:
            session_store = self._builder.session_store
            session = session_store.get_or_create(session_key) if session_store is not None else None
            if session is not None:
                filtered = self._builder.world_state.filtered_candidates(
                    session,
                    runtime_context=runtime_context,
                    current_message=current_message,
                )
                audit["world_summary"] = filtered.get("included_summary", {})
                audit["world_filtered_candidates"] = filtered.get("filtered_candidates", [])
                audit["world_freshness"] = filtered.get("freshness", {})
                audit["world_contested"] = filtered.get("contested_summary", {})
                audit["world_selection_reasons"] = list(filtered.get("selection_reasons", []))
        # 不再直接写回 _last_context_assembly_audit;
        # 由 assemble_user_content 从返回值回写(封装边界修复)
        log_event(
            "context.assembled",
            session_key=session_key,
            block_count=len(merged),
            block_kinds=[
                block.get("_meta", {}).get("kind")
                for block in merged
                if isinstance(block, dict)
            ],
            retrieval_sources=list(
                retrieval.get("sources_used", []) if isinstance(retrieval, dict) else []
            ),
            retrieved_total=(
                sum((retrieval.get("source_counts", {}) or {}).values())
                if isinstance(retrieval, dict)
                else 0
            ),
            trimmed_count=(
                int(retrieval.get("trimmed_count", 0) or 0)
                if isinstance(retrieval, dict)
                else 0
            ),
            recovered_continuity_included=recovered_continuity_block is not None,
        )
        return ContextAssemblyResult(blocks=merged, audit=audit)

    @staticmethod
    def _block_trace(
        block: dict[str, Any],
        *,
        index: int,
        reason: str,
    ) -> dict[str, Any]:
        meta = block.get("_meta") if isinstance(block.get("_meta"), dict) else {}
        block_type = str(block.get("type") or "")
        text = str(block.get("text") or "") if isinstance(block.get("text"), str) else ""
        token_estimate = estimate_message_tokens({"role": "user", "content": [block]})
        preview = text[:160] if text else None
        path = meta.get("path")
        source = meta.get("source")
        return {
            "index": index,
            "type": block_type,
            "kind": meta.get("kind"),
            "source": source,
            "trust": meta.get("trust"),
            "path": str(path) if path else None,
            "token_estimate": token_estimate,
            "included_reason": reason,
            "preview": preview,
        }

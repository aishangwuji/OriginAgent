"""Async snapshot inspection orchestration for continuity Phase 3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.providers.base import LLMProvider
from OriginAgent.session.manager import Session


class SnapshotInspectionService:
    """Run snapshot inspection through the current LLM stack and write it back.

    This service intentionally reuses the main runtime provider or auxiliary
    router instead of introducing a second provider stack.
    """

    def __init__(
        self,
        *,
        world_state: WorldStateManager,
        provider: LLMProvider | Any | None = None,
        model: str | None = None,
        auxiliary_router: Any | None = None,
    ) -> None:
        self._world_state = world_state
        self._provider = provider
        self._model = model
        self._auxiliary_router = auxiliary_router
        self._last_status: dict[str, Any] = {
            "snapshot_inspection_enabled": True,
            "last_status": None,
            "last_snapshot_id": None,
            "last_inspector": None,
        }

    def runtime_status(self) -> dict[str, Any]:
        return dict(self._last_status)

    async def inspect(
        self,
        session: Session,
        *,
        runtime_context: Any,
        snapshot_id: str,
        requested_by: str,
    ) -> dict[str, Any]:
        target = self._world_state.get_snapshot(
            session,
            identity=runtime_context,
            snapshot_id=snapshot_id,
        )
        if target is None:
            self._last_status.update({
                "last_status": "not_found",
                "last_snapshot_id": snapshot_id,
            })
            return {"error": f"snapshot '{snapshot_id}' not found"}

        provider = self._select_provider()
        if provider is None or not hasattr(provider, "chat_with_retry"):
            payload = self._failed_payload(
                target,
                reason="inspection provider unavailable",
            )
            result = self._world_state.apply_inspection(
                session,
                runtime_context=runtime_context,
                snapshot_id=snapshot_id,
                inspection_payload=payload,
            )
            result["inspection_path"] = "service_failed"
            self._last_status.update({
                "last_status": "failed",
                "last_snapshot_id": snapshot_id,
                "last_inspector": payload.get("inspector"),
            })
            return result

        try:
            response = await provider.chat_with_retry(
                messages=self._inspection_messages(target),
                model=self._model,
            )
            payload = self._normalize_response(target, response)
        except Exception as exc:
            payload = self._failed_payload(target, reason=str(exc) or "inspection failed")

        result = self._world_state.apply_inspection(
            session,
            runtime_context=runtime_context,
            snapshot_id=snapshot_id,
            inspection_payload=payload,
        )
        result["inspection_path"] = "service"
        self._last_status.update({
            "last_status": str(payload.get("status") or "unknown"),
            "last_snapshot_id": snapshot_id,
            "last_inspector": payload.get("inspector"),
        })
        return result

    def _select_provider(self) -> Any | None:
        if self._auxiliary_router is not None:
            try:
                return self._auxiliary_router.task_provider("snapshot_inspection")
            except Exception:
                return self._provider
        return self._provider

    @staticmethod
    def _inspection_messages(snapshot: Any) -> list[dict[str, Any]]:
        prompt = {
            "snapshot_id": snapshot.snapshot_id,
            "summary": snapshot.summary,
            "objects": list(snapshot.objects),
            "relationships": list(snapshot.relationships),
            "uncertainties": list(snapshot.uncertainties),
            "media_path": snapshot.media_path,
        }
        return [
            {
                "role": "user",
                "content": (
                    "Inspect this snapshot and return strict JSON with keys "
                    "confirmed, corrected, new_details, uncertain, confidence, "
                    "status, contested, contested_reasons, evidence_excerpt.\n"
                    f"{json.dumps(prompt, ensure_ascii=False)}"
                ),
            }
        ]

    def _normalize_response(self, snapshot: Any, response: Any) -> dict[str, Any]:
        content = str(getattr(response, "content", "") or "").strip()
        parsed = self._parse_json_object(content)
        confirmed = self._string_list(parsed.get("confirmed")) or [line for line in snapshot.relationships if line][:4]
        corrected = self._string_list(parsed.get("corrected"))
        new_details = self._string_list(parsed.get("new_details"))
        uncertain = self._string_list(parsed.get("uncertain")) or list(snapshot.uncertainties[:4])
        contested = bool(parsed.get("contested")) or bool(corrected)
        contested_reasons = self._string_list(parsed.get("contested_reasons"))
        if contested and not contested_reasons and corrected:
            contested_reasons = corrected[:4]
        evidence_excerpt = self._string_list(parsed.get("evidence_excerpt"))
        try:
            confidence = float(parsed.get("confidence", snapshot.confidence))
        except (TypeError, ValueError):
            confidence = snapshot.confidence
        confidence = max(0.0, min(1.0, confidence))
        return {
            "confirmed": confirmed,
            "corrected": corrected,
            "new_details": new_details,
            "uncertain": uncertain,
            "confidence": confidence,
            "status": str(parsed.get("status") or "completed").strip() or "completed",
            "contested": contested,
            "contested_reasons": contested_reasons,
            "evidence_excerpt": evidence_excerpt,
            "inspector": self._inspector_name(),
        }

    def _failed_payload(self, snapshot: Any, *, reason: str) -> dict[str, Any]:
        return {
            "confirmed": [],
            "corrected": [],
            "new_details": [],
            "uncertain": self._string_list([reason, *list(snapshot.uncertainties[:3])], limit=4),
            "confidence": max(0.0, min(1.0, float(getattr(snapshot, "confidence", 0.35) or 0.35))),
            "status": "failed",
            "contested": False,
            "contested_reasons": [],
            "evidence_excerpt": self._string_list([f"media_path: {snapshot.media_path}"], limit=2),
            "inspector": self._inspector_name(default="system"),
        }

    def _inspector_name(self, *, default: str = "") -> str:
        if self._model:
            return self._model
        if self._provider is not None:
            name = getattr(self._provider, "__class__", type(self._provider)).__name__
            if isinstance(name, str) and name:
                return name
        return default

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start:end + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def _string_list(values: Any, *, limit: int = 4) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        out: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text:
                out.append(text)
            if len(out) >= limit:
                break
        return out

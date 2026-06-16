"""Async snapshot inspection orchestration for continuity Phase 3."""

from __future__ import annotations

import base64
import json
import mimetypes
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
        workspace: Path | None = None,
        max_image_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._world_state = world_state
        self._provider = provider
        self._model = model
        self._auxiliary_router = auxiliary_router
        self._workspace = Path(workspace) if workspace is not None else getattr(world_state, "_workspace", None)
        self._max_image_bytes = int(max_image_bytes)
        self._last_status: dict[str, Any] = {
            "snapshot_inspection_enabled": True,
            "last_status": None,
            "last_snapshot_id": None,
            "last_inspector": None,
            "last_inspection_mode": None,
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
            messages, inspection_meta = self._inspection_messages(target, provider=provider)
            response = await provider.chat_with_retry(
                messages=messages,
                model=self._model,
            )
            payload = self._normalize_response(target, response, inspection_meta=inspection_meta, provider=provider)
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
            "last_inspection_mode": payload.get("inspection_mode"),
        })
        return result

    def _select_provider(self) -> Any | None:
        if self._auxiliary_router is not None:
            try:
                return self._auxiliary_router.task_provider("snapshot_inspection")
            except Exception:
                return self._provider
        return self._provider

    def _inspection_messages(self, snapshot: Any, *, provider: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prompt = {
            "snapshot_id": snapshot.snapshot_id,
            "summary": snapshot.summary,
            "objects": list(snapshot.objects),
            "relationships": list(snapshot.relationships),
            "uncertainties": list(snapshot.uncertainties),
            "media_path": snapshot.media_path,
            "media_mime_type": getattr(snapshot, "media_mime_type", None),
            "media_status": getattr(snapshot, "media_status", None),
        }
        meta = {
            "inspection_mode": "text",
            "source_mime_type": getattr(snapshot, "media_mime_type", None),
            "failure_reason": None,
        }
        text = (
            "Inspect this snapshot and return strict JSON with keys "
            "confirmed, corrected, new_details, uncertain, confidence, "
            "status, contested, contested_reasons, evidence_excerpt.\n"
            f"{json.dumps(prompt, ensure_ascii=False)}"
        )
        image_block, image_meta = self._image_content_block(snapshot, provider=provider)
        meta.update({k: v for k, v in image_meta.items() if v is not None})
        if image_block is not None:
            meta["inspection_mode"] = "vision"
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        image_block,
                    ],
                }
            ], meta
        if image_meta.get("failure_reason"):
            meta["inspection_mode"] = "text_fallback"
        return [
            {
                "role": "user",
                "content": text,
            }
        ], meta

    def _normalize_response(
        self,
        snapshot: Any,
        response: Any,
        *,
        inspection_meta: dict[str, Any] | None = None,
        provider: Any | None = None,
    ) -> dict[str, Any]:
        inspection_meta = dict(inspection_meta or {})
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
            "inspection_mode": str(inspection_meta.get("inspection_mode") or "text"),
            "provider": self._provider_name(provider),
            "model": self._model,
            "source_media_ids": [snapshot.snapshot_id],
            "source_mime_type": inspection_meta.get("source_mime_type") or getattr(snapshot, "media_mime_type", None),
            "failure_reason": inspection_meta.get("failure_reason"),
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
            "inspection_mode": "failed",
            "provider": self._provider_name(self._provider),
            "model": self._model,
            "source_media_ids": [snapshot.snapshot_id],
            "source_mime_type": getattr(snapshot, "media_mime_type", None),
            "failure_reason": reason,
        }

    def _inspector_name(self, *, default: str = "") -> str:
        if self._model:
            return self._model
        if self._provider is not None:
            name = getattr(self._provider, "__class__", type(self._provider)).__name__
            if isinstance(name, str) and name:
                return name
        return default

    def _provider_name(self, provider: Any | None = None) -> str | None:
        provider = provider if provider is not None else self._provider
        if provider is None:
            return None
        name = getattr(provider, "__class__", type(provider)).__name__
        return name if isinstance(name, str) and name else None

    def _provider_supports_vision(self, provider: Any | None) -> bool:
        if provider is None:
            return False
        for attr in ("supports_vision", "vision_enabled", "supports_multimodal"):
            value = getattr(provider, attr, None)
            if callable(value):
                try:
                    return bool(value())
                except TypeError:
                    continue
            if value is not None:
                return bool(value)
        capabilities = getattr(provider, "capabilities", None)
        if isinstance(capabilities, dict):
            return bool(capabilities.get("vision") or capabilities.get("multimodal"))
        return False

    def _image_content_block(self, snapshot: Any, *, provider: Any | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        meta: dict[str, Any] = {
            "source_mime_type": getattr(snapshot, "media_mime_type", None),
            "failure_reason": None,
        }
        if not str(getattr(snapshot, "kind", "") or "").lower().startswith("image"):
            return None, meta
        if not self._provider_supports_vision(provider):
            meta["failure_reason"] = "provider_vision_unsupported"
            return None, meta
        if self._workspace is None:
            meta["failure_reason"] = "workspace_unavailable"
            return None, meta
        media_path = Path(str(getattr(snapshot, "media_path", "") or ""))
        absolute = media_path if media_path.is_absolute() else Path(self._workspace) / media_path
        try:
            resolved = absolute.resolve()
            workspace = Path(self._workspace).resolve()
            resolved.relative_to(workspace)
        except Exception:
            meta["failure_reason"] = "media_path_outside_workspace"
            return None, meta
        try:
            stat = resolved.stat()
        except OSError:
            meta["failure_reason"] = "media_file_missing"
            return None, meta
        if stat.st_size <= 0:
            meta["failure_reason"] = "media_file_empty"
            return None, meta
        if stat.st_size > self._max_image_bytes:
            meta["failure_reason"] = "media_file_too_large"
            return None, meta
        mime_type = meta.get("source_mime_type") or mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if not str(mime_type).startswith("image/"):
            meta["failure_reason"] = "unsupported_image_mime_type"
            meta["source_mime_type"] = mime_type
            return None, meta
        try:
            data = base64.b64encode(resolved.read_bytes()).decode("ascii")
        except OSError:
            meta["failure_reason"] = "media_file_unreadable"
            return None, meta
        meta["source_mime_type"] = mime_type
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{data}"},
            "_meta": {"path": str(resolved)},
        }, meta

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

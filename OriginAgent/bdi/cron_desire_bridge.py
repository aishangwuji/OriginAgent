"""Bridge between CronService and BDI DeliberationEngine.

Cron handles precise timing. BDI handles intent and strategy.
This bridge connects them without merging their code.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from OriginAgent.bdi.models import Desire, DesirePriority, DesireStatus


class CronDesireBridge:
    """Bidirectional bridge between CronService and BDI.

    Responsibilities:
    1. When a cron job is created, create a linked Desire (if BDI enabled).
    2. When a cron job fires, record the delivery outcome.
    3. Surface failing cron jobs to BDI via ``get_observation_beliefs()``.
    4. Allow BDI to disable a cron job it wants to manage directly.

    The bridge is entirely optional.  If BDI is disabled the bridge
    is not created and CronService works exactly as before.
    """

    def __init__(
        self,
        desire_store: Any,
        observation_store: Any,
        *,
        enabled: bool = True,
    ) -> None:
        self._desire_store = desire_store
        self._observation_store = observation_store
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Job creation ──────────────────────────────────────────────

    def on_cron_job_created(
        self,
        job: Any,
        session_key: str = "",
        owner_id: str = "",
    ) -> str | None:
        """Create a desire linked to *job*.

        Returns the desire ID, or ``None`` if the bridge is disabled or
        the desire already exists (idempotent on restart).
        """
        if not self._enabled:
            return None

        desire_id = f"cron:{job.id}"
        # Check if link already exists (restart resilience)
        existing = self._observation_store.get_link_by_job_id(job.id)
        if existing is not None:
            return desire_id

        content = job.payload.message or f"Scheduled: {job.name}"
        deadline = ""
        if job.schedule.kind == "at" and job.schedule.at_ms:
            from datetime import datetime, timezone

            deadline = datetime.fromtimestamp(job.schedule.at_ms / 1000, tz=timezone.utc).isoformat()

        desire = Desire(
            desire_id=desire_id,
            owner_id=owner_id or "user",
            session_key=session_key,
            content=content,
            status=DesireStatus.PENDING,
            priority=DesirePriority.MEDIUM,
            deadline_at=deadline or None,
            source_foresight_id=None,
            source_episode_id=None,
            source_cycle_id=None,
        )
        try:
            self._desire_store.add(desire)
            self._observation_store.save_link(
                job.id, desire_id,
                session_key=session_key,
                owner_id=owner_id,
            )
            logger.debug("CronDesireBridge: linked job {} to desire {}", job.id, desire_id)
            return desire_id
        except ValueError:
            # Desire already exists — link was already created
            return desire_id
        except Exception:
            logger.opt(exception=True).warning("CronDesireBridge: failed to link job {} to desire", job.id)
            return None

    # ── Delivery observation ───────────────────────────────────────

    async def on_job_completed(
        self,
        job: Any,
        *,
        success: bool,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Record a delivery attempt for *job*.

        BDI picks this up on its next deliberation cycle.
        """
        if not self._enabled:
            return
        try:
            self._observation_store.record_delivery(
                job.id,
                status="ok" if success else "error",
                error=error,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.opt(exception=True).warning("CronDesireBridge: failed to record delivery for job {}", job.id)

    # ── BDI beliefs ───────────────────────────────────────────────

    def get_observation_beliefs(self) -> dict[str, Any]:
        """Return cron observation data for BDI's belief set.

        Called by ``DeliberationEngine._gather_beliefs()`` during each
        deliberation cycle.
        """
        if not self._enabled:
            return {"bridge_enabled": False}

        try:
            failing = self._observation_store.list_failing_links()
            stats = self._observation_store.stats()

            return {
                "bridge_enabled": True,
                "total_linked_jobs": stats.get("total_links", 0),
                "active_jobs": stats.get("active_links", 0),
                "failing_jobs": [
                    {
                        "cron_job_id": link["cron_job_id"],
                        "desire_id": link["desire_id"],
                        "consecutive_failures": link["consecutive_failures"],
                    }
                    for link in failing
                ],
                "failing_count": stats.get("failing_links", 0),
            }
        except Exception:
            return {"bridge_enabled": True, "error": "observation_store_unavailable"}

    # ── Cron override ─────────────────────────────────────────────

    def disable_cron_job(self, cron_job_id: str, *, cron_service: Any = None) -> bool:
        """Mark a cron job as disabled in the link table.

        If *cron_service* is provided, also disables the actual cron job.
        Returns ``True`` if the link was updated.
        """
        if not self._enabled:
            return False
        try:
            updated = self._observation_store.set_cron_disabled(cron_job_id, disabled=True)
            if updated and cron_service is not None:
                cron_service.enable_job(cron_job_id, enabled=False)
            return updated
        except Exception:
            logger.opt(exception=True).warning("CronDesireBridge: failed to disable cron job {}", cron_job_id)
            return False


__all__ = ["CronDesireBridge"]

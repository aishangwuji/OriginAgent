"""Bridge between Session Episodes and Nearline EpisodeRecords.

When a session episode closes, this bridge syncs the episode metadata
to the nearline memory store as an EpisodeRecord, so long-term memory
retains conversation structure even after session trimming.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from OriginAgent.session.manager import Session


def sync_closed_episode_to_nearline(
    session: Session,
    episode_summary: dict[str, Any],
    nearline_store: Any | None,
) -> bool:
    """Sync a closed session episode to nearline memory.

    Args:
        session: The session containing the episode.
        episode_summary: The episode summary dict from ``_episode_summaries``.
        nearline_store: A ``NearlineMemoryStore`` instance, or None (no-op).

    Returns:
        True if the episode was synced, False otherwise.
    """
    from OriginAgent.memory.models import EpisodeRecord

    if nearline_store is None:
        return False

    episode_id = episode_summary.get("episode_id", "")
    if not episode_id:
        return False

    # Build a summary string from available metadata.
    tone = episode_summary.get("tone", "")
    preview = episode_summary.get("preview", "")
    label = episode_summary.get("label") or "(untitled)"
    quotes = episode_summary.get("key_quotes", [])
    wm = episode_summary.get("working_memory_snapshot", {})

    parts: list[str] = [f"Episode: {label}"]
    if tone:
        parts.append(f"Tone: {tone}")
    if preview:
        parts.append(preview)
    if quotes:
        parts.append("Quotes: " + "; ".join(q[:100] for q in quotes[:2]))

    summary = " | ".join(parts)
    goal_summary = str(wm.get("current_goal", "")) if wm else ""
    open_loops = list(wm.get("open_loops", [])) if wm else []
    constraints = list(wm.get("active_constraints", [])) if wm else []
    decisions = list(wm.get("current_plan", [])) if wm else []

    record = EpisodeRecord(
        episode_id=episode_id,
        memcell_id=f"session_ep_{episode_id[:8]}",
        session_key=session.key,
        owner_id="",
        summary=summary,
        content=preview,
        timestamp=datetime.now().isoformat(),
        goal_summary=goal_summary,
        decisions=decisions,
        constraints=constraints,
        open_loops=open_loops,
        key_events=quotes[:3],
        metadata={
            "source": "session_episode_bridge",
            "message_count": episode_summary.get("message_count", 0),
            "label": label,
        },
    )
    try:
        nearline_store.append_episodes([record])
        logger.debug("Synced episode {} to nearline memory", episode_id)
        return True
    except Exception:
        logger.warning("Failed to sync episode {} to nearline memory", episode_id, exc_info=True)
        return False


def sync_episode_summaries(
    session: Session,
    nearline_store: Any | None,
    *,
    max_sync: int = 3,
) -> int:
    """Sync recent unsynced episode summaries to nearline memory.

    Checks ``_episode_summaries`` for entries missing a ``_nearline_synced``
    flag and syncs them.

    Returns:
        Number of episodes synced.
    """
    if nearline_store is None:
        return 0
    summaries: list[dict[str, Any]] = list(
        session.metadata.get("_episode_summaries", [])
    )
    synced = 0
    changed = False
    for entry in summaries:
        if entry.get("_nearline_synced"):
            continue
        if synced >= max_sync:
            break
        if sync_closed_episode_to_nearline(session, entry, nearline_store):
            entry["_nearline_synced"] = True
            synced += 1
            changed = True
    if changed:
        session.metadata["_episode_summaries"] = summaries
    return synced

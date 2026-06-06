from __future__ import annotations

from OriginAgent.memory.models import AgentCaseRecord, CanonicalMessage, EpisodeRecord, ForesightRecord, MemCell, ProfileSnapshot
from OriginAgent.memory.store import NearlineMemoryStore


def _sample_memcell() -> MemCell:
    message = CanonicalMessage(
        message_id="msg_1",
        session_key="websocket:chat1",
        role="user",
        content="remember this preference",
        timestamp="2026-06-05T10:00:00+08:00",
        channel="websocket",
        chat_id="chat1",
    )
    return MemCell(
        memcell_id="memcell_1",
        session_key="websocket:chat1",
        kind="conversation_turn",
        started_at="2026-06-05T10:00:00+08:00",
        ended_at="2026-06-05T10:00:00+08:00",
        message_ids=["msg_1"],
        roles=["user"],
        content="user: remember this preference",
        messages=[message],
        metadata={"message_count": 1},
    )


def test_nearline_store_persists_memcells_and_cursor_without_touching_dream_cursor(tmp_path) -> None:
    store = NearlineMemoryStore(tmp_path)
    dream_cursor = tmp_path / "memory" / ".dream_cursor"
    dream_cursor.parent.mkdir(parents=True, exist_ok=True)
    dream_cursor.write_text("41", encoding="utf-8")

    assert store.append_memcells([_sample_memcell()]) == 1
    assert store.write_cursor(7) == 7
    assert store.advance_cursor(3) == 7
    assert store.advance_cursor(9) == 9

    memcells = store.read_memcells()
    assert len(memcells) == 1
    assert memcells[0].memcell_id == "memcell_1"
    assert memcells[0].messages[0].content == "remember this preference"
    assert store.read_cursor() == 9
    assert dream_cursor.read_text(encoding="utf-8") == "41"

    summary = store.summary(
        nearline_enabled=True,
        pipeline_enabled=False,
        profile_shadow_write_enabled=False,
    )
    assert summary["status"] == "idle"
    assert summary["memcell_count"] == 1
    assert summary["last_memcell_at"] == "2026-06-05T10:00:00+08:00"
    assert summary["latest_cursor"] == 9


def test_nearline_store_read_memcells_limit_returns_recent_suffix(tmp_path) -> None:
    store = NearlineMemoryStore(tmp_path)
    first = _sample_memcell()
    second = MemCell(
        memcell_id="memcell_2",
        session_key="websocket:chat1",
        kind="conversation_turn",
        started_at="2026-06-05T10:05:00+08:00",
        ended_at="2026-06-05T10:05:30+08:00",
        message_ids=["msg_2"],
        roles=["assistant"],
        content="assistant: acknowledged",
        messages=[
            CanonicalMessage(
                message_id="msg_2",
                session_key="websocket:chat1",
                role="assistant",
                content="acknowledged",
                timestamp="2026-06-05T10:05:30+08:00",
            )
        ],
        metadata={"message_count": 1},
    )

    store.append_memcells([first, second])

    memcells = store.read_memcells(limit=1)
    assert [cell.memcell_id for cell in memcells] == ["memcell_2"]


def test_nearline_store_reads_layered_objects(tmp_path) -> None:
    store = NearlineMemoryStore(tmp_path)
    store.append_episodes([
        EpisodeRecord(
            episode_id="ep_1",
            memcell_id="mem_1",
            session_key="cli:direct",
            owner_id="user",
            summary="User asked for a release checklist",
            content="Need a release checklist for Friday deploy.",
            timestamp="2026-06-05T10:00:00+08:00",
            source_message_ids=["msg_1"],
        )
    ])
    store.append_foresights([
        ForesightRecord(
            foresight_id="fo_1",
            memcell_id="mem_1",
            session_key="cli:direct",
            owner_id="user",
            content="Plan to deploy on 2026-06-07",
            evidence="We will ship on 2026-06-07.",
            start_at="2026-06-07T09:00:00+08:00",
            end_at="2026-06-07T12:00:00+08:00",
            timestamp="2026-06-05T10:00:00+08:00",
            source_message_ids=["msg_1"],
        )
    ])
    store.append_agent_cases([
        AgentCaseRecord(
            case_id="case_1",
            memcell_id="mem_2",
            session_key="cli:direct",
            agent_id="origin",
            task_intent="Implement release validation",
            approach="Added smoke tests",
            outcome_summary="Validation passed",
            quality_score=0.9,
            timestamp="2026-06-05T11:00:00+08:00",
            source_message_ids=["msg_2"],
        )
    ])
    store.append_profiles([
        ProfileSnapshot(
            profile_id="profile_1",
            owner_id="user",
            summary="User prefers concise release updates.",
            explicit_traits=["Prefers concise status notes"],
            implicit_traits=["Often asks for deploy checklists"],
            source_memcell_ids=["mem_1"],
            updated_at="2026-06-05T12:00:00+08:00",
        )
    ])

    assert store.read_episodes()[0].episode_id == "ep_1"
    assert store.read_foresights()[0].foresight_id == "fo_1"
    assert store.read_agent_cases()[0].case_id == "case_1"
    assert store.read_profiles()[0].profile_id == "profile_1"

from OriginAgent.session.goal_state import (
    GOAL_STATE_KEY,
    discard_legacy_goal_state_key,
    goal_state_runtime_lines,
    goal_state_ws_blob,
    parse_goal_state,
    sustained_goal_active,
)


def test_goal_state_runtime_lines_only_when_active():
    assert goal_state_runtime_lines(None) == []
    assert goal_state_runtime_lines({GOAL_STATE_KEY: {"status": "completed"}}) == []

    lines = goal_state_runtime_lines({
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Ship the migration.",
            "ui_summary": "migration",
        }
    })

    assert "Goal (active):" in lines
    assert "Ship the migration." in lines
    assert "Summary: migration" in lines


def test_goal_state_accepts_legacy_key_and_discards_it():
    meta = {
        "thread_goal": {"status": "active", "objective": "Legacy."},
        GOAL_STATE_KEY: {"status": "active", "objective": "Current."},
    }

    assert sustained_goal_active(meta) is True
    assert "Current." in goal_state_runtime_lines(meta)
    assert "Legacy." not in goal_state_runtime_lines(meta)

    discard_legacy_goal_state_key(meta)
    assert "thread_goal" not in meta


def test_goal_state_ws_blob_is_compact_and_json_safe():
    assert goal_state_ws_blob({}) == {"active": False}
    blob = goal_state_ws_blob({
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "x" * 700,
            "ui_summary": "A" * 200,
        }
    })

    assert blob["active"] is True
    assert len(blob["objective"]) <= 603
    assert len(blob["ui_summary"]) == 120


def test_parse_goal_state_accepts_json_string():
    assert parse_goal_state('{"status":"active","objective":"x"}') == {
        "status": "active",
        "objective": "x",
    }


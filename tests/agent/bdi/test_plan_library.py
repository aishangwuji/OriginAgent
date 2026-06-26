"""Tests for PlanLibrary — cached means-ends reasoning for BDI."""

import tempfile
from pathlib import Path

import pytest

from OriginAgent.bdi.models import (
    Desire, DesireStatus, DesirePriority,
    DeliberationIntention, PlanTemplate, PlanMatch, now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.plan_library import PlanLibrary


def make_desire(content: str, **kw) -> Desire:
    return Desire(
        desire_id=f"d_{hash(content) % 10000}",
        owner_id="user:test", session_key="sess:test",
        content=content, **kw,
    )


def make_intent(desire_id: str, action: str = "send_message",
                payload: dict | None = None) -> DeliberationIntention:
    return DeliberationIntention(
        desire_id=desire_id, action=action, scope="test",
        reasoning="Cached plan", payload=payload or {},
    )


class TestPlanTemplate:
    def test_immutable(self):
        t = PlanTemplate(
            plan_id="p1", keywords=("remind", "medicine"),
            action="send_message", scope="telegram",
            payload_template={"text": "Time to take your medicine!"},
            description="Medicine reminder", hit_count=0,
        )
        with pytest.raises(Exception):
            t.action = "changed"

    def test_serialization_roundtrip(self):
        t = PlanTemplate(
            plan_id="p_med_reminder", keywords=("remind", "medicine", "吃药"),
            action="send_message", scope="telegram",
            payload_template={"text": "💊 该吃药了！"},
            description="Medication reminder", hit_count=5,
            last_used_at="2026-06-26T10:00:00",
        )
        data = t.to_json()
        restored = PlanTemplate.from_json(data)
        assert restored.plan_id == "p_med_reminder"
        assert restored.keywords == ("remind", "medicine", "吃药")
        assert restored.hit_count == 5


class TestPlanLibrary:
    @pytest.fixture
    def lib(self):
        with tempfile.TemporaryDirectory() as td:
            yield PlanLibrary(workspace=Path(td))

    def test_add_and_match_exact(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="remind_medicine", keywords=("remind", "medicine", "吃药"),
            action="send_message", scope="telegram",
            payload_template={"text": "💊 Time to take your medicine!"},
            description="Medicine reminder",
        ))
        desire = make_desire("Remind the user to take medicine at 8pm")
        match = lib.match(desire)
        assert match is not None
        assert match.plan.plan_id == "remind_medicine"
        assert match.confidence > 0.5

    def test_match_with_chinese_keywords(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="remind_water", keywords=("喝水", "提醒"),
            action="send_message", scope="telegram",
            payload_template={"text": "💧 记得喝水！"}, description="Water reminder",
        ))
        desire = make_desire("提醒用户多喝水")
        match = lib.match(desire)
        assert match is not None
        assert match.plan.plan_id == "remind_water"

    def test_no_match_for_unfamiliar_desire(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="remind_medicine", keywords=("remind", "medicine"),
            action="send_message", scope="telegram",
            payload_template={"text": "💊"}, description="Medicine",
        ))
        desire = make_desire("Analyze the stock market trends and report back")
        match = lib.match(desire)
        assert match is None or match.confidence < 0.3

    def test_learn_from_llm_result(self, lib: PlanLibrary):
        desire = make_desire("Remind me to check the oven in 30 minutes")
        intent = make_intent(desire.desire_id, "send_message",
                              payload={"text": "⏰ Check the oven!"})
        lib.learn(desire=desire, intention=intent, auto_keywords=True)
        similar = make_desire("Please remind me to check the oven")
        match = lib.match(similar)
        assert match is not None
        assert match.plan.action == "send_message"

    def test_hit_count_increments_on_match(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="daily_standup", keywords=("standup", "daily"),
            action="send_message", scope="slack",
            payload_template={"text": "Time for standup!"},
            description="Daily standup reminder",
        ))
        desire = make_desire("Send the daily standup reminder")
        match1 = lib.match(desire)
        match2 = lib.match(desire)
        assert match1 is not None
        assert match2 is not None
        assert match2.plan.hit_count == match1.plan.hit_count + 1

    def test_list_plans_sorted_by_hit_count(self, lib: PlanLibrary):
        lib.add(PlanTemplate(plan_id="p1", keywords=("a",), action="x", scope="x",
                              payload_template={}, description="A", hit_count=3))
        lib.add(PlanTemplate(plan_id="p2", keywords=("b",), action="x", scope="x",
                              payload_template={}, description="B", hit_count=10))
        lib.add(PlanTemplate(plan_id="p3", keywords=("c",), action="x", scope="x",
                              payload_template={}, description="C", hit_count=1))
        plans = lib.list_plans()
        assert plans[0].plan_id == "p2"
        assert plans[2].plan_id == "p3"

    def test_learn_idempotent(self, lib: PlanLibrary):
        desire = make_desire("Check the door lock")
        intent = make_intent(desire.desire_id, "exec",
                              payload={"command": "check-door-lock"})
        lib.learn(desire=desire, intention=intent, auto_keywords=True)
        before = len(lib.list_plans())
        lib.learn(desire=desire, intention=intent, auto_keywords=True)
        after = len(lib.list_plans())
        assert after == before

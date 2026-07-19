import json
from OriginAgent.domain_packs.smart_home.runtime.action_safety import SmartHomeActionSafetyGate
from datetime import datetime, timedelta, timezone

import pytest

from OriginAgent.agent.action_safety import ActionDecision, ActionRequest
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import (
    PROMPT_MAX_CHARS,
    REASON_MAX_CHARS,
    ConfirmationManager,
    ConfirmationRequest,
    PendingConfirmationStore,
)
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.memory import MemoryWorkspaceSnapshot
from OriginAgent.domain_packs.smart_home.runtime.presence import PresenceStore

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


class FailingAuditLogger:
    def log_confirmation_event(self, **kwargs):
        raise OSError("disk full")


def request(**kwargs):
    defaults = {
        "action": "unlock",
        "scope": "home.entry.lock",
        "trigger": "user_initiated",
        "risk": "high",
    }
    defaults.update(kwargs)
    return ActionRequest(**defaults)


def decision(**kwargs):
    defaults = {
        "decision": "ask_confirmation",
        "reason": "high-risk user action needs confirmation with unknown occupancy",
        "presence_status": "unknown",
    }
    defaults.update(kwargs)
    return ActionDecision(**defaults)


def test_allow_and_deny_create_no_executable_confirmation(tmp_path):
    manager = ConfirmationManager(tmp_path)

    assert manager.create_from_action_decision(
        request(),
        decision(decision="allow", reason="safety checks passed"),
        now=NOW,
    ) is None
    assert manager.create_from_action_decision(
        request(),
        decision(decision="deny", reason="high-risk non-user action denied"),
        now=NOW,
    ) is None
    assert manager.list_action_confirmations() == []


def test_ask_confirmation_creates_pending_action_confirmation(tmp_path):
    manager = ConfirmationManager(tmp_path)

    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    assert confirmation is not None
    assert confirmation.kind == "action_confirmation"
    assert confirmation.status == "pending"
    assert confirmation.action == "unlock"
    assert confirmation.scope == "home.entry.lock"
    assert confirmation.risk == "high"
    assert confirmation.presence_status == "unknown"
    assert confirmation.prompt
    assert manager.store.read_all()[0].confirmation_id == confirmation.confirmation_id


def test_create_confirmation_logs_confirmation_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)

    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    events = audit.find_by_confirmation_id(confirmation.confirmation_id)
    assert events[0].event_type == "confirmation_event"
    assert events[0].decision == "created"
    assert events[0].metadata["confirmation_status"] == "pending"
    assert events[0].metadata["confirmation_kind"] == "action_confirmation"


def test_notify_only_creates_notified_non_executable_item(tmp_path):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(
        request(action="alert", risk="low"),
        decision(decision="notify_only", reason="notification only"),
        now=NOW,
    )

    assert confirmation is not None
    assert confirmation.kind == "notify_only"
    assert confirmation.status == "notified"
    assert manager.store.read_all()[0].confirmation_id == confirmation.confirmation_id

    result = manager.resolve_user_reply(confirmation.confirmation_id, "yes", now=NOW)

    assert result.decision == "rejected"
    assert manager.store.read_all()[0].status == "notified"


def test_notify_only_logs_notified_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)

    confirmation = manager.create_from_action_decision(
        request(action="alert", risk="low"),
        decision(decision="notify_only", reason="notification only"),
        now=NOW,
    )

    events = audit.find_by_confirmation_id(confirmation.confirmation_id)
    assert events[0].decision == "notified"
    assert events[0].metadata["confirmation_status"] == "notified"


def test_tool_approval_creates_pending_confirmation_with_session_metadata(tmp_path):
    manager = ConfirmationManager(tmp_path)

    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="Approve exec?",
        decision_reason="exec requires approval",
        requested_by="alice",
        trigger="user_initiated",
        session_key="websocket:chat-1",
        metadata={"policy_rule": "capability_exec_denied"},
        action_payload={"grant_flags": "{\"can_exec\":true}"},
        idempotency_key="tool_approval:exec:test",
        now=NOW,
    )

    assert confirmation.kind == "tool_approval"
    assert confirmation.status == "pending"
    assert confirmation.action == "tool:exec"
    assert confirmation.metadata["session_key"] == "websocket:chat-1"
    assert confirmation.metadata["tool_name"] == "exec"


def test_tool_approval_reuses_pending_confirmation_by_idempotency_key(tmp_path):
    manager = ConfirmationManager(tmp_path)

    first = manager.create_tool_approval(
        tool_name="exec",
        prompt="Approve exec?",
        decision_reason="exec requires approval",
        session_key="websocket:chat-1",
        idempotency_key="tool_approval:exec:test",
        now=NOW,
    )
    second = manager.create_tool_approval(
        tool_name="exec",
        prompt="Approve exec?",
        decision_reason="exec requires approval",
        session_key="websocket:chat-1",
        idempotency_key="tool_approval:exec:test",
        now=NOW,
    )

    assert second.confirmation_id == first.confirmation_id
    assert len(manager.list_tool_approvals(session_key="websocket:chat-1", now=NOW)) == 1


def test_from_dict_sanitizes_action_payload_before_construction():
    confirmation = ConfirmationRequest.from_dict(
        {
            "confirmation_id": "conf_payload",
            "kind": "action_confirmation",
            "status": "pending",
            "prompt": "confirm",
            "action": "set_light_power",
            "scope": "home.living_room.lighting.private_device_7f3a9c",
            "trigger": "user_initiated",
            "risk": "low",
            "requested_by": "alice",
            "decision_reason": "ok",
            "presence_status": "unknown",
            "related_fact_ids": [],
            "created_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
            "action_payload": {
                "token": "secret-token",
                "parameters": {"secret": "hidden", "power": "on"},
            },
        }
    )
    raw = str(confirmation.action_payload)

    assert "secret-token" not in raw
    assert "hidden" not in raw
    assert "power" in raw


def test_high_risk_confirmation_expires_quickly_and_persists_expired_on_resolve(tmp_path):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(request(risk="high"), decision(), now=NOW)

    assert confirmation is not None
    assert confirmation.expires_at == (NOW + timedelta(minutes=2)).isoformat()

    result = manager.resolve_user_reply(
        confirmation.confirmation_id,
        "yes",
        now=NOW + timedelta(minutes=3),
    )

    assert result.decision == "expired"
    assert manager.store.read_all()[0].status == "expired"


def test_resolve_expired_logs_expired_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_from_action_decision(request(risk="high"), decision(), now=NOW)

    manager.resolve_user_reply(
        confirmation.confirmation_id,
        "yes",
        now=NOW + timedelta(minutes=3),
    )

    assert [event.decision for event in audit.find_by_confirmation_id(
        confirmation.confirmation_id
    )] == ["created", "expired"]


def test_expire_old_atomically_marks_expired_items(tmp_path):
    manager = ConfirmationManager(tmp_path)
    manager.create_from_action_decision(request(risk="medium"), decision(), now=NOW)

    changed = manager.expire_old(now=NOW + timedelta(minutes=6))

    assert changed == 1
    assert manager.store.read_all()[0].status == "expired"


def test_expire_old_logs_expired_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_from_action_decision(request(risk="medium"), decision(), now=NOW)

    manager.expire_old(now=NOW + timedelta(minutes=6))

    assert [event.decision for event in audit.find_by_confirmation_id(
        confirmation.confirmation_id
    )] == ["created", "expired"]


@pytest.mark.parametrize(
    "reply",
    ["是", "可以", "继续", "确认", "就这次", "只是这次", "yes", "continue"],
)
def test_yes_replies_confirm_once(tmp_path, reply):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    result = manager.resolve_user_reply(confirmation.confirmation_id, reply, now=NOW)

    assert result.decision == "confirmed"
    assert result.applies_once is True
    assert manager.store.read_all()[0].status == "confirmed_once"


def test_yes_reply_logs_confirmed_once_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    manager.resolve_user_reply(confirmation.confirmation_id, "yes", now=NOW)

    assert [event.decision for event in audit.find_by_confirmation_id(
        confirmation.confirmation_id
    )] == ["created", "confirmed_once"]


@pytest.mark.parametrize("reply", ["不", "不可以", "不是", "取消", "不要", "别执行", "no", "cancel"])
def test_no_replies_reject(tmp_path, reply):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    result = manager.resolve_user_reply(confirmation.confirmation_id, reply, now=NOW)

    assert result.decision == "rejected"
    assert manager.store.read_all()[0].status == "rejected"


def test_no_reply_logs_rejected_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    manager.resolve_user_reply(confirmation.confirmation_id, "no", now=NOW)

    assert [event.decision for event in audit.find_by_confirmation_id(
        confirmation.confirmation_id
    )] == ["created", "rejected"]


@pytest.mark.parametrize("reply", ["以后都这样", "设为规则", "以后不用问", "always", "remember this"])
def test_persistent_language_creates_persistent_rule(tmp_path, reply):
    """方案 C3: 持久授权现在被支持（旧版本返回 unclear，已变更）。

    需求来源：用户明确要求支持"以后都这样"持久授权。
    受规则 18 安全边界分级限制：exec/cron/spawn 仍会被 grant_store 降级为单次。
    """
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    result = manager.resolve_user_reply(confirmation.confirmation_id, reply, now=NOW)

    assert result.decision == "persistent"
    assert "persistent" in result.reason
    assert manager.store.read_all()[0].status == "confirmed_persistent"


def test_unclear_reply_leaves_confirmation_pending(tmp_path):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    result = manager.resolve_user_reply(confirmation.confirmation_id, "hmm maybe", now=NOW)

    assert result.decision == "unclear"
    assert manager.store.read_all()[0].status == "pending"


def test_unclear_reply_logs_unclear_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    manager.resolve_user_reply(confirmation.confirmation_id, "maybe", now=NOW)

    assert [event.decision for event in audit.find_by_confirmation_id(
        confirmation.confirmation_id
    )] == ["created", "unclear_reply"]


@pytest.mark.parametrize("reply", ["不是的", "不可以吧"])
def test_negative_chinese_substrings_never_confirm(tmp_path, reply):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    result = manager.resolve_user_reply(confirmation.confirmation_id, reply, now=NOW)

    assert result.decision in {"rejected", "unclear"}
    assert result.decision != "confirmed"
    assert manager.store.read_all()[0].status == "pending"


def test_prompt_hides_fact_ids_cursors_json_and_source_excerpt(tmp_path):
    manager = ConfirmationManager(tmp_path)
    fact_id = "fact_secret123"
    source_excerpt = '{"source_cursor": 123, "raw": "door log details"}'
    confirmation = manager.create_from_action_decision(
        request(),
        decision(
            reason=f"pending fact {fact_id} from cursor 123 {source_excerpt}",
            pending_facts=[fact_id],
        ),
        now=NOW,
    )

    assert confirmation is not None
    assert fact_id in confirmation.related_fact_ids
    assert fact_id not in confirmation.prompt
    assert "cursor" not in confirmation.prompt.casefold()
    assert "{" not in confirmation.prompt
    assert "door log details" not in confirmation.prompt


def test_prompt_and_reason_length_caps_and_redaction(tmp_path):
    manager = ConfirmationManager(tmp_path)
    long_reason = (
        "x" * 3000
        + " alice@example.com Bearer abcdefghijklmnop sk-proj_abcdefghijklmnopqrstuvwxyz"
    )
    confirmation = manager.create_from_action_decision(
        request(),
        decision(reason=long_reason),
        now=NOW,
    )

    assert confirmation is not None
    assert len(confirmation.prompt) <= PROMPT_MAX_CHARS
    assert len(confirmation.decision_reason) <= REASON_MAX_CHARS
    assert "alice@example.com" not in confirmation.decision_reason
    assert "Bearer abcdefghijklmnop" not in confirmation.decision_reason
    assert "sk-proj_" not in confirmation.decision_reason


def test_metadata_sanitization_removes_sensitive_evidence_fields(tmp_path):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(
        request(),
        decision(),
        now=NOW,
        metadata={
            "mac": "aa:bb",
            "ip": "192.0.2.10",
            "raw_audio": "bytes",
            "safe": "ok",
            "note": "alice@example.com used Bearer abcdefghijklmnop",
            "comment": "token=supersecretvalue and ghp_abcdefghijklmnopqrstuvwxyz123",
        },
    )

    assert confirmation is not None
    assert confirmation.metadata == {
        "safe": "ok",
        "note": "[REDACTED_EMAIL] used [REDACTED_BEARER_TOKEN]",
        "comment": "token=[REDACTED_SECRET] and [REDACTED_SECRET]",
    }
    raw = json.loads(manager.store.pending_file.read_text(encoding="utf-8"))
    assert "aa:bb" not in json.dumps(raw)
    assert "192.0.2.10" not in json.dumps(raw)
    assert "bytes" not in json.dumps(raw)
    assert "alice@example.com" not in json.dumps(raw)
    assert "Bearer abcdefghijklmnop" not in json.dumps(raw)
    assert "supersecretvalue" not in json.dumps(raw)
    assert "ghp_" not in json.dumps(raw)


def test_action_snapshot_fields_are_sanitized_before_storage(tmp_path):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_from_action_decision(
        request(requires_presence_empty=True, uses_facts=["fact_policy"]),
        decision(),
        now=NOW,
        action_payload={
            "token": "secret-token",
            "mac": "aa:bb",
            "note": "password=supersecretvalue",
            "nested": {"raw_audio": "bytes", "safe": "ok"},
        },
        idempotency_key="idem-1",
    )

    assert confirmation is not None
    assert confirmation.requires_presence_empty is True
    assert confirmation.uses_facts == ["fact_policy"]
    assert confirmation.action_payload == {
        "note": "password=[REDACTED_SECRET]",
        "nested": '{"safe":"ok"}',
    }
    assert confirmation.idempotency_key == "idem-1"
    assert confirmation.consumed_at is None
    raw = manager.store.pending_file.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    assert "aa:bb" not in raw
    assert "raw_audio" not in raw
    assert "supersecretvalue" not in raw


def test_claim_consumption_logs_consumed_event(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)
    manager.resolve_user_reply(confirmation.confirmation_id, "yes", now=NOW)

    claimed = manager.claim_consumption(confirmation.confirmation_id, now=NOW)

    assert claimed is not None
    assert [event.decision for event in audit.find_by_confirmation_id(
        confirmation.confirmation_id
    )] == ["created", "confirmed_once", "consumed"]


def test_claim_consumption_supports_tool_approval_kind(tmp_path):
    manager = ConfirmationManager(tmp_path)
    confirmation = manager.create_tool_approval(
        tool_name="originagent_evolution_control",
        prompt="Approve write?",
        decision_reason="write needs approval",
        requested_by="alice",
        metadata={"action_kind": "suppress_signal", "target_id": "signal-1", "reason_digest": "abcd"},
        now=NOW,
    )
    manager.resolve_user_reply(confirmation.confirmation_id, "yes", now=NOW)

    claimed = manager.claim_consumption(
        confirmation.confirmation_id,
        now=NOW,
        kinds=("tool_approval",),
    )

    assert claimed is not None
    assert claimed.kind == "tool_approval"


def test_retry_confirmation_creates_new_pending_copy_and_audits(tmp_path):
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="Approve exec?",
        decision_reason="exec approval",
        requested_by="alice",
        now=NOW,
    )
    manager.expire_old(now=NOW + timedelta(minutes=6))

    retried = manager.retry_confirmation(
        confirmation.confirmation_id,
        requested_by="alice",
        now=NOW + timedelta(minutes=6, seconds=1),
    )

    assert retried.confirmation_id != confirmation.confirmation_id
    assert retried.status == "pending"
    assert retried.metadata["retried_from_confirmation_id"] == confirmation.confirmation_id
    assert retried.metadata["retry_root_confirmation_id"] == confirmation.confirmation_id
    assert retried.metadata["retry_count"] == "1"
    assert [event.decision for event in audit.find_by_confirmation_id(retried.confirmation_id)] == ["retried"]


def test_retry_confirmation_enforces_limit(tmp_path):
    manager = ConfirmationManager(tmp_path)
    original = manager.create_tool_approval(
        tool_name="exec",
        prompt="Approve exec?",
        decision_reason="exec approval",
        requested_by="alice",
        now=NOW,
    )
    manager.expire_old(now=NOW + timedelta(minutes=6))
    current = original
    for step in range(3):
        current = manager.retry_confirmation(
            current.confirmation_id,
            requested_by="alice",
            now=NOW + timedelta(minutes=6 + step, seconds=step + 1),
        )
        manager.expire_old(now=NOW + timedelta(minutes=12 + step))

    with pytest.raises(ValueError, match="retry_limit_reached"):
        manager.retry_confirmation(
            current.confirmation_id,
            requested_by="alice",
            now=NOW + timedelta(minutes=20),
        )


def test_audit_write_failure_does_not_change_confirmation_result(tmp_path):
    manager = ConfirmationManager(tmp_path, audit_logger=FailingAuditLogger())
    confirmation = manager.create_from_action_decision(request(), decision(), now=NOW)

    result = manager.resolve_user_reply(confirmation.confirmation_id, "yes", now=NOW)

    assert confirmation is not None
    assert result.decision == "confirmed"
    assert manager.store.read_all()[0].status == "confirmed_once"


def test_pending_confirmations_store_uses_lock_and_atomic_writer(tmp_path, monkeypatch):
    store = PendingConfirmationStore(tmp_path)
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    def fake_write(path, content):
        events.append(("write", path, json.loads(content)))

    monkeypatch.setattr(store, "_locked", lambda: FakeLock())
    monkeypatch.setattr("OriginAgent.agent.confirmation._write_text_atomic", fake_write)

    store.write_all([
        ConfirmationRequest(
            confirmation_id="confirmation_test",
            kind="action_confirmation",
            status="pending",
            prompt="Continue?",
            action="unlock",
            scope="home.entry.lock",
            trigger="user_initiated",
            risk="high",
            requested_by=None,
            decision_reason="reason",
            presence_status="unknown",
            related_fact_ids=[],
            created_at=NOW.isoformat(),
            expires_at=(NOW + timedelta(minutes=2)).isoformat(),
        )
    ])

    assert events[0] == "enter"
    assert events[1][0] == "write"
    assert events[1][2]["confirmations"][0]["confirmation_id"] == "confirmation_test"
    assert events[2] == "exit"


def test_memory_workspace_snapshot_does_not_track_pending_confirmations(tmp_path):
    pending_file = tmp_path / "memory" / "pending_confirmations.json"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text('{"confirmations": []}\n', encoding="utf-8")
    snapshot = MemoryWorkspaceSnapshot(tmp_path)

    pending_file.write_text('{"confirmations": [{"confirmation_id": "live"}]}\n', encoding="utf-8")

    assert snapshot.restore() is True
    assert pending_file.read_text(encoding="utf-8") == (
        '{"confirmations": [{"confirmation_id": "live"}]}\n'
    )


def test_high_risk_unknown_presence_gate_creates_confirmation(tmp_path):
    presence = PresenceStore(tmp_path)
    facts = FactStore(tmp_path)
    gate = SmartHomeActionSafetyGate(presence, facts)
    manager = ConfirmationManager(tmp_path)
    action_request = request(risk="high", trigger="user_initiated")
    action_decision = gate.evaluate(action_request)

    confirmation = manager.create_from_action_decision(
        action_request,
        action_decision,
        now=NOW,
    )

    assert action_decision.decision == "ask_confirmation"
    assert confirmation is not None
    assert confirmation.status == "pending"


def test_medium_scheduled_unknown_gate_deny_creates_no_confirmation(tmp_path):
    presence = PresenceStore(tmp_path)
    facts = FactStore(tmp_path)
    gate = SmartHomeActionSafetyGate(presence, facts)
    manager = ConfirmationManager(tmp_path)
    action_request = request(
        action="set_temperature",
        scope="home.hvac",
        trigger="scheduled",
        risk="medium",
    )
    action_decision = gate.evaluate(action_request)

    confirmation = manager.create_from_action_decision(
        action_request,
        action_decision,
        now=NOW,
    )

    assert action_decision.decision == "deny"
    assert confirmation is None
    assert manager.store.read_all() == []


# ─── 方案 C：自然语言授权 + 持久授权 ───────────────────────────


def test_classify_reply_recognizes_natural_chinese_confirmations(tmp_path):
    """方案 C1: 扩充自然语言同义词。

    用户可以用"好的，授权吧"、"行，做吧"、"批准"、"没问题"等自然语言回复。
    """
    from OriginAgent.agent.confirmation import classify_confirmation_reply

    natural_confirmations = [
        "好的，授权吧",
        "行，做吧",
        "批准",
        "没问题",
        "OK",
        "准了",
        "同意",
        "可以的",
        "去吧",
        "嗯，可以",
        "好",
        "行",
        "可以",
        "yes, go ahead",
        "approve",
        "授权",
    ]
    for reply in natural_confirmations:
        result = classify_confirmation_reply(reply)
        assert result == "confirmed", f"Expected 'confirmed' for reply={reply!r}, got {result!r}"


def test_classify_reply_recognizes_natural_rejections(tmp_path):
    """方案 C1: 自然语言拒绝识别。

    "不行"、"别"、"算了"、"先不要"等都应识别为 rejected。
    """
    from OriginAgent.agent.confirmation import classify_confirmation_reply

    natural_rejections = [
        "不行",
        "别",
        "算了",
        "先不要",
        "不要",
        "不批准",
        "不同意",
        "拒绝",
        "no, stop",
        "别执行",
        "取消",
        "先别",
    ]
    for reply in natural_rejections:
        result = classify_confirmation_reply(reply)
        assert result == "rejected", f"Expected 'rejected' for reply={reply!r}, got {result!r}"


def test_classify_reply_parses_persistent_with_ttl(tmp_path):
    """方案 C1: 持久授权 + TTL 解析。

    "以后都这样 3 天" → persistent + ttl_seconds=259200
    "always 7 days" → persistent + ttl_seconds=604800
    "以后不用问" → persistent + ttl_seconds=None（使用默认）
    """
    from OriginAgent.agent.confirmation import classify_confirmation_reply_with_ttl

    # 带 TTL 的持久授权
    cases = [
        ("以后都这样 3 天", "persistent", 3 * 24 * 3600),
        ("以后都这样 1 天", "persistent", 24 * 3600),
        ("always 7 days", "persistent", 7 * 24 * 3600),
        ("以后都这样 12 小时", "persistent", 12 * 3600),
        ("以后不用问", "persistent", None),  # 默认 TTL
        ("以后都这样", "persistent", None),
    ]
    for reply, expected_decision, expected_ttl in cases:
        decision, ttl_seconds = classify_confirmation_reply_with_ttl(reply)
        assert decision == expected_decision, (
            f"Expected decision={expected_decision!r} for reply={reply!r}, got {decision!r}"
        )
        assert ttl_seconds == expected_ttl, (
            f"Expected ttl_seconds={expected_ttl!r} for reply={reply!r}, got {ttl_seconds!r}"
        )


def test_classify_reply_ambiguous_falls_back_to_unclear(tmp_path):
    """方案 C1: 含糊回复默认 unclear（不静默同意，规则 18 安全边界）。"""
    from OriginAgent.agent.confirmation import classify_confirmation_reply

    ambiguous_replies = [
        "可能吧",
        "不确定",
        "你说呢",
        "看看情况",
        "再想想",
        "你来定吧",  # 委托型回复，规则 18 安全边界从严判 unclear
        "",
        "   ",
    ]
    for reply in ambiguous_replies:
        result = classify_confirmation_reply(reply)
        assert result == "unclear", f"Expected 'unclear' for reply={reply!r}, got {result!r}"


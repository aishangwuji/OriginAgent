"""Tests for ``Session.get_hot_history`` — turn-count-bounded history retrieval."""

from datetime import datetime

from OriginAgent.session.manager import Session


def _fresh_session() -> Session:
    """Return a clean session with no messages or episodes."""
    return Session(key="test:hot_history")


def _add_simple_turn(s: Session, idx: int) -> None:
    """Append a simple user + assistant turn (2 messages)."""
    s.messages.append({
        "role": "user",
        "content": f"user {idx}",
        "timestamp": datetime.now().isoformat(),
    })
    s.messages.append({
        "role": "assistant",
        "content": f"assistant {idx}",
        "timestamp": datetime.now().isoformat(),
    })


def _tool_call(cid: str) -> dict:
    return {"id": cid, "type": "function", "function": {"name": "f", "arguments": "{}"}}


class TestGetHotHistory:
    def test_returns_last_n_turns(self):
        """53 轮取最后 50 轮：106 条消息 → 100 条，边界对齐到 user 轮。"""
        s = _fresh_session()
        for i in range(53):
            _add_simple_turn(s, i)

        hist = s.get_hot_history(max_turns=50)

        # 50 轮 × (user + assistant) = 100 条。
        assert len(hist) == 100
        # 起点对齐到第 3 轮（53 - 50 = 3），不得从 assistant 中间截断。
        assert hist[0]["content"] == "user 3"
        assert hist[1]["content"] == "assistant 3"
        assert hist[-1]["content"] == "assistant 52"

    def test_preserves_tool_call_integrity(self):
        """含 3 个 tool_call + 3 个 tool_result 的轮次落在边界上时，配对完整保留。"""
        s = _fresh_session()
        # 49 个普通轮次。
        for i in range(49):
            _add_simple_turn(s, i)

        # 第 49 轮：user → assistant(3 tool_calls) → 3 tool_results → assistant 收尾。
        s.messages.append({"role": "user", "content": "user 49", "timestamp": datetime.now().isoformat()})
        s.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [_tool_call("call_1"), _tool_call("call_2"), _tool_call("call_3")],
            "timestamp": datetime.now().isoformat(),
        })
        for tid in ("call_1", "call_2", "call_3"):
            s.messages.append({
                "role": "tool",
                "content": f"result {tid}",
                "tool_call_id": tid,
                "timestamp": datetime.now().isoformat(),
            })
        s.messages.append({"role": "assistant", "content": "assistant 49 final", "timestamp": datetime.now().isoformat()})

        # 再补 2 个普通轮次。
        _add_simple_turn(s, 50)
        _add_simple_turn(s, 51)

        # 取最后 3 轮 → 边界正好落在第 49 轮（含 tool_call 的轮）。
        hist = s.get_hot_history(max_turns=3)

        # 3 个 tool_call 全部保留。
        callers = [m for m in hist if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(callers) == 1
        call_ids = {tc["id"] for tc in callers[0]["tool_calls"]}
        assert call_ids == {"call_1", "call_2", "call_3"}

        # 3 个 tool_result 全部保留，且与 tool_call 一一配对。
        results = [m for m in hist if m.get("role") == "tool"]
        assert len(results) == 3
        result_ids = {m["tool_call_id"] for m in results}
        assert result_ids == {"call_1", "call_2", "call_3"}
        assert result_ids <= call_ids

    def test_extends_boundary_for_orphan_tool_results(self):
        """截断点落在 tool_call 序列中间时，向前扩展到上一个 user 轮。"""
        s = _fresh_session()
        # 第 0 轮：user → assistant 发起 3 个 tool_call（结果尚未返回）。
        s.messages.append({"role": "user", "content": "user 0", "timestamp": datetime.now().isoformat()})
        s.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [_tool_call("orphan_1"), _tool_call("orphan_2"), _tool_call("orphan_3")],
            "timestamp": datetime.now().isoformat(),
        })
        # 第 1 轮：tool_result 落在 user 1 之后（跨轮），朴素边界会使其成为孤儿。
        s.messages.append({"role": "user", "content": "user 1", "timestamp": datetime.now().isoformat()})
        s.messages.append({"role": "tool", "content": "r1", "tool_call_id": "orphan_1", "timestamp": datetime.now().isoformat()})
        s.messages.append({"role": "tool", "content": "r2", "tool_call_id": "orphan_2", "timestamp": datetime.now().isoformat()})
        s.messages.append({"role": "tool", "content": "r3", "tool_call_id": "orphan_3", "timestamp": datetime.now().isoformat()})
        s.messages.append({"role": "assistant", "content": "assistant 1", "timestamp": datetime.now().isoformat()})
        # 第 2 轮。
        s.messages.append({"role": "user", "content": "user 2", "timestamp": datetime.now().isoformat()})
        s.messages.append({"role": "assistant", "content": "assistant 2", "timestamp": datetime.now().isoformat()})

        # max_turns=2 → 朴素起点为 user 1，会把 3 个 tool_result 孤儿化。
        # 边界须向前扩展到 user 0，使 tool_call 与 tool_result 保持配对。
        hist = s.get_hot_history(max_turns=2)

        call_ids: set[str] = set()
        for m in hist:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                call_ids.update(tc["id"] for tc in m["tool_calls"])
        result_ids = {m["tool_call_id"] for m in hist if m.get("role") == "tool"}

        # 3 个 tool_result 全部保留，且其声明者 assistant 也被拉回。
        assert result_ids == {"orphan_1", "orphan_2", "orphan_3"}
        assert result_ids <= call_ids
        # 扩展把第 0 轮纳入返回结果。
        assert hist[0]["content"] == "user 0"

    def test_returns_all_when_fewer_than_max_turns(self):
        """不足 max_turns 轮时全部返回：10 轮 → 20 条消息。"""
        s = _fresh_session()
        for i in range(10):
            _add_simple_turn(s, i)

        hist = s.get_hot_history(max_turns=50)

        assert len(hist) == 20
        assert hist[0]["content"] == "user 0"
        assert hist[-1]["content"] == "assistant 9"

    def test_empty_session_returns_empty(self):
        """空 session 返回空列表。"""
        s = _fresh_session()
        assert s.get_hot_history() == []
        assert s.get_hot_history(max_turns=10) == []

    def test_respects_max_tokens(self, monkeypatch):
        """max_tokens 从尾部按 token 预算截断，并对齐到首个可见 user 轮。"""
        s = _fresh_session()
        for i in range(5):
            _add_simple_turn(s, i)

        token_map = {f"user {i}": 100 for i in range(5)}
        token_map.update({f"assistant {i}": 100 for i in range(5)})
        monkeypatch.setattr(
            "OriginAgent.session.manager.estimate_message_tokens",
            lambda message: token_map.get(message.get("content"), 0),
        )

        # 预算 250：尾部 user4 + assistant4 共 200，再加一条会超 250。
        hist = s.get_hot_history(max_turns=50, max_tokens=250)
        assert [m["content"] for m in hist] == ["user 4", "assistant 4"]

"""温区缓冲区管理器（session-scoped warm buffer）。

温区（warm store）是介于热区（session.messages，当前对话上下文）与冷区
（持久化归档 / 记忆库）之间的会话级缓冲层。它会累积最近若干轮 user/assistant
来回，达到阈值后由上层调用 ``drain()`` 取出整批消息归档到冷区，从而：

- 控制热区上下文长度，避免 token 膨胀；
- 以"轮"为单位批量归档，比逐条归档更高效；
- 保持跨轮次的局部连续性，便于后续检索 / 摘要。

``WarmStore`` 自身不持有任何状态，所有状态都通过
``session.metadata["warm_buffer"]`` 持久化，因此天然支持多会话隔离与重启恢复。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from OriginAgent.session.manager import Session

# 温区状态在 session.metadata 中的键名，与 working_memory_v1 等约定保持一致。
WARM_BUFFER_METADATA_KEY = "warm_buffer"


def _utcnow_iso() -> str:
    """返回 UTC 时间的 ISO8601 字符串，用作 updated_at 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def _empty_buffer() -> dict[str, Any]:
    """构造一个空的温区缓冲区结构。"""
    return {
        "messages": [],
        "turn_count": 0,
        "updated_at": _utcnow_iso(),
    }


class WarmStore:
    """温区缓冲区管理器：session-scoped 的对话轮次缓冲层。

    设计要点：
    - 无状态：所有数据落在 ``session.metadata[WARM_BUFFER_METADATA_KEY]``，
      实例本身可被任意复用，天然支持多会话隔离。
    - 以"轮"为单位：一次 ``append`` 对应一轮 user + assistant 来回，
      ``turn_count`` 记录当前累积轮次。
    - drain 后清空：取出消息同时清空缓冲区，避免重复归档。
    """

    def load(self, session: Session) -> dict[str, Any]:
        """从 session.metadata 加载温区状态。

        空 session 或缺失键时返回一个全新的空缓冲区（不写入 session），
        调用方可安全读取 ``messages`` / ``turn_count`` / ``updated_at`` 字段。
        """
        raw = session.metadata.get(WARM_BUFFER_METADATA_KEY)
        if not isinstance(raw, dict):
            return _empty_buffer()
        # 防御性归一化：历史数据可能字段缺失或类型不一致
        return {
            "messages": list(raw.get("messages") or []),
            "turn_count": int(raw.get("turn_count") or 0),
            "updated_at": str(raw.get("updated_at") or _utcnow_iso()),
        }

    def save(self, session: Session, buffer: dict[str, Any]) -> None:
        """保存温区状态到 session.metadata。"""
        session.metadata[WARM_BUFFER_METADATA_KEY] = buffer

    def append(
        self,
        session: Session,
        user_msg: dict[str, Any],
        assistant_msgs: list[dict[str, Any]],
    ) -> int:
        """追加一轮对话到温区，返回当前温区轮次。

        Args:
            session: 当前会话，温区状态从其 metadata 读写。
            user_msg: 一条 user message。
            assistant_msgs: 对应的 assistant 响应消息列表
                （可能含 tool_calls / tool_results 等多条消息）。

        Returns:
            追加完成后的温区轮次计数。
        """
        buffer = self.load(session)
        # 先追加 user，再追加 assistant 序列，保持对话顺序
        messages = buffer["messages"]
        messages.append(user_msg)
        for msg in assistant_msgs:
            messages.append(msg)
        buffer["turn_count"] = int(buffer.get("turn_count", 0)) + 1
        buffer["updated_at"] = _utcnow_iso()
        self.save(session, buffer)
        return buffer["turn_count"]

    def is_full(self, session: Session, max_turns: int = 50) -> bool:
        """检查温区是否已满 ``max_turns`` 轮。"""
        buffer = self.load(session)
        return int(buffer.get("turn_count", 0)) >= max_turns

    def drain(self, session: Session) -> list[dict[str, Any]]:
        """取出温区全部消息并清空缓冲区，返回取出的消息列表。

        若温区为空则返回空列表。清空操作通过覆盖为空缓冲区完成，
        保证后续 ``load()`` 仍能返回结构合法的字典。
        """
        buffer = self.load(session)
        messages = list(buffer.get("messages") or [])
        self.save(session, _empty_buffer())
        return messages

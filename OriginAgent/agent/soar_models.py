"""Soar 认知架构数据模型 — 障碍、子目标、子目标堆栈。

本模块实现 Soar 的"通用子目标"与"块化"概念的数据结构层：
- SoarObstacle: 结构化表示子代理遇到的障碍
- SoarSubgoal: 子目标堆栈帧（含障碍 + 解决路径）
- SoarSubgoalStack: LIFO 堆栈 + 原子持久化
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


def _utcnow_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SoarObstacle:
    """结构化表示子代理遇到的障碍。

    替代原有的字符串 failure_summary，提供可机器消费的障碍分类。
    """
    obstacle_id: str
    obstacle_type: str          # "tool_failure" | "internal_error" | "impasse"
    root_cause: str             # 障碍根因摘要（截断 240 字符）
    attempted_tools: list[str]  # 已尝试的工具名列表
    recoverable_hint: str = "unknown"  # "retry" | "reauth" | "replan" | "unknown"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", _utcnow_iso())

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SoarObstacle":
        return cls(
            obstacle_id=data["obstacle_id"],
            obstacle_type=data["obstacle_type"],
            root_cause=data.get("root_cause", ""),
            attempted_tools=list(data.get("attempted_tools", [])),
            recoverable_hint=data.get("recoverable_hint", "unknown"),
            created_at=data.get("created_at", ""),
        )


@dataclass(frozen=True)
class SoarSubgoal:
    """子目标堆栈帧 — 关联一个障碍及其解决路径。

    当主代理遇到障碍时 push，子代理解决障碍后 pop，
    solution_path 被填充用于 Soar 块化（chunking）。
    """
    subgoal_id: str
    parent_goal_id: str | None          # 父目标 ID（None 表示顶层）
    obstacle: SoarObstacle              # 触发此子目标的障碍
    working_state_snapshot: dict[str, Any]  # push 时的工作记忆切片
    subagent_task_id: str | None = None # 关联的子代理任务 ID
    created_at: str = ""
    solution_path: dict[str, Any] | None = None  # 解决路径（pop 时填充）

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", _utcnow_iso())

    def with_solution(self, solution: dict[str, Any]) -> "SoarSubgoal":
        """填充解决路径，返回新实例（frozen dataclass）。"""
        from dataclasses import replace
        return replace(self, solution_path=solution)

    def to_json(self) -> dict[str, Any]:
        return {
            "subgoal_id": self.subgoal_id,
            "parent_goal_id": self.parent_goal_id,
            "obstacle": self.obstacle.to_json(),
            "working_state_snapshot": dict(self.working_state_snapshot),
            "subagent_task_id": self.subagent_task_id,
            "created_at": self.created_at,
            "solution_path": self.solution_path,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SoarSubgoal":
        return cls(
            subgoal_id=data["subgoal_id"],
            parent_goal_id=data.get("parent_goal_id"),
            obstacle=SoarObstacle.from_json(data["obstacle"]),
            working_state_snapshot=dict(data.get("working_state_snapshot", {})),
            subagent_task_id=data.get("subagent_task_id"),
            created_at=data.get("created_at", ""),
            solution_path=data.get("solution_path"),
        )


class SoarSubgoalStack:
    """Soar 子目标 LIFO 堆栈 + 原子持久化。

    复用 IntentionStack 的持久化模式（tempfile + os.replace）。
    服务重启后从 soar_subgoal_stack.jsonl 重建栈。
    """

    def __init__(self, max_depth: int = 10) -> None:
        self._frames: list[SoarSubgoal] = []
        self.max_depth = max_depth

    def push(self, frame: SoarSubgoal) -> None:
        """压入子目标帧。超深时抛 OverflowError。"""
        if len(self._frames) >= self.max_depth:
            raise OverflowError(f"SoarSubgoalStack overflow: max depth {self.max_depth} reached")
        self._frames.append(frame)

    def pop(self) -> SoarSubgoal | None:
        """弹出栈顶帧。栈空时返回 None。"""
        if not self._frames:
            return None
        return self._frames.pop()

    def peek(self) -> SoarSubgoal | None:
        """查看栈顶帧但不弹出。"""
        return self._frames[-1] if self._frames else None

    @property
    def depth(self) -> int:
        return len(self._frames)

    @property
    def is_empty(self) -> bool:
        return len(self._frames) == 0

    def find_by_subagent(self, subagent_task_id: str) -> SoarSubgoal | None:
        """按子代理任务 ID 查找栈帧。"""
        for frame in reversed(self._frames):
            if frame.subagent_task_id == subagent_task_id:
                return frame
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "frames": [f.to_json() for f in self._frames],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SoarSubgoalStack":
        stack = cls(max_depth=data.get("max_depth", 10))
        for frame_data in data.get("frames", []):
            stack._frames.append(SoarSubgoal.from_json(frame_data))
        return stack

    def persist_to(self, path: Path) -> None:
        """原子写入栈到 JSON 文件。best-effort，失败仅记 warning。"""
        import json
        import os
        import tempfile

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(path.parent),
                delete=False, suffix=".tmp",
            )
            try:
                json.dump(self.to_json(), tmp, ensure_ascii=False)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, str(path))
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise
        except Exception:
            logger.opt(exception=True).warning("SoarSubgoalStack: persist failed to {}", path)

    @classmethod
    def load_from(cls, path: Path) -> "SoarSubgoalStack":
        """从 JSON 文件重建栈。文件不存在或损坏时返回空栈。"""
        import json

        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_json(data)
        except Exception:
            logger.opt(exception=True).warning(
                "SoarSubgoalStack: corrupt stack file {}, starting with empty stack", path,
            )
            return cls()

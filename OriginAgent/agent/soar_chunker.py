"""SoarChunker — 将"障碍 + 解决路径"压缩为技能候选。

当子代理成功解决障碍后，SoarChunker 从 SubagentToolRecord 提取有序工具序列，
构造 ActionTraceDigest(correction_flag=True) 并调用 SkillBootstrapper.ingest_chunk()，
实现在线即时技能形成（Soar chunking）。
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from OriginAgent.agent.soar_models import SoarObstacle, SoarSubgoal
from OriginAgent.agent.skill_bootstrapper import SkillBootstrapper
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest


class SoarChunker:
    """将子代理的成功解决路径压缩为技能候选。

    依赖：
    - SkillBootstrapper: 在线 ingest digest 并在阈值达到时 compile
    - tool_record_store: 提供 SubagentToolRecord 查询接口
    """

    def __init__(
        self,
        bootstrapper: SkillBootstrapper,
        tool_record_store: Any | None = None,
    ) -> None:
        self._bootstrapper = bootstrapper
        self._tool_record_store = tool_record_store

    async def chunk(self, subgoal: SoarSubgoal) -> Any | None:
        """将已解决的 SoarSubgoal 压缩为技能候选。

        Args:
            subgoal: 已填充 solution_path 的 SoarSubgoal

        Returns:
            SkillCandidate | None（由 SkillBootstrapper.ingest_chunk 返回）
        """
        # 从 tool_record_store 提取工具序列
        tool_sequence = await self._extract_tool_sequence(subgoal.subagent_task_id)

        # 从 solution_path 或 obstacle 提取 param_preview
        param_preview = self._extract_param_preview(subgoal)

        # 构造 ActionTraceDigest
        digest = ActionTraceDigest(
            digest_id=f"soar_{uuid.uuid4().hex[:12]}",
            session_key=subgoal.working_state_snapshot.get("session_key", ""),
            tool_sequence=tool_sequence,
            param_preview=param_preview[:200],  # 截断到 200 字符
            correction_flag=True,  # Soar chunk 总是标记为含修正
            task_reference=subgoal.obstacle.root_cause[:200],
            created_at=subgoal.created_at,
        )

        logger.debug(
            "SoarChunker: chunking subgoal={} tools={} -> digest={}",
            subgoal.subgoal_id, tool_sequence, digest.digest_id,
        )

        return self._bootstrapper.ingest_chunk(digest)

    async def chunk_from_success(
        self,
        subagent_task_id: str,
        obstacle: SoarObstacle,
        result_summary: str,
        session_key: str = "",
    ) -> Any | None:
        """便捷方法：当无显式 SoarSubgoal 时，从成功结果直接 chunk。

        Args:
            subagent_task_id: 子代理任务 ID
            obstacle: 该任务解决的障碍
            result_summary: 子代理最终结果摘要
            session_key: 会话键

        Returns:
            SkillCandidate | None
        """
        tool_sequence = await self._extract_tool_sequence(subagent_task_id)

        digest = ActionTraceDigest(
            digest_id=f"soar_{uuid.uuid4().hex[:12]}",
            session_key=session_key,
            tool_sequence=tool_sequence,
            param_preview=result_summary[:200],
            correction_flag=True,
            task_reference=obstacle.root_cause[:200],
            created_at=obstacle.created_at,
        )

        logger.debug(
            "SoarChunker: chunk_from_success task={} tools={} -> digest={}",
            subagent_task_id, tool_sequence, digest.digest_id,
        )

        return self._bootstrapper.ingest_chunk(digest)

    async def _extract_tool_sequence(self, subagent_task_id: str | None) -> list[str]:
        """从 tool_record_store 提取按时间排序的工具名列表。

        无 task_id 或 store 不可用时返回空列表。
        """
        if not subagent_task_id or self._tool_record_store is None:
            return []

        try:
            # 尝试不同的查询 API（list_by_task / recent_for_task / list_for_task）
            records: list[Any] = []
            for method_name in ("list_by_task", "recent_for_task", "list_for_task", "get_by_task"):
                method = getattr(self._tool_record_store, method_name, None)
                if method is not None:
                    result = method(subagent_task_id)
                    # 处理同步/异步返回
                    if hasattr(result, "__await__"):
                        result = await result
                    records = list(result)
                    break

            # 按 started_at 排序，提取工具名
            def sort_key(r: Any) -> str:
                return getattr(r, "started_at", "") or ""

            records.sort(key=sort_key)

            # 提取工具名（去重但保持顺序）
            seen: set[str] = set()
            tools: list[str] = []
            for r in records:
                name = getattr(r, "tool_name", None) or getattr(r, "name", None)
                if name and name not in seen:
                    seen.add(name)
                    tools.append(name)
            return tools

        except Exception:
            logger.opt(exception=True).debug(
                "SoarChunker: failed to extract tool sequence for task={}", subagent_task_id,
            )
            return []

    @staticmethod
    def _extract_param_preview(subgoal: SoarSubgoal) -> str:
        """从 SoarSubgoal 的 solution_path 或 obstacle 提取参数预览。"""
        if subgoal.solution_path and isinstance(subgoal.solution_path, dict):
            # 优先用 solution_path 中的 result / summary
            result = subgoal.solution_path.get("result") or subgoal.solution_path.get("summary")
            if result:
                return str(result)[:200]
        # 回退到 obstacle 的 root_cause
        return subgoal.obstacle.root_cause[:200]

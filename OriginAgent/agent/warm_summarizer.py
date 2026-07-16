"""温区总结器：调用辅助 LLM 对温区历史轮次生成结构化 JSON 总结。

温区存放 50~100 轮之外的较旧对话。当对话推进到下一批 50 轮时，
``WarmSummarizer`` 将这批温区消息压缩为结构化摘要，供后续检索与长期记忆使用。

设计要点：总结时同时注入热区（最近 50 轮）上下文，避免"孤立片段"式的失真总结——
温区尾部与热区首部语义相连，缺少热区会让 LLM 误判边界话题的归属与状态。
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from OriginAgent.agent.auxiliary_llm import AuxiliaryLLMRouter, call_llm
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.providers.base import LLMResponse
from OriginAgent.utils.constants import RoleConstants


class WarmSummarizer:
    """温区总结器：把温区消息压缩为结构化 JSON 摘要。"""

    def __init__(
        self,
        *,
        auxiliary_router: AuxiliaryLLMRouter | None,
        workspace: Path,
        output_language: str | None = None,
    ) -> None:
        self.auxiliary_router = auxiliary_router
        self.workspace = Path(workspace)
        # 摘要产出语言；None 时跟随 LLM 默认（通常与对话语言一致）
        self.output_language = output_language or None

    async def summarize(
        self,
        warm_messages: list[dict],
        hot_messages: list[dict],
        *,
        turn_range: str,
        session_key: str,
    ) -> dict | None:
        """调用 LLM 生成结构化 JSON 总结。

        prompt 包含温区 50 轮 + 热区 50 轮的完整内容，确保总结结合热区上下文。
        返回结构化 JSON，失败时返回 None（不抛异常）。
        """
        prompt = self._build_prompt(
            warm_messages=warm_messages,
            hot_messages=hot_messages,
            turn_range=turn_range,
        )
        try:
            response = await call_llm(
                task="warm_summary",
                router=self.auxiliary_router,
                messages=[
                    {"role": RoleConstants.SYSTEM, "content": self._system_prompt()},
                    {"role": RoleConstants.USER, "content": prompt},
                ],
                tools=None,
                tool_choice=None,
                max_tokens=2048,
                temperature=0.1,
            )
        except Exception:
            # 调用层任何异常都不应阻断温区压缩流程，降级为 None
            logger.exception(
                "WarmSummarizer call_llm raised (session={}, range={})",
                session_key, turn_range,
            )
            return None

        return self._parse_summary(response, turn_range=turn_range, session_key=session_key)

    def _system_prompt(self) -> str:
        lines = [
            "你是一个对话总结器。请基于温区与热区对话生成结构化 JSON 总结。",
            "只输出纯 JSON，不要包含 markdown code fence 或任何解释性文字。",
        ]
        if self.output_language:
            lines.append(f"summary 等文本字段请使用 {self.output_language} 输出。")
        return "\n".join(lines)

    def _build_prompt(
        self,
        *,
        warm_messages: list[dict],
        hot_messages: list[dict],
        turn_range: str,
    ) -> str:
        warm_text = self._format_messages(warm_messages) or "(无)"
        hot_text = self._format_messages(hot_messages) or "(无)"
        schema = json.dumps(
            {
                "turn_range": turn_range,
                "summary": "一句话总结",
                "commitments": ["Agent 答应过的事"],
                "decisions": ["做出的决策"],
                "open_questions": ["悬而未决的问题"],
                "key_entities": ["关键实体"],
                "timestamp_range": {"start": "...", "end": "..."},
            },
            ensure_ascii=False,
            indent=2,
        )
        return (
            f"请总结以下温区对话（第{turn_range}轮），生成结构化 JSON。\n\n"
            f"## 温区对话（待总结）\n{warm_text}\n\n"
            f"## 热区上下文（最近 50 轮，用于理解连贯性）\n{hot_text}\n\n"
            f"## 输出要求\n"
            f"输出纯 JSON（不要 markdown code fence），包含以下字段：\n"
            f"{schema}"
        )

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """把消息列表渲染为 LLM 可读文本，保留 role/content 与时间戳。"""
        if not messages:
            return ""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = msg.get("timestamp") or msg.get("created_at") or ""
            prefix = f"[{ts}] " if ts else ""
            lines.append(f"{prefix}{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_summary(
        response: LLMResponse,
        *,
        turn_range: str,
        session_key: str,
    ) -> dict | None:
        """从 LLM 响应解析结构化摘要；空内容/非法 JSON 时优雅降级返回 None。"""
        # 错误响应直接放弃，避免把错误信息误当 JSON 解析
        if getattr(response, "finish_reason", None) == "error":
            logger.warning(
                "WarmSummarizer got error response (session={}, range={})",
                session_key, turn_range,
            )
            return None
        # 优先取 content；部分 reasoning 模型会把 JSON 放进 reasoning_content
        text = (getattr(response, "content", None) or "").strip()
        if not text:
            text = (getattr(response, "reasoning_content", None) or "").strip()
        if not text:
            logger.warning(
                "WarmSummarizer got empty content (session={}, range={})",
                session_key, turn_range,
            )
            return None
        # 复用 MetaCognitionReflector._load_json_payload：处理 code fence + 容错解析
        payload = MetaCognitionReflector._load_json_payload(text)
        if not isinstance(payload, dict):
            logger.warning(
                "WarmSummarizer got invalid JSON (session={}, range={}, preview={})",
                session_key, turn_range, text[:200],
            )
            return None
        # turn_range 是确定性输入，回填以保证业务数据标准化，避免 LLM 误填
        payload["turn_range"] = turn_range
        return payload

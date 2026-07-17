"""Tests for log_event attrs enrichment in RetrievalFusion.retrieve.

验证 `retrieval.fused` 事件包含以下 attrs：
- sources
- per_source_counts
- deduped_count
- trimmed_count
- final_block_count
- top_score
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from OriginAgent.agent.facts import FactRetrievalBundle, FactRetrievalResult, FactRecord
from OriginAgent.agent.retrieval_fusion import RetrievalFusion
from OriginAgent.config.schema import ContextConfig
from OriginAgent.memory.retrieval import NearlineRetrievalResult


def _fact_record(content: str, *, scope: str = "user.preference.theme") -> FactRecord:
    return FactRecord(
        fact_id="fact_1",
        content=content,
        canonical_key="fact.key",
        category="preference",
        scope=scope,
        owner="user",
        source_cursors=[1],
        source_excerpt="excerpt",
        confidence=0.9,
        status="active",
        created_at="2026-06-09T00:00:00+00:00",
        updated_at="2026-06-09T00:00:00+00:00",
        last_seen_at="2026-06-09T00:00:00+00:00",
        expires_at=None,
        supersedes_fact_id=None,
        requires_confirmation=False,
    )


def _build_fusion() -> RetrievalFusion:
    """构造一个多源命中的 RetrievalFusion，覆盖 4 个检索源（memory_candidates 为空）。"""
    fact = _fact_record("User prefers dark mode")
    bundle = FactRetrievalBundle(
        facts=[fact],
        rendered_text="## Long-term Memory\n- User prefers dark mode",
        fallback_used=False,
        retrievals=[FactRetrievalResult(fact=fact, score=0.95, source="semantic")],
    )
    memory = SimpleNamespace(
        fact_store=SimpleNamespace(retrieve_context_bundle=lambda scope_prefix=None: bundle),
        get_memory_context_bundle=lambda scope_prefix=None: bundle,
    )

    nearline = NearlineRetrievalResult(
        rendered_text="## Relevant Episodes\n- Prepare deployment checklist",
        episodes=[
            SimpleNamespace(
                episode_id="ep_1",
                summary="Prepare deployment checklist",
                content="Prepare deployment checklist",
                timestamp="2026-06-09T00:00:00+00:00",
                owner_id="user-1",
            )
        ],
    )
    nearline_memory = SimpleNamespace(retrieve=lambda query=None, recent_history=None: nearline)

    def _search(**kwargs):
        # memory_blocks 形态的调用返回空，让 memory_candidates 源无命中
        if kwargs.get("result_shape") == "memory_blocks":
            return {
                "results": [],
                "searched_sources": [],
                "mode": "hybrid",
                "performance_note": None,
                "index_stale": False,
                "index_refresh_running": False,
            }
        return {
            "results": [
                {
                    "source": "sessions",
                    "snippet": "Recent deployment notes",
                    "timestamp": "2026-06-09T00:00:00+00:00",
                    "locator": {"message_index": 0},
                    "score": 0.8,
                    "match_type": "semantic",
                }
            ],
            "searched_sources": ["sessions"],
            "mode": "hybrid",
            "performance_note": None,
            "index_stale": False,
            "index_refresh_running": False,
        }

    session_search = SimpleNamespace(search=_search)

    return RetrievalFusion(
        workspace=Path("."),
        memory=memory,
        nearline_memory=nearline_memory,
        session_search=session_search,
        context_config=ContextConfig(),
        nearline_memory_config=SimpleNamespace(enabled=True, pipeline_enabled=True),
    )


def _find_log_call(mock_log_event, event_name):
    """从 mock_log_event.call_args_list 中找出指定事件名的首次调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


def test_retrieval_fused_logs_source_counts():
    """retrieval.fused 事件应包含 sources / per_source_counts / deduped_count /
    trimmed_count / final_block_count / top_score。"""
    fusion = _build_fusion()

    prewarm_seed = [
        {
            "title": "recent_session:cli:other",
            "text": "Prewarm context for deployment",
            "scope": "session",
            "owner_id": "user-1",
            "timestamp": "2026-06-09T00:00:00+00:00",
            "details": {"device_id": "device-a"},
            "score": 0.65,
        }
    ]

    with patch("OriginAgent.agent.retrieval_fusion.log_event") as mock_log_event:
        result = fusion.retrieve(
            query="deployment",
            session_key="cli:test-session",
            runtime_context=SimpleNamespace(
                default_scope="session", user_id="user-1", device_id="device-a"
            ),
            current_message="deployment",
            recent_history=[],
            session_summary=None,
            prewarm_seed=prewarm_seed,
        )

    # 验证 retrieve 仍正常返回（不破坏既有契约）
    assert result is not None

    fused_call = _find_log_call(mock_log_event, "retrieval.fused")
    assert fused_call is not None, "retrieval.fused 未被记录"
    kwargs = fused_call.kwargs

    # 必需 attrs 存在性断言
    assert "sources" in kwargs, "retrieval.fused 缺少 sources"
    assert "per_source_counts" in kwargs, "retrieval.fused 缺少 per_source_counts"
    assert "deduped_count" in kwargs, "retrieval.fused 缺少 deduped_count"
    assert "trimmed_count" in kwargs, "retrieval.fused 缺少 trimmed_count"
    assert "final_block_count" in kwargs, "retrieval.fused 缺少 final_block_count"
    assert "top_score" in kwargs, "retrieval.fused 缺少 top_score"

    # session_key 应透传
    assert kwargs.get("session_key") == "cli:test-session"

    # sources 应包含所有检索源
    assert isinstance(kwargs["sources"], list)
    assert "fact_store" in kwargs["sources"]
    assert "prewarm_seed" in kwargs["sources"]
    assert "nearline_retrieval" in kwargs["sources"]
    assert "session_search" in kwargs["sources"]

    # per_source_counts 应为 dict[str, int]
    assert isinstance(kwargs["per_source_counts"], dict)
    assert kwargs["per_source_counts"]["fact_store"] == 1
    assert kwargs["per_source_counts"]["prewarm_seed"] == 1
    assert kwargs["per_source_counts"]["nearline_retrieval"] == 1
    assert kwargs["per_source_counts"]["session_search"] == 1
    assert kwargs["per_source_counts"]["memory_candidates"] == 0

    # deduped_count / trimmed_count 应为 int
    assert isinstance(kwargs["deduped_count"], int)
    assert isinstance(kwargs["trimmed_count"], int)
    # 各命中内容不同，无去重、无裁剪
    assert kwargs["deduped_count"] == 0
    assert kwargs["trimmed_count"] == 0

    # final_block_count 应等于返回的 blocks 数
    assert isinstance(kwargs["final_block_count"], int)
    assert kwargs["final_block_count"] == len(result.retrieved_blocks)
    assert kwargs["final_block_count"] >= 1

    # top_score 应为 float，且等于最大 score
    # 各源 score: fact_store=0.8, prewarm_seed=0.65, nearline=0.7, session_search=0.8
    assert isinstance(kwargs["top_score"], float)
    assert kwargs["top_score"] == 0.8

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def _fusion(*, fact_bundle: FactRetrievalBundle, nearline_result: NearlineRetrievalResult, search_rows: list[dict]):
    memory = SimpleNamespace(
        fact_store=SimpleNamespace(retrieve_context_bundle=lambda scope_prefix=None: fact_bundle),
        get_memory_context_bundle=lambda scope_prefix=None: fact_bundle,
    )
    nearline = SimpleNamespace(retrieve=lambda query=None, recent_history=None: nearline_result)
    session_search = SimpleNamespace(
        search=lambda **kwargs: {
            "results": search_rows,
            "searched_sources": ["sessions", "history", "webui"],
            "mode": "hybrid",
            "performance_note": None,
            "index_stale": False,
            "index_refresh_running": False,
        }
    )
    return RetrievalFusion(
        workspace=Path("."),
        memory=memory,
        nearline_memory=nearline,
        session_search=session_search,
        context_config=ContextConfig(),
        nearline_memory_config=SimpleNamespace(enabled=True, pipeline_enabled=True),
    )


def test_retrieval_fusion_prefers_fact_store_over_session_search_duplicates():
    fact = _fact_record("User prefers dark mode")
    bundle = FactRetrievalBundle(
        facts=[fact],
        rendered_text="## Long-term Memory\n- User prefers dark mode",
        fallback_used=False,
        retrievals=[FactRetrievalResult(fact=fact, score=0.95, source="semantic")],
    )
    fusion = _fusion(
        fact_bundle=bundle,
        nearline_result=NearlineRetrievalResult(),
        search_rows=[
            {
                "source": "sessions",
                "snippet": "User prefers dark mode",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "locator": {"message_index": 0},
                "score": 0.8,
                "match_type": "semantic",
            }
        ],
    )

    result = fusion.retrieve(
        query="dark mode",
        session_key="cli:direct",
        runtime_context=SimpleNamespace(default_scope="session", user_id="user-1", device_id=None),
        current_message="dark mode",
        recent_history=[],
        session_summary=None,
    )

    assert result.audit["deduped_count"] == 1
    assert result.audit["source_counts"]["fact_store"] == 1
    assert result.audit["source_counts"]["session_search"] == 0


def test_retrieval_fusion_prefers_nearline_over_session_search_duplicates():
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
    fusion = _fusion(
        fact_bundle=FactRetrievalBundle(facts=[], rendered_text="", fallback_used=False, retrievals=[]),
        nearline_result=nearline,
        search_rows=[
            {
                "source": "sessions",
                "snippet": "Prepare deployment checklist",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "locator": {"message_index": 0},
                "score": 0.7,
                "match_type": "semantic",
            }
        ],
    )

    result = fusion.retrieve(
        query="deployment checklist",
        session_key="cli:direct",
        runtime_context=SimpleNamespace(default_scope="session", user_id="user-1", device_id=None),
        current_message="deployment checklist",
        recent_history=[],
        session_summary=None,
    )

    assert result.audit["deduped_count"] == 1
    assert result.audit["source_counts"]["nearline_retrieval"] == 1
    assert result.audit["source_counts"]["session_search"] == 0


def test_retrieval_fusion_prefers_prewarm_seed_over_session_search_duplicates():
    fusion = _fusion(
        fact_bundle=FactRetrievalBundle(facts=[], rendered_text="", fallback_used=False, retrievals=[]),
        nearline_result=NearlineRetrievalResult(),
        search_rows=[
            {
                "source": "sessions",
                "snippet": "Prepare deployment checklist",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "locator": {"message_index": 0},
                "score": 0.7,
                "match_type": "semantic",
            }
        ],
    )

    result = fusion.retrieve(
        query="deployment checklist",
        session_key="cli:direct",
        runtime_context=SimpleNamespace(default_scope="session", user_id="user-1", device_id="device-a"),
        current_message="deployment checklist",
        recent_history=[],
        session_summary=None,
        prewarm_seed=[
            {
                "title": "recent_session:cli:other",
                "text": "Prepare deployment checklist",
                "scope": "session",
                "owner_id": "user-1",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "details": {"device_id": "device-a"},
            }
        ],
    )

    assert result.audit["deduped_count"] == 1
    assert result.audit["source_counts"]["prewarm_seed"] == 1
    assert result.audit["source_counts"]["session_search"] == 0
    assert result.retrieved_blocks[0].source == "retrieval_prewarm_seed"


def test_retrieval_fusion_reports_owner_hidden_for_user_scope_mismatch():
    fusion = _fusion(
        fact_bundle=FactRetrievalBundle(facts=[], rendered_text="", fallback_used=False, retrievals=[]),
        nearline_result=NearlineRetrievalResult(),
        search_rows=[],
    )

    result = fusion.retrieve(
        query="prewarm",
        session_key="cli:direct",
        runtime_context=SimpleNamespace(default_scope="session", user_id="user-1", device_id="device-a"),
        current_message="prewarm",
        recent_history=[],
        session_summary=None,
        prewarm_seed=[
            {
                "title": "recent_session:cli:other",
                "text": "Cross-user memory",
                "scope": "user",
                "owner_id": "user-2",
                "timestamp": "2026-06-09T00:00:00+00:00",
            }
        ],
    )

    assert result.audit["scope_filtered"] == [
        {"source": "prewarm_seed", "title": "recent_session:cli:other", "reason": "owner_hidden"}
    ]


def test_retrieval_fusion_reports_device_hidden_for_device_scope_mismatch():
    fusion = _fusion(
        fact_bundle=FactRetrievalBundle(facts=[], rendered_text="", fallback_used=False, retrievals=[]),
        nearline_result=NearlineRetrievalResult(),
        search_rows=[],
    )

    result = fusion.retrieve(
        query="prewarm",
        session_key="cli:direct",
        runtime_context=SimpleNamespace(default_scope="device", user_id="user-1", device_id="device-a"),
        current_message="prewarm",
        recent_history=[],
        session_summary=None,
        prewarm_seed=[
            {
                "title": "recent_session:cli:other",
                "text": "Other device memory",
                "scope": "device",
                "owner_id": "user-1",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "details": {"device_id": "device-b"},
            }
        ],
    )

    assert result.audit["scope_filtered"] == [
        {"source": "prewarm_seed", "title": "recent_session:cli:other", "reason": "device_hidden"}
    ]

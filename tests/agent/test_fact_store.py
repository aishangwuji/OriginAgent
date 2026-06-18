import json
from datetime import datetime, timedelta

import pytest

from OriginAgent.agent.facts import FactRecord, FactStore
from OriginAgent.agent.memory import MemoryStore, MemoryWorkspaceSnapshot, redact_memory_text


@pytest.fixture
def fact_store(tmp_path):
    return FactStore(tmp_path, redactor=redact_memory_text)


def test_upsert_creates_fact_with_required_fields(fact_store):
    fact = fact_store.upsert_fact(
        "Dim living room lights after 22:00",
        category="preference",
        scope="home.living_room.lighting",
        owner="user",
        source_cursors=[123],
        source_excerpt="after 22:00 dim the living room lights",
        confidence=0.86,
    )

    assert fact.fact_id.startswith("fact_")
    assert fact.fact_id != fact.canonical_key
    assert fact.status == "active"
    assert fact.requires_confirmation is False
    assert fact.source_cursors == [123]
    assert fact_store.facts_file.exists()

    raw = json.loads(fact_store.facts_file.read_text(encoding="utf-8"))
    for key in (
        "fact_id",
        "content",
        "canonical_key",
        "category",
        "scope",
        "owner",
        "source_cursors",
        "source_excerpt",
        "confidence",
        "status",
        "created_at",
        "updated_at",
        "last_seen_at",
        "expires_at",
        "supersedes_fact_id",
        "requires_confirmation",
    ):
        assert key in raw


def test_same_canonical_key_updates_existing_fact_without_duplicate(fact_store):
    first = fact_store.upsert_fact(
        "Use warm lights in the bedroom",
        category="preference",
        scope="home.bedroom.lighting",
        owner="user",
        source_cursors=[1],
        confidence=0.4,
    )
    second = fact_store.upsert_fact(
        "  Use   warm lights in the bedroom  ",
        category="preference",
        scope="home.bedroom.lighting",
        owner="user",
        source_cursors=[1, 2],
        confidence=0.9,
    )

    assert second.fact_id == first.fact_id
    assert second.source_cursors == [1, 2]
    assert second.confidence == 0.9
    assert len(fact_store.read_all()) == 1
    assert fact_store.facts_file.read_text(encoding="utf-8").count("\n") == 1


def test_explicit_relation_candidates_persist_supported_relation_kinds(tmp_path):
    store = FactStore(
        tmp_path,
        redactor=redact_memory_text,
        feature_flags={"fact_graph_enabled": True},
    )
    base = store.upsert_fact(
        "User lives in Tokyo",
        category="preference",
        scope="user.location",
        owner="user",
    )
    narrower = store.upsert_fact(
        "User lives in Tokyo Minato",
        category="preference",
        scope="user.location",
        owner="user",
        target_fact_id=base.fact_id,
        relation_candidates=[
            {"relation_type": "narrows", "target_fact_id": base.fact_id},
            {"relation_type": "generalizes", "source_fact_id": base.fact_id, "target_fact_id": "__new_fact__"},
            {"relation_type": "contradicts", "source_fact_id": "__new_fact__", "target_fact_id": base.fact_id},
        ],
    )

    relations = store.read_relations()
    triples = {
        (relation.source_fact_id, relation.target_fact_id, relation.relation_type)
        for relation in relations
    }
    assert (narrower.fact_id, base.fact_id, "narrows") in triples
    assert (base.fact_id, narrower.fact_id, "generalizes") in triples
    assert (narrower.fact_id, base.fact_id, "contradicts") in triples


def test_related_facts_can_traverse_persisted_relations(tmp_path):
    store = FactStore(
        tmp_path,
        redactor=redact_memory_text,
        feature_flags={"fact_graph_enabled": True},
    )
    general = store.upsert_fact(
        "User lives in Tokyo",
        category="preference",
        scope="user.location",
        owner="user",
    )
    specific = store.upsert_fact(
        "User lives in Tokyo Minato",
        category="preference",
        scope="user.location",
        owner="user",
        relation_candidates=[
            {"relation_type": "narrows", "target_fact_id": general.fact_id},
        ],
    )

    forward = store.related_facts(
        specific.fact_id,
        relation_types=("narrows",),
        depth=1,
        include_pending=True,
    )
    reverse = store.related_facts(
        general.fact_id,
        relation_types=("narrows",),
        depth=1,
        include_pending=True,
        bidirectional=True,
    )

    assert [fact.fact_id for fact in forward] == [general.fact_id]
    assert [fact.fact_id for fact in reverse] == [specific.fact_id]


def test_source_excerpt_is_redacted_but_fact_content_is_not(fact_store):
    fact = fact_store.upsert_fact(
        "The support contact is alice@example.com",
        source_excerpt=(
            "alice@example.com used Bearer abcdefghijklmnop and "
            "token='supersecretvalue'"
        ),
    )

    assert "alice@example.com" in fact.content
    assert "alice@example.com" not in fact.source_excerpt
    assert "Bearer abcdefghijklmnop" not in fact.source_excerpt
    assert "supersecretvalue" not in fact.source_excerpt


def test_deprecated_same_canonical_key_can_create_new_fact(fact_store):
    old = fact_store.upsert_fact("Keep porch light on", scope="home.entry.lighting")
    assert fact_store.deprecate_fact(old.fact_id) is True

    new = fact_store.upsert_fact("Keep porch light on", scope="home.entry.lighting")

    assert new.fact_id.startswith("fact_")
    assert new.fact_id != old.fact_id
    records = fact_store.read_all()
    assert [record.status for record in records] == ["deprecated", "active"]


def test_deprecate_marks_fact_without_deleting_it(fact_store):
    fact = fact_store.upsert_fact("Use quiet notifications")

    assert fact_store.deprecate_fact(fact.fact_id) is True

    records = fact_store.read_all()
    assert len(records) == 1
    assert records[0].status == "deprecated"
    assert fact_store.list_active() == []


def test_render_memory_md_excludes_deprecated_and_contradicted(fact_store):
    active = fact_store.upsert_fact("Use quiet notifications")
    deprecated = fact_store.upsert_fact("Old preference")
    contradicted = fact_store.upsert_fact("Contradicted preference")
    records = fact_store.read_all()
    for record in records:
        if record.fact_id == deprecated.fact_id:
            record.status = "deprecated"
        if record.fact_id == contradicted.fact_id:
            record.status = "contradicted"
    fact_store._write_records_unlocked(records)

    markdown = fact_store.render_memory_md()

    assert active.content in markdown
    assert deprecated.content not in markdown
    assert contradicted.content not in markdown


def test_policy_and_safety_default_to_pending_confirmation(fact_store):
    policy = fact_store.upsert_fact(
        "Guests cannot unlock the front door",
        category="policy",
        scope="home.entry.lock",
        owner="user",
        source_cursors=[201],
    )
    safety = fact_store.upsert_fact(
        "Alert if the gas sensor reports a leak",
        category="safety",
        scope="home.kitchen.gas",
        owner="user",
    )

    assert policy.status == "pending_confirmation"
    assert policy.requires_confirmation is True
    assert safety.status == "pending_confirmation"
    assert safety.requires_confirmation is True

    markdown = fact_store.render_memory_md()
    assert "## Pending Confirmation" in markdown
    assert "Guests cannot unlock the front door" in markdown
    assert "## Policies" not in markdown
    assert "## Safety" not in markdown


def test_policy_can_be_active_only_with_explicit_override(fact_store):
    policy = fact_store.upsert_fact(
        "Trusted family can unlock the side door",
        category="policy",
        scope="home.entry.side_door",
        owner="user",
        requires_confirmation=False,
        status="active",
    )

    assert policy.status == "active"
    assert policy.requires_confirmation is False
    markdown = fact_store.render_memory_md()
    assert "## Policies" in markdown
    assert "Trusted family can unlock the side door" in markdown
    assert "## Pending Confirmation" not in markdown


def test_temporary_fact_can_store_expires_at(fact_store):
    fact = fact_store.upsert_fact(
        "Parents are visiting this week",
        category="temporary",
        scope="household.guests",
        owner="user",
        expires_at="2026-05-22T00:00:00",
    )

    assert fact.expires_at == "2026-05-22T00:00:00"
    assert "expires_at: 2026-05-22T00:00:00" in fact_store.render_memory_md()


def test_blank_scope_normalizes_to_general(fact_store):
    fact = fact_store.upsert_fact("Use quiet notifications", scope="   ")

    assert fact.scope == "general"
    raw = json.loads(fact_store.facts_file.read_text(encoding="utf-8"))
    assert raw["scope"] == "general"


def test_bad_json_lines_are_skipped_and_dropped_on_rewrite(fact_store):
    fact_store.facts_file.parent.mkdir(parents=True, exist_ok=True)
    fact_store.facts_file.write_text("{bad json\n", encoding="utf-8")

    assert fact_store.read_all() == []
    fact_store.upsert_fact("Use warm lights", category="preference")

    raw = fact_store.facts_file.read_text(encoding="utf-8")
    assert "{bad json" not in raw
    assert "Use warm lights" in raw
    assert len(fact_store.read_all()) == 1


def test_fact_store_cache_refreshes_after_write(fact_store):
    fact_store.upsert_fact("Use warm lights", category="preference")
    first = fact_store.read_all()
    assert len(first) == 1

    fact_store.upsert_fact("Use quiet notifications", category="preference")
    second = fact_store.read_all()

    assert len(second) == 2


def test_render_memory_md_is_deterministic_by_category_scope_content_and_id(fact_store):
    fact_store.upsert_fact(
        "Wake at 07:30",
        category="routine",
        scope="user.routine",
    )
    fact_store.upsert_fact(
        "Use bright kitchen lights",
        category="preference",
        scope="home.kitchen.lighting",
    )
    fact_store.upsert_fact(
        "Use dim bedroom lights",
        category="preference",
        scope="home.bedroom.lighting",
    )

    markdown = fact_store.render_memory_md()

    assert markdown.index("## Preferences") < markdown.index("## Routines")
    assert markdown.index("Use dim bedroom lights") < markdown.index(
        "Use bright kitchen lights"
    )


def test_decay_confidence_only_updates_stale_active_facts(fact_store):
    now = datetime(2026, 5, 26, 12, 0, 0)
    stale = fact_store.upsert_fact(
        "Use warm bedroom lights",
        category="preference",
        scope="home.bedroom.lighting",
        confidence=0.9,
    )
    recent = fact_store.upsert_fact(
        "Use quiet notifications",
        category="preference",
        scope="user.notifications",
        confidence=0.8,
    )
    pending = fact_store.upsert_fact(
        "Guests cannot unlock the front door",
        category="policy",
        scope="home.entry.lock",
        confidence=0.9,
    )
    records = fact_store.read_all()
    for record in records:
        if record.fact_id == stale.fact_id:
            record.last_seen_at = (now - timedelta(days=40)).isoformat()
        elif record.fact_id == recent.fact_id:
            record.last_seen_at = (now - timedelta(days=10)).isoformat()
        elif record.fact_id == pending.fact_id:
            record.last_seen_at = (now - timedelta(days=90)).isoformat()
    fact_store._write_records_unlocked(records)

    changed = fact_store.decay_confidence(
        factor=0.5,
        min_confidence=0.3,
        decay_start_days=30,
        now=now,
    )

    assert changed == 1
    by_id = {record.fact_id: record for record in fact_store.read_all()}
    assert by_id[stale.fact_id].confidence == 0.3
    assert by_id[recent.fact_id].confidence == 0.8
    assert by_id[pending.fact_id].confidence == 0.9


def test_confidence_v2_decay_slows_for_frequently_retrieved_facts(tmp_path):
    store = FactStore(
        tmp_path,
        redactor=redact_memory_text,
        feature_flags={"confidence_v2_enabled": True},
    )
    now = datetime(2026, 5, 26, 12, 0, 0)
    often_used = store.upsert_fact(
        "Use warm bedroom lights",
        category="preference",
        scope="home.bedroom.lighting",
        confidence=0.9,
    )
    rarely_used = store.upsert_fact(
        "Use quiet notifications",
        category="preference",
        scope="user.notifications",
        confidence=0.9,
    )
    records = store.read_all()
    for record in records:
        record.last_seen_at = (now - timedelta(days=60)).isoformat()
        record.last_supported_at = (now - timedelta(days=60)).isoformat()
        if record.fact_id == often_used.fact_id:
            record.retrieval_count = 10
            record.injection_count = 6
            record.last_retrieved_at = (now - timedelta(days=35)).isoformat()
        elif record.fact_id == rarely_used.fact_id:
            record.retrieval_count = 0
            record.injection_count = 0
            record.last_retrieved_at = (now - timedelta(days=60)).isoformat()
    store._write_records_unlocked(records)

    changed = store.decay_confidence(now=now)

    assert changed == 2
    by_id = {record.fact_id: record for record in store.read_all()}
    assert by_id[often_used.fact_id].confidence > by_id[rarely_used.fact_id].confidence


def test_confidence_v2_decay_keeps_low_usage_behavior(tmp_path):
    store = FactStore(
        tmp_path,
        redactor=redact_memory_text,
        feature_flags={"confidence_v2_enabled": True},
    )
    now = datetime(2026, 5, 26, 12, 0, 0)
    fact = store.upsert_fact(
        "Use quiet notifications",
        category="preference",
        scope="user.notifications",
        confidence=0.9,
    )
    records = store.read_all()
    assert len(records) == 1
    records[0].last_seen_at = (now - timedelta(days=60)).isoformat()
    records[0].last_supported_at = (now - timedelta(days=60)).isoformat()
    records[0].retrieval_count = 1
    records[0].injection_count = 1
    records[0].last_retrieved_at = (now - timedelta(days=60)).isoformat()
    baseline_before = records[0].confidence
    derived_baseline = store._derive_confidence(records[0], now=now)
    assert derived_baseline < baseline_before
    store._write_records_unlocked(records)

    changed = store.decay_confidence(now=now)

    assert changed == 1
    updated = {record.fact_id: record for record in store.read_all()}[fact.fact_id]
    assert updated.confidence == pytest.approx(derived_baseline)


def test_calibrate_confidence_applies_bias_after_sample_threshold(fact_store):
    fact_store.calibration_file.write_text(
        json.dumps({
            "fact:home": {"bias": -0.15, "count": 12},
            "fact:user": {"bias": -0.2, "count": 3},
        }),
        encoding="utf-8",
    )

    assert fact_store.calibrate_confidence("fact", "home", 0.9) == pytest.approx(0.75)
    assert fact_store.calibrate_confidence("fact", "user", 0.9) == 0.9
    assert fact_store.calibrate_confidence("fact", "missing", 0.9) == 0.9


def test_memory_store_rebuilds_memory_md_from_active_facts(tmp_path):
    store = MemoryStore(tmp_path)

    store.upsert_fact_and_rebuild_memory(
        "Use warm lights",
        category="preference",
        scope="home.living_room.lighting",
        source_cursors=[3],
    )

    memory = store.read_memory()
    assert memory.startswith("# Long-term Memory")
    assert "Generated from memory/facts.jsonl" in memory
    assert "Use warm lights" in memory
    assert "source: cursor 3" in memory
    assert "Use warm lights" in store.get_memory_context()


def test_upsert_fact_and_rebuild_memory_uses_one_memory_lock(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    fact = FactRecord(
        fact_id="fact_test",
        content="content",
        canonical_key="key",
        category="note",
        scope="general",
        owner="unknown",
        source_cursors=[],
        source_excerpt="",
        confidence=1.0,
        status="active",
        created_at="2026-05-15T00:00:00",
        updated_at="2026-05-15T00:00:00",
        last_seen_at="2026-05-15T00:00:00",
        expires_at=None,
        supersedes_fact_id=None,
        requires_confirmation=False,
    )

    def fake_upsert(content, **kwargs):
        events.append(("upsert", content, kwargs))
        return fact

    def fake_render():
        events.append("render")
        return "# Long-term Memory\n"

    def fake_write(path, content):
        events.append(("write", path, content))

    monkeypatch.setattr(store, "_locked", lambda: FakeLock())
    monkeypatch.setattr(store.fact_store, "upsert_fact_unlocked", fake_upsert)
    monkeypatch.setattr(store.fact_store, "render_memory_md_unlocked", fake_render)
    monkeypatch.setattr(store, "_write_text_atomic", fake_write)

    result = store.upsert_fact_and_rebuild_memory("content")

    assert result is fact
    assert events[0] == "enter"
    assert events[1][0] == "upsert"
    assert events[1][1] == "content"
    assert events[1][2]["category"] == "note"
    assert events[1][2]["scope"] == "general"
    assert events[1][2]["owner"] == "unknown"
    assert events[1][2]["source_cursors"] is None
    assert events[1][2]["supersedes_fact_id"] is None
    assert events[2] == "render"
    assert events[3] == ("write", store.memory_file, "# Long-term Memory\n")
    assert events[4] == "exit"


def test_seed_legacy_memory_as_single_low_confidence_note(tmp_path):
    store = MemoryStore(tmp_path)
    legacy = "# Old Memory\n- Keep porch light warm\n"
    store.write_memory(legacy)

    fact = store.seed_legacy_memory_as_note()

    assert fact is not None
    assert fact.content == legacy.strip()
    assert fact.category == "note"
    assert fact.scope == "legacy.memory"
    assert fact.owner == "system"
    assert fact.confidence == 0.5
    assert fact.source_excerpt == "legacy MEMORY.md import"
    assert len(store.fact_store.read_all()) == 1
    assert "# Long-term Memory" in store.read_memory()
    assert "## Notes" in store.read_memory()
    assert "Keep porch light warm" in store.read_memory()
    assert store.seed_legacy_memory_as_note() is None


def test_seed_legacy_memory_requires_empty_facts_file(tmp_path):
    store = MemoryStore(tmp_path)
    store.write_memory("legacy")
    store.fact_store.upsert_fact("Existing fact")

    assert store.seed_legacy_memory_as_note() is None
    assert len(store.fact_store.read_all()) == 1


def test_seed_legacy_memory_allows_invalid_only_facts_file(tmp_path):
    store = MemoryStore(tmp_path)
    store.write_memory("# Old Memory\n- Keep porch light warm\n")
    store.facts_file.write_text("{bad json\n", encoding="utf-8")

    fact = store.seed_legacy_memory_as_note()

    assert fact is not None
    assert fact.category == "note"
    assert fact.scope == "legacy.memory"
    raw = store.facts_file.read_text(encoding="utf-8")
    assert "{bad json" not in raw
    assert len(store.fact_store.read_all()) == 1


def test_memory_workspace_snapshot_tracks_facts_jsonl(tmp_path):
    facts_file = tmp_path / "memory" / "facts.jsonl"
    facts_file.parent.mkdir(parents=True, exist_ok=True)
    facts_file.write_text("original\n", encoding="utf-8")
    snapshot = MemoryWorkspaceSnapshot(tmp_path)

    facts_file.write_text("dirty\n", encoding="utf-8")

    assert snapshot.restore() is True
    assert facts_file.read_text(encoding="utf-8") == "original\n"


def test_memory_workspace_snapshot_does_not_track_presence_json(tmp_path):
    presence_file = tmp_path / "memory" / "presence.json"
    presence_file.parent.mkdir(parents=True, exist_ok=True)
    presence_file.write_text('{"people": {}}\n', encoding="utf-8")
    snapshot = MemoryWorkspaceSnapshot(tmp_path)

    presence_file.write_text('{"people": {"alice": {}}}\n', encoding="utf-8")

    assert snapshot.restore() is True
    assert presence_file.read_text(encoding="utf-8") == '{"people": {"alice": {}}}\n'


def test_semantic_retrieval_does_not_drop_unranked_facts(tmp_path):
    store = FactStore(
        tmp_path,
        redactor=redact_memory_text,
        feature_flags={"semantic_retrieval_enabled": True},
    )
    store.upsert_fact("Use warm lights", category="preference", scope="home.living.lighting", owner="user")
    store.upsert_fact("Use quiet notifications", category="preference", scope="user.notifications", owner="user")

    bundle = store.retrieve_context_bundle(scope_prefix="home", top_k=1)

    assert len(bundle.facts) == 1
    assert len(store.read_all()) == 2


def test_lazy_memory_workspace_snapshot_restores_only_touched_skill_files(tmp_path):
    existing_skill = tmp_path / "skills" / "existing" / "SKILL.md"
    existing_skill.parent.mkdir(parents=True, exist_ok=True)
    existing_skill.write_text("original skill", encoding="utf-8")
    untouched_skill = tmp_path / "skills" / "untouched" / "SKILL.md"
    untouched_skill.parent.mkdir(parents=True, exist_ok=True)
    untouched_skill.write_text("keep me", encoding="utf-8")

    snapshot = MemoryWorkspaceSnapshot(tmp_path, lazy=True)
    snapshot.capture_before_write(existing_skill)
    snapshot.capture_before_write(tmp_path / "skills" / "new-skill" / "SKILL.md")

    existing_skill.write_text("dirty existing skill", encoding="utf-8")
    (tmp_path / "skills" / "new-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skills" / "new-skill" / "SKILL.md").write_text("dirty new skill", encoding="utf-8")

    assert snapshot.restore() is True
    assert existing_skill.read_text(encoding="utf-8") == "original skill"
    assert untouched_skill.read_text(encoding="utf-8") == "keep me"
    assert not (tmp_path / "skills" / "new-skill" / "SKILL.md").exists()

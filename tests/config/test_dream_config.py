from OriginAgent.config.schema import AgentDefaults, DreamConfig, NearlineMemoryConfig


def test_dream_config_defaults_to_interval_hours() -> None:
    cfg = DreamConfig()

    assert cfg.interval_h == 2
    assert cfg.cron is None


def test_dream_config_builds_every_schedule_from_interval() -> None:
    cfg = DreamConfig(interval_h=3)

    schedule = cfg.build_schedule("UTC")

    assert schedule.kind == "every"
    assert schedule.every_ms == 3 * 3_600_000
    assert schedule.expr is None


def test_dream_config_honors_legacy_cron_override() -> None:
    cfg = DreamConfig.model_validate({"cron": "0 */4 * * *"})

    schedule = cfg.build_schedule("UTC")

    assert schedule.kind == "cron"
    assert schedule.expr == "0 */4 * * *"
    assert schedule.tz == "UTC"
    assert cfg.describe_schedule() == "cron 0 */4 * * * (legacy)"


def test_dream_config_dump_uses_interval_h_and_hides_legacy_cron() -> None:
    cfg = DreamConfig.model_validate({"intervalH": 5, "cron": "0 */4 * * *"})

    dumped = cfg.model_dump(by_alias=True)

    assert dumped["intervalH"] == 5
    assert "cron" not in dumped


def test_dream_config_uses_model_override_name_and_accepts_legacy_model() -> None:
    cfg = DreamConfig.model_validate({"model": "openrouter/sonnet"})

    dumped = cfg.model_dump(by_alias=True)

    assert cfg.model_override == "openrouter/sonnet"
    assert dumped["modelOverride"] == "openrouter/sonnet"
    assert "model" not in dumped


def test_dream_config_memory_rollout_flags_default_disabled() -> None:
    cfg = DreamConfig()

    assert cfg.semantic_retrieval_enabled is False
    assert cfg.semantic_merge_enabled is False
    assert cfg.fact_graph_enabled is False
    assert cfg.confidence_v2_enabled is False
    assert cfg.contradiction_auto_flip_enabled is False
    assert cfg.fact_audit_enabled is False
    assert cfg.lazy_snapshot_enabled is False


def test_dream_config_memory_rollout_flags_accept_camel_case() -> None:
    cfg = DreamConfig.model_validate({
        "semanticRetrievalEnabled": True,
        "semanticMergeEnabled": True,
        "factGraphEnabled": True,
        "confidenceV2Enabled": True,
        "contradictionAutoFlipEnabled": True,
        "factAuditEnabled": True,
        "lazySnapshotEnabled": True,
    })

    dumped = cfg.model_dump(by_alias=True)

    assert cfg.semantic_retrieval_enabled is True
    assert cfg.semantic_merge_enabled is True
    assert cfg.fact_graph_enabled is True
    assert cfg.confidence_v2_enabled is True
    assert cfg.contradiction_auto_flip_enabled is True
    assert cfg.fact_audit_enabled is True
    assert cfg.lazy_snapshot_enabled is True
    assert dumped["semanticRetrievalEnabled"] is True
    assert dumped["semanticMergeEnabled"] is True
    assert dumped["factGraphEnabled"] is True
    assert dumped["confidenceV2Enabled"] is True
    assert dumped["contradictionAutoFlipEnabled"] is True
    assert dumped["factAuditEnabled"] is True
    assert dumped["lazySnapshotEnabled"] is True


def test_nearline_memory_config_defaults_disabled() -> None:
    cfg = NearlineMemoryConfig()

    assert cfg.enabled is False
    assert cfg.pipeline_enabled is False
    assert cfg.profile_shadow_write_enabled is False
    assert cfg.retrieval_top_k == 8
    assert cfg.episode_compaction_interval_turns == 20


def test_agent_defaults_accept_nearline_memory_camel_case() -> None:
    defaults = AgentDefaults.model_validate({
        "nearlineMemory": {
            "enabled": True,
            "pipelineEnabled": True,
            "profileShadowWriteEnabled": True,
            "retrievalTopK": 12,
            "maxMessagesPerMemcell": 16,
            "idleGapSeconds": 600,
            "profileRefreshMinMemcells": 2,
            "eventBatchSize": 20,
        }
    })

    dumped = defaults.model_dump(by_alias=True)
    nearline = dumped["nearlineMemory"]

    assert defaults.nearline_memory.enabled is True
    assert defaults.nearline_memory.pipeline_enabled is True
    assert defaults.nearline_memory.profile_shadow_write_enabled is True
    assert nearline["pipelineEnabled"] is True
    assert nearline["profileShadowWriteEnabled"] is True
    assert nearline["retrievalTopK"] == 12
    assert nearline["episodeCompactionIntervalTurns"] == 20

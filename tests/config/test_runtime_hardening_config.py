from OriginAgent.config.schema import AgentDefaults, BackgroundReviewConfig, ConfirmationConfig, ContextConfig, CuratorConfig, TaskRuntimeConfig


def test_context_config_defaults() -> None:
    cfg = ContextConfig()
    assert cfg.enable_phase1_continuity is True
    assert cfg.max_recent_history == 50
    assert cfg.max_history_chars == 32_000
    assert cfg.world_summary_ttl_minutes == 5
    assert cfg.snapshot_freshness_minutes == 30
    assert cfg.world_attention_max_items == 3
    assert cfg.governance_enabled is True
    assert cfg.prewarm_enabled is True
    assert cfg.promotion_confidence_threshold == 0.8
    assert cfg.promotion_min_confirmations == 2
    assert cfg.promotion_require_user_confirmation_for_sensitive is True
    assert cfg.prewarm_max_items == 8
    assert cfg.max_media_files == 8
    assert cfg.max_media_bytes == 8 * 1024 * 1024


def test_confirmation_config_aliases() -> None:
    cfg = ConfirmationConfig.model_validate(
        {
            "promptMaxChars": 1200,
            "reasonMaxChars": 2400,
            "notifyTtlSeconds": 60,
            "confirmTtlByRisk": {"low": 30, "medium": 20, "high": 10},
        }
    )
    assert cfg.prompt_max_chars == 1200
    assert cfg.reason_max_chars == 2400
    assert cfg.notify_ttl_seconds == 60
    assert cfg.confirm_ttl_by_risk["high"] == 10


def test_background_review_extended_limits_and_retry() -> None:
    cfg = BackgroundReviewConfig.model_validate(
        {
            "titleMaxChars": 200,
            "contentMaxChars": 3000,
            "rationaleMaxChars": 1400,
            "evidenceMaxItems": 6,
            "evidenceMaxChars": 600,
            "messageMaxChars": 1800,
            "reviewReasonMaxChars": 1100,
            "transientRetryCount": 2,
        }
    )
    assert cfg.title_max_chars == 200
    assert cfg.content_max_chars == 3000
    assert cfg.transient_retry_count == 2


def test_curator_extended_limits_and_retry() -> None:
    cfg = CuratorConfig.model_validate(
        {
            "titleMaxChars": 200,
            "bodyMaxChars": 3000,
            "rationaleMaxChars": 1400,
            "evidenceMaxChars": 600,
            "maxEvidence": 6,
            "transientRetryCount": 2,
        }
    )
    assert cfg.title_max_chars == 200
    assert cfg.body_max_chars == 3000
    assert cfg.max_evidence == 6
    assert cfg.transient_retry_count == 2


def test_agent_defaults_embed_runtime_hardening_configs() -> None:
    defaults = AgentDefaults()
    assert isinstance(defaults.context, ContextConfig)
    assert isinstance(defaults.confirmation, ConfirmationConfig)
    assert isinstance(defaults.task_runtime, TaskRuntimeConfig)

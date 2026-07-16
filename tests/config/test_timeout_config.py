"""Tests for TimeoutConfig — centralized timeout configuration (spec 3.4, rule 17).

Verifies default values, alias handling, and that consumer classes accept
the new parameters correctly.
"""

from OriginAgent.config.schema import Config, TimeoutConfig


def test_timeout_config_defaults() -> None:
    cfg = TimeoutConfig()
    assert cfg.http_download_timeout == 30.0
    assert cfg.http_api_timeout == 30.0
    assert cfg.db_busy_timeout == 5000
    assert cfg.tool_default_timeout == 30.0


def test_config_has_timeouts_field() -> None:
    config = Config()
    assert isinstance(config.timeouts, TimeoutConfig)
    assert config.timeouts.http_download_timeout == 30.0
    assert config.timeouts.http_api_timeout == 30.0
    assert config.timeouts.db_busy_timeout == 5000
    assert config.timeouts.tool_default_timeout == 30.0


def test_timeout_config_camel_case_alias() -> None:
    cfg = TimeoutConfig.model_validate(
        {
            "httpDownloadTimeout": 60.0,
            "httpApiTimeout": 45.0,
            "dbBusyTimeout": 8000,
            "toolDefaultTimeout": 60.0,
        }
    )
    assert cfg.http_download_timeout == 60.0
    assert cfg.http_api_timeout == 45.0
    assert cfg.db_busy_timeout == 8000
    assert cfg.tool_default_timeout == 60.0


def test_timeout_config_snake_case_alias() -> None:
    cfg = TimeoutConfig.model_validate(
        {
            "http_download_timeout": 60.0,
            "http_api_timeout": 45.0,
            "db_busy_timeout": 8000,
            "tool_default_timeout": 60.0,
        }
    )
    assert cfg.http_download_timeout == 60.0
    assert cfg.http_api_timeout == 45.0
    assert cfg.db_busy_timeout == 8000
    assert cfg.tool_default_timeout == 60.0


def test_config_timeouts_from_dict() -> None:
    config = Config.model_validate(
        {
            "timeouts": {
                "httpDownloadTimeout": 90.0,
                "httpApiTimeout": 60.0,
                "dbBusyTimeout": 10000,
                "toolDefaultTimeout": 45.0,
            }
        }
    )
    assert config.timeouts.http_download_timeout == 90.0
    assert config.timeouts.http_api_timeout == 60.0
    assert config.timeouts.db_busy_timeout == 10000
    assert config.timeouts.tool_default_timeout == 45.0


def test_web_search_tool_accepts_http_api_timeout() -> None:
    from OriginAgent.agent.tools.web import WebSearchTool

    tool = WebSearchTool(http_api_timeout=45.0)
    assert tool._http_api_timeout == 45.0


def test_web_fetch_tool_accepts_http_api_timeout() -> None:
    from OriginAgent.agent.tools.web import WebFetchTool

    tool = WebFetchTool(http_api_timeout=45.0)
    assert tool._http_api_timeout == 45.0


def test_sqlite_evolution_ledger_accepts_db_busy_timeout(tmp_path) -> None:
    from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger

    ledger = SqliteEvolutionLedger(workspace=tmp_path, db_busy_timeout=8000)
    assert ledger._db_busy_timeout == 8000


def test_session_search_index_accepts_db_busy_timeout(tmp_path) -> None:
    from OriginAgent.session.search_index import SessionSearchIndexService

    service = SessionSearchIndexService(workspace=tmp_path, db_busy_timeout=8000)
    assert service._db_busy_timeout == 8000


def test_create_ledger_threads_db_busy_timeout_from_config(tmp_path) -> None:
    from OriginAgent.evolution.ledger_factory import create_ledger

    config = Config()
    config.timeouts.db_busy_timeout = 7000
    ledger = create_ledger(tmp_path, config=config)
    assert ledger._db_busy_timeout == 7000

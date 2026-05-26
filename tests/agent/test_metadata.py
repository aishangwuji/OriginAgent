import json

from OriginAgent.agent.metadata import (
    ORIGINAGENT_METADATA_KEY,
    read_originagent_metadata,
    set_originagent_metadata,
)


def test_read_originagent_metadata_prefers_current_namespace() -> None:
    metadata = {
        "OriginAgent": {"requires": {"bins": ["current"]}},
        "openclaw": {"requires": {"bins": ["legacy"]}},
    }

    assert read_originagent_metadata(metadata) == {"requires": {"bins": ["current"]}}


def test_read_originagent_metadata_supports_legacy_openclaw_json() -> None:
    raw = json.dumps({"openclaw": {"requires": {"bins": ["legacy-bin"]}}})

    assert read_originagent_metadata(raw) == {"requires": {"bins": ["legacy-bin"]}}


def test_set_originagent_metadata_writes_current_namespace_only() -> None:
    metadata = set_originagent_metadata(
        {"openclaw": {"requires": {"bins": ["legacy"]}}},
        {"proposal_status": "proposed"},
    )

    assert metadata[ORIGINAGENT_METADATA_KEY] == {"proposal_status": "proposed"}
    assert metadata["openclaw"] == {"requires": {"bins": ["legacy"]}}

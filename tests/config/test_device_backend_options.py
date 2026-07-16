"""Tests for unified DEVICE_BACKEND_OPTIONS single source of truth (spec 3.5)."""
from __future__ import annotations

from OriginAgent.config.schema import DEVICE_BACKEND_OPTIONS
from OriginAgent.gateway import rest_api


def test_device_backend_options_contains_all_three():
    """The canonical constant must include none, fake, and lighting_client."""
    assert "none" in DEVICE_BACKEND_OPTIONS
    assert "fake" in DEVICE_BACKEND_OPTIONS
    assert "lighting_client" in DEVICE_BACKEND_OPTIONS
    assert DEVICE_BACKEND_OPTIONS == ("none", "fake", "lighting_client")


def test_rest_api_uses_canonical_device_backends():
    """gateway.rest_api must source its backend options from schema (rule 6)."""
    assert rest_api._DEVICE_BACKEND_OPTIONS is DEVICE_BACKEND_OPTIONS


def test_websocket_no_longer_defines_local_device_backend_options():
    """channels.websocket must not shadow the canonical constant (rule 6).

    The local ``_DEVICE_BACKEND_OPTIONS`` in websocket.py was extraction leftover
    (dead code: no usage site). It is removed so the schema constant is the sole
    definition — see spec 3.5.
    """
    from OriginAgent.channels import websocket

    assert not hasattr(websocket, "_DEVICE_BACKEND_OPTIONS")

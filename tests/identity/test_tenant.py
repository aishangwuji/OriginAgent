from __future__ import annotations

from pathlib import Path

from OriginAgent.identity.tenant import Tenant


class TestTenant:
    def test_tenant_has_unified_session_key(self) -> None:
        t = Tenant(
            tenant_id="dad", display_name="爸爸",
            unified_session_key="tenant:dad",
            workspace_dir=Path("/tmp/tenants/dad"),
        )
        assert t.unified_session_key == "tenant:dad"
        assert t.tenant_id == "dad"

    def test_active_channels_lists_bound_channels(self) -> None:
        t = Tenant(
            tenant_id="mom", display_name="妈妈",
            unified_session_key="tenant:mom",
            workspace_dir=Path("/tmp/tenants/mom"),
            bindings=[
                {"channel": "telegram", "sender_id": "123", "label": "手机"},
                {"channel": "websocket", "sender_id": "456", "label": "手表"},
            ],
        )
        assert t.active_channels == ["telegram", "websocket"]

    def test_workspace_dir_is_tenant_scoped(self) -> None:
        t = Tenant(
            tenant_id="sister", display_name="姐姐",
            unified_session_key="tenant:sister",
            workspace_dir=Path("/workspace/tenants/sister"),
        )
        assert t.workspace_dir.name == "sister"

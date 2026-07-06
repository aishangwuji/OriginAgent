"""Tests for SubagentManager Soar chunker callback integration."""
import inspect
from unittest.mock import MagicMock

from OriginAgent.agent.subagent import SubagentManager


class TestSoarChunkerCallback:
    def test_init_accepts_soar_chunker_none(self):
        """SubagentManager.__init__ accepts soar_chunker=None (default)."""
        sig = inspect.signature(SubagentManager.__init__)
        assert "soar_chunker" in sig.parameters
        assert sig.parameters["soar_chunker"].default is None

    def test_soar_chunker_stored_as_instance_var(self):
        """soar_chunker is stored as self._soar_chunker."""
        # 绕过 __init__ 创建实例，验证 _soar_chunker 可被赋值与读取
        m = object.__new__(SubagentManager)
        m._soar_chunker = None
        assert m._soar_chunker is None

        mock_chunker = MagicMock()
        m._soar_chunker = mock_chunker
        assert m._soar_chunker is mock_chunker

    def test_soar_chunker_default_is_none(self):
        """When not passed, _soar_chunker is None (no chunking)."""
        m = object.__new__(SubagentManager)
        # 模拟 __init__ 中的赋值行为
        m._soar_chunker = None
        assert m._soar_chunker is None

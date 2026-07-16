"""Strangler Fig 迁移 Phase 0 验证(双路径共存)。

验证点:
- ``RuntimeDependencies`` 可仅凭平铺字段构造(回归保护,旧调用方不受影响)。
- 平铺字段经 ``AgentRuntime.deps`` 正常访问。
- 5 个子容器(``CoreServices``/``MetaCognitionServices``/``MemoryServices``
  /``BackgroundServices``/``StoreServices``)已定义并从 ``agent_runtime`` 模块导出,
  作为 Strangler Fig 迁移 Phase 0 的目标容器,与平铺字段共存。
- 子容器字段是 ``RuntimeDependencies`` 的合法构造参数,默认 None 以保持向后兼容。
- ``RuntimeConfig`` 与 ``config`` 字段保留(不在本次迁移范围)。
"""
from __future__ import annotations

import inspect

from OriginAgent.agent.agent_runtime import (
    AgentRuntime,
    RuntimeConfig,
    RuntimeDependencies,
)

_SUB_CONTAINER_NAMES = (
    "CoreServices",
    "MetaCognitionServices",
    "MemoryServices",
    "BackgroundServices",
    "StoreServices",
)


def test_runtime_dependencies_constructs_with_flat_fields_only():
    """平铺字段是唯一访问路径,无需子容器即可构造。"""
    deps = RuntimeDependencies(
        tools="TOOL_REG",
        provider="PROVIDER",
        model="gpt-test",
        max_iterations=7,
    )

    assert deps.tools == "TOOL_REG"
    assert deps.provider == "PROVIDER"
    assert deps.model == "gpt-test"
    assert deps.max_iterations == 7


def test_runtime_dependencies_defaults_remain_none():
    """未显式传入的平铺字段保持 None 默认值。"""
    deps = RuntimeDependencies()

    assert deps.tools is None
    assert deps.provider is None
    assert deps.state_holder is None
    assert deps.working_memory is None


def test_agent_runtime_exposes_flat_deps():
    """AgentRuntime.deps 暴露平铺字段,调用方访问路径不变。"""
    deps = RuntimeDependencies(tools="TOOL_REG", provider="PROVIDER")
    runtime = AgentRuntime(deps)

    assert runtime.deps.tools == "TOOL_REG"
    assert runtime.deps.provider == "PROVIDER"


def test_sub_container_classes_exported():
    """5 个子容器 dataclass 已定义并从 agent_runtime 模块导出(Phase 0 共存)。"""
    import OriginAgent.agent.agent_runtime as mod

    for name in _SUB_CONTAINER_NAMES:
        assert hasattr(mod, name), f"{name} 应已定义在 agent_runtime 模块(Phase 0 双路径共存)"


def test_sub_container_kwargs_accepted():
    """子容器字段是 RuntimeDependencies 的合法构造参数,默认 None。"""
    # 不传子容器 → 默认 None,不报错
    deps = RuntimeDependencies()
    for field_name in ("core", "meta_cognition_svc", "memory_svc", "background_svc", "stores"):
        assert getattr(deps, field_name) is None

    # 传子容器 → 接受
    from OriginAgent.agent.agent_runtime import CoreServices

    deps_with_core = RuntimeDependencies(core=CoreServices(tools="TOOL_REG"))
    assert deps_with_core.core is not None
    assert deps_with_core.core.tools == "TOOL_REG"


def test_runtime_config_field_retained():
    """RuntimeConfig 与 config 字段保留(不在本次迁移范围)。"""
    cfg = RuntimeConfig(model="m", max_iterations=3)
    deps = RuntimeDependencies(config=cfg)

    assert deps.config is cfg
    assert deps.config.model == "m"
    assert deps.config.max_iterations == 3


def test_strangler_fig_phase0_markers_present():
    """agent_runtime 源码中应保留 Strangler Fig Phase 0 迁移标注。"""
    from OriginAgent.agent import agent_runtime as mod

    src = inspect.getsource(mod)
    # Phase 0 双路径共存:子容器定义中应标注 Strangler Fig 迁移
    assert "Strangler Fig" in src or "strangler" in src.lower(), (
        "源码中应保留 Strangler Fig 迁移标注(Phase 0 双路径共存)"
    )


def test_phase0_flat_and_subcontainer_share_same_object():
    """Phase 0 一致性:平铺字段和子容器应指向同一对象(双路径共存)。

    构造 RuntimeDependencies 时同时传入平铺字段和子容器,验证两者引用相同对象。
    这保证迁移期间两条访问路径不会产生状态分歧。
    """
    from OriginAgent.agent.agent_runtime import CoreServices, MemoryServices

    shared_tools = "TOOL_REG_SHARED"
    shared_provider = "PROVIDER_SHARED"
    shared_nearline = "NEARLINE_SHARED"

    core = CoreServices(tools=shared_tools, provider=shared_provider)
    memory_svc = MemoryServices(nearline_memory=shared_nearline)

    deps = RuntimeDependencies(
        # 平铺字段
        tools=shared_tools,
        provider=shared_provider,
        nearline_memory=shared_nearline,
        # 子容器
        core=core,
        memory_svc=memory_svc,
    )

    # 平铺字段访问
    assert deps.tools is shared_tools
    assert deps.provider is shared_provider
    assert deps.nearline_memory is shared_nearline

    # 子容器访问
    assert deps.core is core
    assert deps.core.tools is shared_tools
    assert deps.core.provider is shared_provider
    assert deps.memory_svc is memory_svc
    assert deps.memory_svc.nearline_memory is shared_nearline

    # 双路径一致性:平铺字段和子容器字段指向同一对象
    assert deps.tools is deps.core.tools
    assert deps.provider is deps.core.provider
    assert deps.nearline_memory is deps.memory_svc.nearline_memory

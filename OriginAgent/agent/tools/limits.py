"""Central limits for built-in tools."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolLimits:
    """Runtime limits shared by tool implementations.

    这些限制值在各 Tool 内部通过 ``self._limits`` 引用(28+ 处),用于
    强制安全边界(防 DoS / 内存耗尽 / 资源超时),属规则 18 安全边界范畴。

    ``limits`` 参数定制点评估结论(tech-debt Batch D, 2026-07):
      - 生产代码:所有工厂均用默认值,无调用方传入非默认 limits。不接入
        ToolsConfig——无生产定制需求,接入将属规则 32 投机性设计。
      - 测试代码:4 个测试文件(含 web_fetch 安全边界测试)通过传入小值
        limits 验证安全限制生效,是已存在的具体调用点(规则 32 第二使用场景)。
      - 结论:保留 ToolLimits 类与 limits 参数。删除将破坏安全边界测试
        覆盖(违反规则 15/18);接入 config 无生产需求(违反规则 32)。
    """

    read_file_max_chars: int = 128_000
    read_file_default_limit: int = 2_000
    read_file_max_pdf_pages: int = 20
    exec_max_output_chars: int = 10_000
    exec_max_timeout_seconds: int = 600
    web_fetch_max_chars: int = 50_000
    grep_max_files: int = 5_000
    grep_max_file_bytes: int = 2_000_000
    grep_max_result_chars: int = 128_000
    best_window_max_scan_lines: int = 2_000
    max_input_bytes: int = 5_000_000
    web_fetch_max_bytes: int = 5_000_000
    mcp_response_max_chars: int = 128_000
    mcp_resource_max_bytes: int = 2_000_000

# 安全边界

智能体拥有较高操作权限（文件系统、终端 Shell、网络请求）。修改相关代码时，**严禁绕过**以下安全防护规则。

## 工作空间限制

文件系统工具（`read_file`、`write_file`、`edit_file`、`list_dir`）通过 `_resolve_path`（位于 `agent/tools/filesystem.py`）解析路径。
该方法强制要求：解析后的路径必须位于 `allowed_dir`（通常为配置的工作空间）、媒体上传目录（`get_media_dir()`）以及所有 `extra_allowed_dirs` 之下。

Shell 命令执行工具（`ExecTool`，`agent/tools/shell.py`）同样遵循 `restrict_to_workspace` 限制：
若该限制开启，且执行目录不在工作空间内，命令会直接拒绝执行。

**规则**：任何新增的路径处理逻辑，**必须**经过 `_resolve_path` 校验，或实现等价的 `allowed_dir` 权限检查。

## SSRF 防护

智能体工具发起的所有外网 HTTP 请求，**必须**经过 `validate_url_target` 校验（位于 `security/network.py`）。
默认会拦截：RFC1918 私有内网地址、链路本地地址段、云服务商元数据接口（含 `169.254.169.254`）。

唯一豁免方式：调用 `configure_ssrf_whitelist(cidrs)`，加载时从配置项 `config.tools.ssrf_whitelist` 读取白名单网段。

**规则**：禁止在工具中直接编写 `httpx.get` / `requests.get` 请求。
必须通过现有网络请求工具封装调用，或自行复刻 `validate_url_target` 校验逻辑。

## Shell 沙箱

`tools/sandbox.py` 提供可选的命令沙箱包装能力。
当前内置唯一后端为 `bwrap`（气泡沙箱），适用于容器化部署场景。
在 Windows 或未安装 `bwrap` 的物理机 Linux 环境下，命令将运行在原生 Shell 中，仅保留工作空间限制作为安全防护。

**规则**：新增沙箱后端时，需实现方法 `_wrap_<name>(command, workspace, cwd) -> str`，并在 `_BACKENDS` 中完成注册。

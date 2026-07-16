# TD-2026-003: EvolutionModuleManager 桥接路径未经 ConfirmationManager 审批

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-15T22:00:00+08:00 |
| 发现人 | 观雪 |
| 关联Spec | .trae/specs/restore-and-wire-design-modules/spec.md |
| 关联规则 | 规则18(安全边界最低要求) |
| 优先级 | P1 |
| 状态 | 已确认 |

## 详细描述
EvolutionControlPlane 的 `execute_action` 方法中,`force_cleanup` 和 `rollback_artifact` 的模块级委派路径(当 `target_id` 非空 / `artifact_type == "module"` 时)直接调用 `EvolutionModuleManager.force_clean_module()` / `rollback_module()`,**未经 ConfirmationManager 审批**。

spec 要求"模块包级回滚经治理层审批 — WHEN 用户请求回滚模块包 THEN EvolutionControlPlane 委派给 EvolutionModuleManager.rollback_module(),且需通过 ConfirmationManager 审批"。

当前实现中,非模块级的 `force_cleanup`(全局维护)和 `rollback_artifact`(artifact 级快照回滚)路径已有各自的治理逻辑,但模块级路径直接跳过了审批。

## 影响范围
- **影响文件**: `OriginAgent/agent/evolution_control_plane.py`
- **影响功能**: 模块包级 force_cleanup / rollback_artifact
- **潜在风险**: 用户可直接强制清理或回滚模块包,跳过审批流程,可能导致未经授权的模块状态变更

## 复现/验证路径
1. 构造 EvolutionControlPlane,调用 `execute_action("force_cleanup", target_id="some_digest", reason="test")`
2. 观察是否触发了 ConfirmationManager 审批流程(当前:否)
3. 同理测试 `execute_action("rollback_artifact", artifact_type="module", artifact_name="some_digest")`

## 修复方案(可选)
1. 在模块级 force_cleanup / rollback_artifact 分支中,先检查 `self.confirmations` 是否需要审批
2. 如果需要审批,创建 ConfirmationRequest,返回 `pending_confirmation` 状态
3. 审批通过后再委派给 EvolutionModuleManager
4. 参考现有 `submit_automation` 中的 `pending_confirmation` 处理模式

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-15 | 观雪 | 确认为 P1 技术债(安全边界),需优先修复 |

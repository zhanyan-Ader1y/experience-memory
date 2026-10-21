# 本仓库自用接入与回退

日期：2026-10-21。目标：先在 Codex 中用本产品开发本仓库，再接 Claude Code，最后形成共享经验、隔离控制状态的双端闭环。

## 1. 当前状态

**S0 本仓库接入准备已完成。** 两项实现与8项新增合同通过，独立只读审阅Spec/Quality均Approved、无发现。首次完整回归遇到的既有C1计时合同阻塞已按用户授权离线修复；修复后完整回归实际528项、521通过、7项未验证跳过，退出0，131项冻结代码/资源摘要无变化。详情见[本轮验证](../verification/project-self-use-readiness.md)与[计时修复验证](../verification/c1-appserver-timing-fix.md)。**本产品尚未在 Codex 或 Claude Code 中启用，本仓库尚未开始产品自用。**

旧插件为**未盘点、未切换**，不代表不存在或已停用。C1/C2/H1/A1/A2/X1 不因候选配置或 doctor 通过而升级；生产 MCP 身份默认拒绝，旧 exec 为 `C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED`，app-server 为 `C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING`。

## 2. 配置与固定核心

- [被动配置预览](../../examples/self-use/project-config.preview.json) 沿用原 Config 模式，`enabled=false`，只通过显式 `--config` 读取，不是根自动发现配置。
- 项目身份固定为 `local:experience-memory`，只用于本逻辑项目；移动检出位置不改变经验命名空间。无关项目不能复制此身份。以后增加 Git 远程也不自动改身份或迁移经验。
- `shared_scope=false`，`namespace_mapping=null`：不开放全局共享，不自动导入旧插件或历史命名空间。
- 固定入口为 [core.md](core.md)，开发规范仍在 [AGENTS.md](../../AGENTS.md)。项目核心最多 1024 UTF-8 字节，和通用协议拼接最多 4096 字节，保留句柄与正式区域余量。
- FileCoreLoader 不递归展开 Markdown 链接。核心中的 AGENTS 链接不表示 AGENTS 已被完整注入；宿主仍需正常加载自己的开发规则入口。

完整 additionalContext 仍须由生产路径按 8192 UTF-8 字节核验，包含固定协议、项目核心、句柄、留存标记和包装；不按 token 计算，核心超限失败、不截断。短核心检查不是完整载荷或原生送达证明。

预览里不放凭据、用户目录、会话标识或机器绝对路径。不要复制为根配置或仅翻转 enabled 来尝试接入：可信身份和原生门槛不会因此成立。

## 3. 安全的本地核对

在仓库根执行：

```bash
.venv/Scripts/python.exe scripts/launch.py doctor --config examples/self-use/project-config.preview.json --project-root .
```

目录外执行时，将解释器、`scripts/launch.py`、配置和 `--project-root` 的路径全部改为当前检出的绝对路径，不依赖已激活环境或 PYTHONPATH。

正常结果应包括：

| 字段 | 本地预期 |
| --- | --- |
| `enabled` | `false` |
| `local_checks_passed` / `runtime_checks_passed` | `true` / `true` |
| `local_project.status` | `prepared` |
| `local_project.project_key` | `local:experience-memory` |
| `local_project.remote_binding_confirmed` | `false` |
| `adapters.experience` / `control_state` | `NOT_CONFIGURED` / `NOT_CONFIGURED` |
| `adapters.session_runtime` | `local_only` |
| `mem0_authentication` | `not_checked` |
| `native_compaction` | `not_verified` |
| `capabilities` 各值 | `implemented_host_contract_unverified` |

这里的 prepared 只指项目身份和核心的本地检查，不是会话绑定。doctor 不认证 Mem0、不创建控制指针或远端绑定、不验证锁，也不安装 MCP/Hook 或恢复经验；退出 0 不是原生验收。输出不应包含核心正文或密钥值，credentials_available 即使为 true 也不证明认证成功。

## 4. 真实开发使用流程（接入通过后）

以下流程现在不能执行为成功的产品操作；须先通过对应宿主接入与项目切换门槛。

1. **开始任务/范围变化**：校验固定核心和当前可信绑定；按当前问题查询相关经验，不枚举全库。遵循产品[经验查询流程](../../skills/memory-query/SKILL.md)。
2. **精读核验**：对少量候选核对项目、状态、来源、当前指纹和适用条件。搜索候选不等于已经采用，读取会创建回执，不是无副作用查询。
3. **实际采用后登记**：确实用于判断、实现或测试后，使用当前有效回执登记用途和优先级。只有 confirmed 有效采用增加 U；查询、读取、恢复、压缩或原操作重试不增加 U。
4. **完成实现与验证**：再提议值得保存的可复用经验。在当前会话经用户确认本次候选后，按[经验维护流程](../../skills/memory-intake/SKILL.md) 验重、维护并真实回读；不默认授权以后无限自动写入。
5. **后续任务/会话复用**：新会话重新绑定，重新精读核验；真正使用后以新会话回执登记。共享经验不等于复用别的会话句柄、回执、U、窗口或检查点。
6. **纠错**：条件变化或发现错误时维护同一经验或将其失效。正式留存区决定当前有效集合，旧摘要不能复活已失效或淘汰记录。

根 `skills/memory-query` / `memory-intake` 是产品资源，不复制到 `.agents` / `.claude` 自动注册。verify-offline 是开发验证技能，不能建立产品身份或代替五工具。

## 5. 纳入与故障处理

经验应有明确触发、适用条件、可执行动作、来源和验证；一次性进度、历史测试数量、未证实推断不自动纳入 active。已有唯一规范的代码/文档不复制成第二份规则，必要时只保留触发和导航。

先语义验重，再核对稳定 experience_id。更新/失效沿用同一经验标识，并使用真实 record_id 和最新 expected_fingerprint；不能因语义搜索无命中就另建重复记录。

- **查询失败**：不是成功无命中；不转为全用户搜索或调用旧插件冒充本产品。
- **pending/unknown**：未确认保存；保持原 operation_id 和原参数，先定案原操作，不换 ID 重新 add，不虚构 record_id、回执或 U。
- **failed**：如实报告未保存/未采用；不得改写为空库成功。
- **必要 P1 无法核验**：暂停依赖它的动作；不依赖它的工作可以继续。
- **恢复缺少本次边界或检查点**：不取最近 ready 代替，不从历史 transcript 猜测身份或恢复集合。

非核心正文、摘要、活动集合以 Mem0 为持久来源。本地不保存候选待办、备用经验、回执正文快照或用于恢复的交接缓存；正常源码/设计文档照常维护，但不能替代工具保存成功。

## 6. 责任与切换检查

现在没有实际安装或切换。下表是后续必须审阅的证据项，不是安装授权，也不是已通过清单。

| 检查项 | 当前状态 | 切换前所需证据 |
| --- | --- | --- |
| Codex / Claude 实际目标版本 | 未检查 | 获授权后实际读取并核对支持合同；不能沿用历史版本推定 |
| 本项目当前经验责任方 | 未盘点/未切换 | 明确每宿主的唯一责任方，不从工具名称猜测 |
| 旧自动 Hook、检索、写入、缓存 | 未检查/未修改 | 只读核对必要配置结构及逐项目停用范围，不收集密钥或正文 |
| 目标配置路径与前后状态 | 未安装 | 精确路径、前态、差异、摘要和变更后摘要；用户已有修改不得覆盖 |
| Mem0 数据范围 | 未在线核验 | 本项目身份、合成测试与正常经验范围分开；旧映射不自动引入 |
| 共同控制目录与项目锁 | H1 未验收 | 两端同机共同目录、同名会话/操作 ID 隔离与实际互斥证据 |
| 操作者批准范围 | 仅 S0 离线开发 | 原生诊断、在线合成操作、实际安装和项目切换分别明确批准 |
| 原生身份与留存能力 | 未通过对应验收 | 工具身份/隔离与压缩留存分别记证，不用一个 verified 开关全开 |

未来盘点只记录必要的脱敏标识、路径和摘要，不收集任意全局配置、历史正文或凭据。不全局卸载旧插件、不清理其缓存或云端数据，不擅自合并命名空间。

## 7. 回退检查

本阶段只交付协议，尚无实际回退执行记录。具体命令须在目标配置和前态明确后单独审阅，不提供盲目复制/删除命令。

1. 停止本项目的新入口，避免继续创建业务操作；不要停止或卸载其他项目的责任方。
2. 核对当前目标仍与本次变更后的路径、身份和摘要匹配；不匹配时先报告冲突，不覆盖用户更改。
3. 仅恢复已审阅的本项目前态；对于原先不存在的文件，也只能处理确实由本次创建且仍匹配的目标。
4. 保留已确认云端经验、旧插件、失败报告和原生尝试账本；不以删数据、重置 U/检查点或返还名额伪装回退成功。
5. 验证本项目新入口已停用、原项目责任方恢复，且其他项目/宿主不受影响；证据不足明确未确认，不自动重试或切换模型/账户。

## 8. 验收与下一阶段

以下证据分层记录，不相互升级：静态资源、本地单元、SDK/Hook 合同、本地假宿主、真实 Mem0 API、真实原生宿主、真实开发任务使用。

S0 新增 6 项 profile/核心合同与 2 项 doctor 合同；不做实际宿主或 Mem0 调用。首次完整回归失败及授权修复后全套528项/521通过/7跳过的过程保留在[本轮验证](../verification/project-self-use-readiness.md)，历史 C1-T 数字不当作本次重跑结果。S0只完成离线接入准备，不升级任何宿主能力。

下一步继续设计/审阅 **C1-T 之后的受控原生诊断启动入口与生命周期验收**，明确版本、隔离配置、线程预热副作用、审批与停止条件后，再取得对应调用授权；不是现在直接启动模型任务。原有授权已消耗，不复用。

后续路线：Codex 真实来源与持久绑定 → Codex 留存和本仓库自用 → H1 共同隔离 → Claude 可行性/适配/自用 → X1 双端验收。双向“保存后读取并重新采用”、跨端回执拒绝、竞争冲突、失效不复活、单端停用与回退均需实际证据。

参考：
- [双宿主自用设计](../superpowers/specs/2026-10-21-project-self-use-design.md)
- [S0 实施计划](../superpowers/plans/2026-10-21-project-self-use-readiness-plan.md)
- [唯一任务导航](../superpowers/plans/2026-10-14-experience-memory-followup-plan.md)
- [兼容矩阵](../compatibility.md)
- [C1-T 离线交付与原生缺口](../verification/codex-c1-appserver-transport.md)

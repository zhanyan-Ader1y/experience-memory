# C1-E 有界离线执行器交付验证

日期：2026-10-19。

## 1. 结论

**C1-E 的离线开发、五任务审阅、整轮修复复审与控制器最终回归已完成。原生执行驱动仍明确 unsupported，不能据此开始 C1-N。**

本轮交付的是固定场景的离线有界执行器及测试，不是生产插件启用、真实 Mem0 验收或 Codex 严格恢复。`src/` 和 `hooks/` 与实施前摘要完全一致；当前仍为无首次提交的 `codex/skeleton`，未提交、推送、合并、建立远程或切换正常项目。历史失败、审计和旧 bundle 均保留。

## 2. 已实现的软件

| 单元 | 文件 | 关键行为 |
| --- | --- | --- |
| 服务前门禁 | [policy](../../tests/fixtures/codex_c1_policy.py)、[fixture](../../tests/fixtures/codex_c1_server.py) | metadata 根 callId；原始工具/参数在 SDK 查找与校验前准入；SDK 错误、未知工具、错序、重复和重叠请求冻结场景；正常五步与普通夹具模式保留 |
| 状态与预算 | [bundle](../../scripts/codex_c1_bundle.py) | review/installed 分离；非覆盖安装与精确隔离回退；A/B 顺序回退；先保留名额、不返还、不自动 B；最终锁内再次校验授权 |
| 只读预检 | [host](../../scripts/codex_c1_host.py) | 精确版本、有效配置、白名单 RPC、必需字段、未知启用项拒绝；限时/限量和脱敏；本轮仅本地假进程运行 |
| 独立证据 | [results](../../scripts/codex_c1_results.py) | 唯一原生 item 与 metadata callId 分开；真实 DTO、传输/业务审计、哈希和顺序关联；明确矛盾优先 failed，纯缺失为 unknown |
| 进程所有权 | [process](../../scripts/codex_c1_process.py) | Windows 挂起创建、加入本轮 Job 后恢复；整树终止/回收；180秒上限与清理预留、每行262144字节/总2097152字节/有界队列；不确认清理则 unknown |
| 编排与 CLI | [executor](../../scripts/codex_c1_executor.py)、[入口](../../scripts/run_codex_c1.py) | 默认 dry-run；作用域/动作分开；证据写入失败仍恰好一次最终记账；程序化假宿主测试缝不暴露给 CLI；原生 run 代码阻断 |
| 被动导出 | [prepare](../../scripts/prepare_codex_c1_review.py) | 全新目录、摘要/配置预览、无安装/无原生授权；单次有界读取同一份清单字节用于哈希和解码 |

五步只允许：bootstrap → 当前 status 成功 → 同项目有效种子句柄拒绝 → 外项目有效种子句柄拒绝 → 当前 status 再成功。U 必须保持整数0；种子明确不是历史原生会话句柄。业务前门禁不是阻止模型提出请求的承诺，而是阻止不合规请求进入业务。

## 3. 最终验证

| 证据 | 实际结果 | 层次/限制 |
| --- | --- | --- |
| 控制器最终完整离线回归 | **277 项，270 通过、7 跳过，262.547秒，退出0** | 七项仍未验证：1 live functional、4 real Mem0、2 native Hook/compaction |
| 最终修复者 C1 针对性测试 | 152 通过、0 跳过，250.365秒 | 修复SDK前置准入、同步/异步重叠和清单有界读取 |
| 最终整轮修复复审 | **规格 Approved、质量 Approved；I1/M1 均 Addressed；无剩余发现** | 先前任务的所有重要发现均经修复复审；无 parked residual |
| 当前代码摘要 | **108项与最终审阅快照一致** | `src/`、`hooks/`相对实施前无变化 |
| 控制器输出检查 | 无 Traceback、运行时/资源/语法/弃用警告、FAIL/ERROR | 完整原始输出保存在本计划 ignored 工作目录 |
| Windows 真实本地进程 | 已验证恢复前 Job 成员资格、Python 父子进程退出；另一次2秒预算观察耗时1.507196秒、job_empty | 本机离线进程证据，不是180秒原生模型运行或硬token预算 |
| 保留的SDK模拟场景 | 实际5条传输+5条业务审计、真实预期拒绝、A/B配置都已回退至隔离副本 | offline_simulation；本轮无B尝试、无自动重试，无真实Codex/Mem0 |

最终完整命令：

```bash
.venv/Scripts/python.exe scripts/test.py
```

原始输出：`.superpowers/sdd/2026-10-18-codex-c1-bounded-executor-plan/controller-full-offline-2.txt`；SHA-256：`94a963d377021ae3af62c224ac2c04209a7e48c4da17e18d4c5c0e2aadc5e4e4`。

机器可读封存：`.superpowers/sdd/2026-10-18-codex-c1-bounded-executor-plan/controller-closure-verification.json`。此前261/267项结果均为历史中间快照，不替代上述最终277项。

## 4. 最终被动材料与安全试读

新材料目录：`.superpowers/sdd/2026-10-18-codex-c1-bounded-executor-plan/final-passive-reviewed-20261019-2/`。

- 清单SHA-256：`f22f5b236ffc0d24edc221d95e91aa0004b0631f90c69d2c510c00cddf942d58`。
- 108项源码摘要；配置仅为A/B preview，实际config、approval、install receipt、attempts均不存在。
- `native_authorization={granted:false,remaining_model_tasks:0}`，`host_version_observed=false`。
- 实际导出：review_prepared、native_model_tasks_started=0、configuration_installed=false。
- 实际只读复核：review_unchanged、native_authorization_granted=false。
- 实际默认dry-run：native_dispatch_supported=false、native_evidence_claimed=false、model_tasks_started=0。

无需宿主调用的安全试读（本轮已实跑）：

```bash
.venv/Scripts/python.exe -I scripts/run_codex_c1.py --bundle "A:/CodingSpace/Ai/experience-memory/.superpowers/sdd/2026-10-18-codex-c1-bounded-executor-plan/final-passive-reviewed-20261019-2"
```

不要将预览复制进正常项目。旧目录的清单与代码摘要可能已过时，只作为历史证据，未覆盖或重签为新版本。

## 5. 原生阻塞与历史结论收窄

固定 `rust-v0.163.0` 源码显示：

1. [exec配置构建](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/lib.rs#L586-L631)写入 harness `AskForApproval::Never`。
2. [配置优先级](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/config/mod.rs#L3852-L3855)先取该override，再考虑请求配置；TUI的审批参数不等于exec继承的审批能力。
3. [AutoReview例外](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/lib.rs#L779-L810)会改变审批/信任语义，本项目不采用该方式绕过约束。

因此仅传 `-c approval_policy="on-request"` 不能满足既定人工审批要求。原生run在真实预检、reservation、Popen之前返回 `C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED`；即使增加一个授权记录也不能解锁。没有偷偷换成app-server turn驱动或修改宿主审批策略。

**历史三个探针只证明传入了所请求的配置，不能再描述为已验证exec最终有效的人工on-request策略。** 其元数据、Hook关联、任务结果和失败材料仍有效地描述那些观察，不改写或重跑；本轮实际新增原生任务为0，真实Mem0调用为0。详细校正见 [契约探针](../contract-probes.md#2026-10-19-c1-e-发现的历史审批表述校正)。

## 6. 执行中作出的裁决与代价

以下按执行账本顺序列全，不隐去范围调整：

1. 在现有无HEAD检出中以源码快照替代提交范围，不自动创建worktree/commit；代价是共享工作目录，靠顺序实现与摘要审阅隔离。
2. 保留本计划账本/报告/快照，不清理唯一证据；代价是ignored磁盘占用。
3. “开始执行”限定为C1-E离线实现与逐任务子Agent，不扩展成实际安装/宿主预检/分派；代价是原生集成另行授权。
4. NativeEvent可兼容地扩充必要结果/哈希字段，避免六字段DTO无法表达真实证据；代价是跨模块接口耦合增加，已纳入审阅。
5. 参数规范哈希与ID-only审计以Task1定义为跨任务合同；代价是后续若需变化必须同步验证，不各自猜算法。
6. 回退用原子隔离移动并保留字节代替直接unlink；代价是多保留一份本轮配置证据，不自动删除隔离副本。
7. 代码中补齐native-c1-wiring/inspect动作验证，模拟授权不得启动原生CLI；代价是授权校验审阅面增加，本轮未创建/使用真实原生授权。
8. 允许既有测试仅在新建可丢弃合成根内初始化/测试Git，并只读获取固定版本官方源码；代价是临时仓库/下载数据，不触及当前检出的提交/远程。
9. 发现exec强制Never后，在分派前明确unsupported，不换AutoReview或其他驱动；代价是必须另立审批可验证的原生transport设计，不能立即C1-N。
10. 狭义修复host的不存在异常类型，避免OSError被AttributeError掩盖；代价是早先模块的小改动重新纳入集成审阅。
11. 补齐A回退后继续B回退的专用状态验证，运行前installed验证不放宽；代价是回退peer证明复杂度增加。
12. finish_attempt增加兼容可选expected_approval，执行器必传且在最终锁内校验；代价是生产者接口扩展，已覆盖锁边界撤销和真实SDK回归。

## 7. 尚未交付/验证的能力与下一步

- **原生审批可验证的执行驱动尚未实现。** 下一步先设计该transport的审批、工具调用与事件证据通道，再进行受控离线实现/审阅；不是直接授权现有exec跑两个任务。
- C1-N/C1-P、真实Mem0生产候选绑定、旧原生句柄/回执、Hook上下文送达、C2压缩、H1双宿主隔离和Claude Code接入都未在本轮通过。
- Python3.10没有运行时实测；相关导入兼容只做局部回归。本轮实测Windows/Python3.13.1/MCP2.3.0。其他OS不提供父进程级假成功回退。
- 不声称抵御同账户恶意宿主/管理员编造一致日志、跨机器锁、远程CAS/事务、断电耐久、瞬时终止或硬token预算。
- 回退/安装实际测试仅新建临时合成根，未改用户正常宿主配置、旧插件或信任；下一次宿主操作仍需范围明确的授权。

设计导航：[后续路线](../superpowers/specs/2026-10-18-development-roadmap-design.md)；实施记录：[C1-E计划](../superpowers/plans/2026-10-18-codex-c1-bounded-executor-plan.md)。

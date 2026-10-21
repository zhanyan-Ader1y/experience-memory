# C1 原生接线：离线准备与执行审阅

日期：2026-10-18 初始准备；2026-10-19 C1-E 离线执行器更新。范围：**夹具、离线 SDK 合同、Windows 拥有进程监督、被动执行清单**。本文不是原生执行授权；生产 CLI 仍默认拒绝。C1 整体与 C2 不因本项交付通过。下方 2026-10-18 的测试数字和旧 bundle 均为历史记录，不代表当前最终汇总。

## 已实现的链路

[专用夹具](../tests/fixtures/codex_c1_server.py)固定一个受审阅的合成 Git 根，检查两个 A/B 根的本轮标记；仅接受 Codex 0.163.0 元数据契约。每请求经过现有 `codex_request_host`、真实 `SessionBinding` 和应用服务，五个记忆工具直接复用生产 `create_server`，不复制授权逻辑。

测试专用 `c1_bootstrap` 只声明 `probe_nonce` 参数，不接受项目或会话身份参数。它从传输 `_meta` 解析当前身份并绑定，返回当前句柄以及两个合成种子句柄。种子各自在原所属会话/项目中通过真实 `memory_status` 后才返回 `owner_status_verified`，用于排除“随机无效字符串恰好被拒绝”的伪负例。它们**不是此前原生 Codex 会话留下的句柄**。

后端仅为进程内 `PlatformDouble`。真实仓储与控制服务仍被执行，但 HTTP 调用由该内存替身承接；非核心正文/集合不写入夹具审计。本地临时状态是生产指针格式的标识/状态/指纹和锁；正常退出时清理。强制结束进程可能留下标识状态文件，不构成自动清理保证。

请求审计仅含 schema、事件、nonce、invocation ID、时间、解析状态、已解析 session ID、根路径哈希或拒绝码。它只证明**传输解析**，不独自证明工具业务成功；后续原生执行器必须另行保存脱敏的真实工具结果。正文、任意传输扩展、候选对象及异常原文不进入审计。原生宿主可能按自己的机制保存合成工具调用记录，不将此误称为插件经验缓存。

## 离线验证

新增 5 项固定根夹具单元测试、5 项真实 SDK stdio 合同测试、5 项审阅导出器单元测试，分别观察 RED→GREEN。

覆盖：固定根与本轮标记；当前绑定幂等；种子原所属身份有效/当前身份拒绝；两个有效传输会话之间的句柄拒绝；缺失、畸形及外来工作区；nonce 错误；参数伪造不能替换传输身份；异常后上下文恢复；真实五工具的内存写入/精读/采用与 U；审计不包含正文；配置预览字节哈希；已有材料不覆盖；预览/源码摘要/标记变化时只读校验失败。

首轮完整离线回归：`.venv/Scripts/python.exe scripts/test.py`，140 项，133 通过、7 项原有在线/原生未验证跳过，37.754 秒，退出 0。独立规格/质量审阅均 Approved，无缺陷发现；控制器审阅后最终完整复验同为 140 项、133 通过、7 跳过，37.630 秒，退出 0。85 个已有代码/资源文件及 6 个新增文件与审阅摘要一致。详见 [契约探针](contract-probes.md#c1-原生接线的离线准备2026-10-18)。跳过不是通过。

本机已生成 ignored `.superpowers/codex-c1-wiring-20261018/review-bundle-1/`，含清单与 A/B 配置预览；最终只读校验返回 `review_unchanged`、`native_authorization_granted=false`。未安装配置、未运行原生任务。

## 被动审阅材料

[导出器](../scripts/prepare_codex_c1_review.py)创建全新的合成 A/B Git 根、本轮标记、两份 `*-config.preview.toml` 和 `launch-review.json`。它只运行本地 Git 初始化及环境/文件检查，**不调用 Codex，不安装 `.codex/config.toml` 或 Hook，不修改信任**。不复用旧探针目录。输出目录已存在时拒绝，不覆盖失败记录。

生成新的审阅材料（只准备，不执行）：

```bash
.venv/Scripts/python.exe -I scripts/prepare_codex_c1_review.py --output "A:/CodingSpace/Ai/experience-memory/local-evidence/c1-wiring-review-new"
```

清单保存：目标版本、当前 Python/MCP 环境、项目根/标记哈希、全部相关源码摘要、配置预览实际落盘字节哈希、工具顺序、停止条件和未证明事项。`host_version_observed=false`：没有调用 Codex 读取版本，目标版本不能冒充本机当前版本。`native_authorization.granted=false`、剩余模型任务为 0。

只读复核（替换为已生成目录）：

```bash
.venv/Scripts/python.exe -I scripts/prepare_codex_c1_review.py --verify "A:/CodingSpace/Ai/experience-memory/local-evidence/c1-wiring-review-new"
```

校验会重新计算源码、环境、标记、预览及清单内容；变化则非零退出，不替换文件。不读取清单中指定的任意路径来执行程序。摘要只是变化检测，不是签名或对恶意本机客户端的认证。正常安装后不再满足“未安装审阅材料”校验，应由未来执行器以独立安装记录核对，不能关闭校验继续。

## 待单独授权的原生提案

| 项目 | 提案上限（当前未授权） |
| --- | --- |
| 目标 | 两个新合成项目 A/B，不切换正常项目 |
| 模型尝试 | 每项目 1 次，总共最多 2 次；失败也计尝试，不自动重试 |
| 工具调用 | 每任务恰好 5 次，总共最多 10 次 |
| 顺序 | bootstrap → 当前 status 成功 → 同项目种子句柄拒绝 → 外项目种子句柄拒绝 → 当前 status 再成功 |
| 允许调用 | 只允许 `c1_bootstrap` 和 `memory_status`；夹具暴露五工具不代表本轮允许调用它们 |
| U | 前后均为 0；不执行 memory_write/read/use |
| 时限 | 每任务 180 秒，配置启动/工具超时各 15 秒 |
| 隔离 | 私有 stdio、进程级禁用旧插件/记忆/Hook/非必要工具；有效配置须现场核验 |

**导出器不是执行器。** C1-E 已实现离线有界监督、唯一调用/结果及双审计核验，但下述精确版本的原生审批语义缺口使原生分派仍被代码阻断；不能把提示词、预检配置或离线通过当成原生就绪。实际安装、只读宿主预检及任何后续原生执行仍需分别范围明确的授权。

执行前必须：

1. 获得针对本轮根、任务数和调用范围的明确授权；此前 3 次原生尝试授权均已使用，不能沿用。
2. 重查当前 Codex 精确版本为 0.163.0；版本变化即停止，不自动升级/降级或放宽解析。
3. 核对源码/配置字节、本轮根及标记、专用 Python/MCP；项目信任由用户按正常宿主流程完成，不代写信任。
4. 获授权后只安装已审阅的合成项目配置；检查有效配置确实只有合成 MCP，Hook、旧插件和无关工具均不启用。发现新的全局入口也必须停止处理，不能靠旧的禁用列表推断隔离成立。
5. 从子进程环境移除 `MEM0_API_KEY`，不读取凭据；重新读取 ordinary usage 状态。保持正常审批和只读沙箱，不开启额度回退、不换账户/模型。

任何版本/哈希/身份不符、额度/登录/审批失败、超时、额外工具/重复调用/顺序错误、非预期结果、缺少审计或 U 变化，都停止并保留失败/unknown 记录。不得自动补跑 B 或重试 A 来凑成功。

## C1-E 离线有界执行器（2026-10-19）

代码分层为 [进程所有权](../scripts/codex_c1_process.py)、[编排](../scripts/codex_c1_executor.py)、[薄 CLI](../scripts/run_codex_c1.py)，复用独立 bundle、只读预检和结果验证模块；不改生产 `src/` 或旧 Hook。

- 默认/`dry-run` 仅核验 bundle、源码/预览/标记和已有安装回执，不调用宿主、不安装、不消耗名额。示例只读命令（目录替换为**新导出的被动 bundle**）：

  ```text
  .venv/Scripts/python.exe -I scripts/run_codex_c1.py --bundle "A:/absolute/fresh-review-bundle"
  .venv/Scripts/python.exe -I scripts/run_codex_c1.py --bundle "A:/absolute/fresh-review-bundle" dry-run
  ```

- `install --approval <bundle 内绝对路径>`、`inspect --project a --executable <绝对 exe>`、`run --project a --executable <绝对 exe>`、`rollback --project a` 各自核对明确 action，原生 CLI 只接受 `native-c1-wiring` scope。安装后动作使用本轮保存的同一审批记录与独立回执；确认引用只是审计指针，不是签名。本文不生成或提供原生审批记录。`inspect` 授权不隐含 `run_project`，反之亦然。
- `offline-simulation` 仅用于可信程序化测试注入的两个工厂，不能授权生产 CLI 的安装、宿主读取、运行或回退。没有 CLI origin/factory/import/command-prefix 开关；可执行文件不从清单读取。没有 all/loop/retry/force/skip-checks；绝不自动 B。
- Windows `OwnedProcess` 用标准 Job Object API 和 `Popen(CREATE_SUSPENDED)`，先加入本轮不允许 breakaway 的 Job，再恢复唯一初始线程。加入/恢复失败不得执行 Python 用户代码；不支持安全所有权时拒绝，不回退成只杀父进程。关闭时 `TerminateJobObject` 并核验根退出和 Job active count=0，不能确认则顶层 `unknown`、停止本轮。真实本地父/子 Python、挂起期间尚无首条指令标记、Job 成员关系、二者退出均经过实际测试，不是原生 Codex 证据。
- stdout/stderr 各自有界队列（16 行），每行最多 262144 字节（含换行），两流合计最多 2097152 字节；执行/读取截止最多 180 秒，其中预留至多 1 秒关闭。不是硬 token 预算或瞬时终止保证。stderr、原始 DTO/句柄/正文不落 executor 报告；只留字节数、SHA-256、白名单 ID/状态/关联。子环境移除 `MEM0_API_KEY`。
- 顺序为审批/安装校验 → 经明确 `inspect` 授权的只读预检 → 再校验字节/审批 → 独占持久 reservation → 才启动进程 → reducer/实际双审计核验 → 确认全 Job 关闭 → 独占报告/终结名额。创建进程异常也不退款、不重试。不能完成严格终结时保留已消耗 reservation 和单独 unknown 证据，不为填报告而放宽验证。
- 回退只原子改名本轮创建且字节/身份仍匹配的配置至独占 quarantine，**不删除 quarantine**。可依次回退 A/B；已回退 peer 必须有完整匹配的 rollback 记录、原文件身份/摘要和仍存在的 quarantine。任意缺文件、报告/回执/隔离副本改变、目标用户修改均拒绝。运行的 `installed` 验证不接受“已回退”状态；回退不重置名额。

### 原生分派的明确阻塞：0.163.0 exec 不保留人工 on-request

只读取固定 `rust-v0.163.0` 源码，**没有运行真实 Codex，包括 `--version`**：

1. [exec parser](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/cli.rs#L15-L85) 及 [共享参数](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/utils/cli/src/shared_options.rs#L9-L77) 不提供 exec 的 `--ask-for-approval`。该参数属于 TUI，不能编造 exec 选项。
2. [exec 配置构建](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/lib.rs#L586-L631) 设置 harness `AskForApproval::Never`；[配置优先级](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/config/mod.rs#L3852-L3855) 先采用该 override，之后才考虑 `cfg.approval_policy`。所以 `-c approval_policy="on-request"` 只是请求配置，不能证明 exec 有效策略。
3. [例外](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/lib.rs#L779-L810) 只对 AutoReview 移除 headless override；它改变审批/信任行为，本任务**不以 AutoReview、绕过、换模型或改 transport 驱动作为解决办法**。

因此 `run_project(...execution_origin="native_codex")` 在预检、reservation 和进程创建前明确 `C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED`；即使日后有原生 scope 审批，也不能解除代码门。`dry-run` 和阻断报告都返回 `native_dispatch_supported=false`。只读 `inspect` 的 `preflight_ready` 不是 exec 原生就绪或任务授权。模拟测试使用 parser-compatible argv、真实 Python 假宿主和真实 MCP SDK，以独立元数据生成五步，固定标为 `offline_simulation`、`native_evidence_claimed=false`；不能把源码结构测试当成实际原生证据。

历史三个探针的元数据/Hook/任务结果与失败文件原样保留；先前“正常 on-request”表述应缩小为**启动参数请求的配置**，不是 exec 已有效执行人工 on-request 的证明。没有重跑历史探针或增加原生名额。解除阻塞需另行设计、授权并审阅能证明审批语义的原生 transport，C1-E 交付不表示“可立即跑两个原生任务”。

## C1-E 最终复验与封存

五任务和整轮修复复审均完成，最终规格/质量Approved，无剩余发现。控制器最终完整离线回归277项、270通过、7项原有未验证跳过（262.547秒），退出0；源码108项与审阅快照一致，生产src/hooks未改变。新被动材料仅preview、未安装、原生授权false/0，默认dry-run明确native unsupported；旧清单和历史回归保留。

详见 [交付验证](verification/codex-c1-executor.md)，含最终材料摘要、完整裁决和原生驱动阻塞。完成本节离线工作不代表可直接开始原生A/B任务。

<a id="c1-t-离线候选2026-10-21待独立审阅"></a>

## C1-T 离线通道交付（2026-10-21）

用户在2026-10-20审阅设计/计划后批准本轮离线实现，不复用历史原生尝试授权。五任务与整轮修复复审已完成，最终Spec/Quality均Approved，无剩余发现；控制器最终完整回归520项、513通过、7项未验证跳过，1088.950秒，退出0。123项代码与最终审阅快照一致，39项受保护文件未变。新被动基础清单摘要20bbc5f97b25d3c3e675b0800bc4be0cd0a2eced34614f1fb98c08adf3e78703、sidecar摘要821d6d42510be9398dc119689cb3f111158b6d6fb49c53916ba633b3fd403ce3，两层校验通过、未安装/授权。实际迭代证据仍保留在本计划SDD工作区；此前277/418/504/508项为历史快照。完整结果和裁决见 [交付验证](verification/codex-c1-appserver-transport.md)。

- `run_appserver_simulation(snapshot, project, approval, receipt, contract, *, process_factory, version_proof, execution_origin="offline_simulation")` 仅为可信程序化本地测试缝。工厂必须返回真实 OwnedProcess，候选不提供默认命令或任何真实 Codex argv；`version_proof` 只是本地假进程的固定版本字符串，不能称为真实版本证据。
- 初始化/config/hooks/usage 在同一真实双向管道完成；30 秒预检 watchdog 从同一个最多 180 秒绝对截止取预算，并在结束前取消、join。复核字节/审批/sidecar 后实际 reserve 落盘，才发 thread/start；实际策略核对后才发唯一 turn/start。报告把 intent、实际写入 ACK、False/True/None 歧义与真实 pending client ID 分开。
- Session 和事件 owner 分别观察原始实际 RpcMessage。尚未验证 turn 时最多 16 条/受总字节上限约束的原始消息仅留内存；绑定后按原 wire 顺序重放，保留缺字段下仍存在的反向 scope 事实。唯一容许的 collecting 缺失为明确列出的三种“尚未完成”摘要；turn 结束/EOF/deadline 时全部重新成为非成功证据，其他 unknown 或矛盾立即停止。正文/完整 wire/异常原文不落报告。
- 已知审批只发精确 cancel/空 permissions 拒绝，随后关闭；interrupt 需要实际 verified turn 且无 pending client。未知交互不猜响应；完整五步之后的迟到 error/请求仍走 Peer，不能被 expected EOF 隐藏。
- 实际 MCP SDK 调用现有 bounded fixture 产生两种独立审计，各 5 条；v2 item ID、metadata.callId 与审计 invocation ID 分开核验。正常关闭需真实 stdin ACK、各流 EOF、自然 exit0、无强杀、无 sticky I/O failure、全部 I/O worker 停止及 Job empty。没有用旧 exec validator 填 fake exit0。
- `a/b-appserver-evidence.json` 独占保存为 pending_finalization（candidate_status 不是最终成功）；既有 `a/b-report.json` 与 ledger 才是最终名额结果。晚授权由既有 finish(expected_approval) 在锁内复核。证据文件/目录/reparse 或最终化冲突都不覆写旧材料、不重试 finish、不退名额、不自动 B。协调器另批准 reserve_attempt/finish_attempt 的可选 deadline=None 扩展：候选传同一原始绝对预算，真实锁等待最多min(原10秒,剩余时间)，省略参数的旧调用不变；过期或实际锁竞争只产生unknown/unconfirmed，不延长预算。实际reservation持久化后若读取不安全/不明，报告attempt_reserved=null、finalization未确认，不猜DTO调用finish；保留原字节、实际清理和独占best-effort unknown证据。文件系统原子操作/fsync不能安全抢占，不声称对任意OS文件停顿的硬墙钟上限。
- 新导出器只调用既有 prepare 创建新 schema2 基础 bundle，再独占写固定 schema1 `appserver-contract.preview.json`；实际基础字节摘要、同一源码映射、严格完整 profile/规范字节、原 review-only 校验共同绑定。`native_entry_enabled=false`，状态固定 `C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING`；没有 native run/origin/factory/import CLI。

必须区分四层：**源码合同、普通 SDK 夹具、真实本地假 app-server 候选模拟、真实原生 Codex**。前三层不证明第四层的线程预热、策略快照、模型、原生 callId 或 EOF/取消；C1整体/C2及生产启用仍未通过。旧 exec `NATIVE_DISPATCH_SUPPORTED=False` / `C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED`、旧 schema2 和历史失败材料均保留。正常项目/全局配置、src/hooks、依赖和真实账户/额度/信任/Mem0 未改变。

## 未来应保留的证据与限制

必须将原生 `thread.started` ID、唯一工具调用 ID/顺序、真实工具结果、五条 ID-only 审计的 session/固定根哈希及执行前摘要关联。只有模型回答 `C1_WIRING_DONE`、审计文件存在或 SDK 离线通过，都不够证明原生拒绝成立。

即使该提案未来通过，仍只证明实际原生请求下的当前绑定及**合成种子**拒绝，不证明跨进程保留下来的旧原生句柄、真实 Mem0 路径、任意客户端认证、Hook 上下文送达或压缩。生产启用、C1 最终门槛和 C2 需要分别定案；Claude Code 接入不在本项范围。

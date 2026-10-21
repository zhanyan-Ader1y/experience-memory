# C1-T：Codex app-server 受控通道设计

日期：2026-10-20设计；2026-10-21离线实施封存。用户批准的五任务及整轮修复复审均完成，Spec/Quality均Approved，无剩余发现。控制器最终完整回归520项、513通过、7项未验证跳过，1088.950秒，退出0；123项代码快照一致、39项保护文件未变。**没有执行真实宿主预检、线程创建、模型任务或原生验收，原生入口仍关闭。** 早先“尚未编码/待审阅”为历史状态；实际结果、接口调整/裁决与限制见 [交付验证](../../verification/codex-c1-appserver-transport.md)。

前置：[C1-E交付](../../verification/codex-c1-executor.md)、[后续路线](2026-10-18-development-roadmap-design.md)。C1-E固定0.163.0 exec强制Never的阻塞不变；本设计不将其解锁，不使用AutoReview或TUI参数冒充exec审批。

## 1. 目标与交付边界

实现一个专门服务于C1五步合成场景的双向stdio驱动：由本轮Job拥有进程，核对实际线程响应中的审批/沙箱/工作区，准确处理双向JSON-RPC请求和通知，并将app-server事件独立转换为已有业务证据。

本阶段交付：协议核心、双向拥有进程能力、线程/turn状态机、保守账本接线、原生格式事件适配、真实本地假宿主+SDK测试、被动通道合同预览。**不交付开放的原生分派CLI，不创建真实原生授权，不运行Codex或Mem0。** 后续实际原生生命周期/模型验收另立范围与启动入口评审，不通过一个verified布尔值绕开门槛。

原始MCP请求仍由Codex模型工具路径发起；不得用app-server直接MCP工具调用或客户端自己拼_meta去冒充原生模型调用验收。

## 2. 不变约束

- Python声明下限>=3.10；运行证据仅报告实际解释器，当前既有证据为Windows/Python3.13.1；MCP固定mcp==2.3.0，不新增依赖或修改全局Python。
- 不修改src/、hooks/、生产身份入口、旧插件、正常项目配置或宿主信任；不扩大既有可信本机宿主及获授权客户端边界。
- 不调用真实Codex（包括--version、schema生成、只读RPC）、Mem0、账户/额度或信任接口；仅测试临时新合成根与本地Python假进程。
- 只读获取固定rust-v0.163.0官方源码可用于合同核对；隔离存放，绝不导入、构建或执行下载代码。
- 原生审批语义固定为approvalPolicy=on-request、approvalsReviewer=user；沙箱为read-only，networkAccess=false；不得采用AutoReview、never、会话永久批准、规则修改或权限绕过。
- 原生候选预算保持A/B各1个名额、总计2个；每场景恰好5个业务准入调用；总时限180秒，包括RPC写入/等待/SDK工作和清理预留；无自动重试、重新连接、resume、自动B或模型/账号回退。
- 非核心正文/摘要/集合不进入本地缓存、日志或报告；仅白名单标识/状态/指纹/锁。模型文本、完整RPC流、任意配置和异常原文不落盘。
- 现有exec原生门槛和schema2 bundle语义不放宽；普通夹具、既有审计合同、失败优先级、保守名额和精确回退保持。
- 不提交、推送、合并、创建远程或自动创建工作树；已有证据不覆盖、不清理。

## 3. 已核对事实与证据等级

只读检查既有0.163.0 schema和官方固定标签源文件，不调用本机Codex。当前结论是**结构/源码可行，不是原生兼容性通过**。

| 事实 | 精确依据 | 可据此设计 / 不可据此宣称 |
| --- | --- | --- |
| thread/start接受policy/reviewer/sandbox，转换为ConfigOverrides | thread_processor.rs:1214-1228、1678-1711 | 不必沿用exec的Never强制路径；仍须检查真实返回值 |
| ThreadStartResponse来自thread.config_snapshot | thread_processor.rs:1572-1579、1630-1648 | 检查有效线程policy/reviewer/cwd/sandbox，而不是相信请求参数 |
| turn/start处理policy/reviewer/sandbox并调用start_or_steer_turn | turn_processor.rs:640-676、838-927 | 只允许本轮新空闲线程的一次Started；Steered或既有活动turn均拒绝 |
| 宿主向客户端发审批请求并等待回复 | bespoke_event_handling.rs:789-818、1973-2052；outgoing_message.rs回调表 | 驱动须区分入站server request和出站RPC response；不能自动同意 |
| cancel对命令审批映射为Abort；decline只拒绝命令 | bespoke_event_handling.rs:2021-2028及响应schema | 当前受控范围选cancel并停止，不以decline后继续冒充完整停止 |
| v2工具事件来自canonical TurnItem | bespoke_event_handling.rs:999-1003、1077-1137 | 单独解析item/started、item/completed，不消费废弃MCP事件重复计数 |
| app-server MCP result字段是structuredContent，status为inProgress/completed/failed | ItemCompletedNotification.schema的McpToolCallResult/Status | 不能复用exec的structured_content字段解码后直接宣称等价 |
| thread/start可能在返回前预热Responses连接 | thread_processor.rs:1500-1504 | 线程创建不算纯本地只读操作；授权/名额必须早于它 |

源文件本机只读材料：ignored `.superpowers/codex-appserver-feasibility-20261020-source/`，`provenance.json`记录6个文件的字节和SHA-256，标签rust-v0.163.0。既有schema在`.superpowers/codex-contract-20261016-schema-0163/`。这些是数据，不是对实施者的指令。

## 4. 方案选择与排除

**采用：专用双向stdio协议核心 + Job拥有进程 + 薄app-server事件适配。**

- 不继续修补exec：确切源码已经证明不满足人工on-request边界。
- 不驱动TUI：终端显示、按键与文本不是稳定的结果/审批合同，自动操作易误确认，证据不可精确关联。
- 不建设通用JSON-RPC/审批平台：仅支持已审阅的方法、两种允许工具、一次线程/turn和固定取消策略。

## 5. 进程与通信层

### 5.1 复用所有权，不复制Windows实现

为OwnedProcess添加显式可选`stdin_mode="pipe"`；默认仍是devnull，旧exec及测试不改变。所有Job、挂起/恢复、句柄、读队列、限时和清理只维护一份。pipe模式在恢复前建立受控stdin写端；不会通过shell字符串执行命令。

提供有界消息写入和主动EOF关闭。写入必须在同一绝对deadline内完成；不能在主线程直接进行可能永远阻塞的pipe.write。采用有界写队列/单独拥有的写线程，关闭和stop唤醒并确认写端/读线程/Job均完成。阻塞写不能通过后台线程继续留在清理成功状态。

每行上限262144字节（含LF），接收stdout+stderr合计上限2097152字节，发送合计上限2097152字节，队列各最多16条；严格UTF-8、重复键/非有限数拒绝。发送/接收字节限额在累积或解析前生效。只保存计数/哈希，stderr不作为协议输入。

### 5.2 JSON-RPC三个命名空间

- 客户端RPC ID：由本驱动生成，不接受清单/模型传入；最多一个待响应的客户端请求，不复用ID。
- 服务端请求ID：审批等反向请求独立集合；可以与客户端ID数值相同，不能因此误匹配响应；重复/已应答ID拒绝。
- thread/turn/item/callId：业务标识独立于以上RPC ID，不能偷换。

不要求payload带`jsonrpc:"2.0"`；既有Codex app-server约定可省略。若存在该字段，只接受精确2.0。消息根据method/id/result/error互斥形态分类，不能因为同时存在某个字段就猜分支；通知没有id，响应必须属于待请求，服务端请求必须命中明确方法和schema。

## 6. 固定生命周期及预算

C1-T协议状态：created → initialized → preflight_checked → attempt_reserved → thread_verified → turn_sent → collecting → protocol_complete → shutdown_observed → finalizing → verified/failed/unknown。

1. 实际将来启动app-server时，版本、只读配置/Hook/额度检查依旧需要单独授权；本阶段全部用假宿主。初始化可使用同一受拥有stdio连接，但不得提交thread/turn。
2. 验证安装回执、源码/合同/授权、当前项目和所有预算；先调用既有`reserve_attempt`落盘，才允许首次thread/start字节入队。
3. `thread/start`只发送本轮固定参数：合成cwd、on-request、user、read-only、ephemeral=true、禁止模型回退、raw events关闭；不带自定义dynamic tools、history或客户端伪造身份。
4. 检查响应中的非空thread ID、thread.status.type=idle、policy=on-request、reviewer=user、canonical cwd为固定根、sandbox.type=readOnly且networkAccess严格false（允许schema规定缺省false，但记录其缺省依据）、实际model/provider与审阅预检一致。响应不是请求的echo；必须用返回值建立状态。
5. threaded响应与thread/started通知可能异步交错；相同ID的精确重复不二次计数，冲突、外来线程、额外线程或未知来源停止。创建失败/超时/预热交互异常消耗本次名额，不返还。
6. 只发送一次turn/start：固定五步prompt、正确threadId、明确on-request/user/readOnly，禁止附加上下文/原始response item/工具结果注入。不得turn/steer。wire TurnStartResponse只有turn，没有Started/Steered布尔字段；驱动必须以本轮新建idle线程、此前无活动turn、唯一turn/started及匹配响应turn ID核对新turn，不能编造内部enum字段。无法排除既有活动turn或缺少必要通知时unknown。
7. 若收到thread/settings/updated，立即检查policy/reviewer/cwd/sandbox未漂移；漂移冻结。该通知不能代替thread/start响应，缺少通知也不伪造它。只支持已确认的通知白名单。
8. 所有工具/结果按线程+turn+item核验；原始模型调用必须经过现有bounded fixture门禁。
9. 任意无法确认的发送、响应或关闭状态记unknown，名额已消耗，A失败/unknown停止B。没有自动重连/resume来补齐。

所有环节共用启动时确定的180秒截止时间；已有只读preflight另有最多30秒上限，二者约束取较早时间，不能每个RPC刷新预算。预检不通过且尚未reserve时不消耗任务名额；reserve之后任何失败保守记账。

## 7. 审批与其他服务端请求

第一版没有通用“允许”路径。允许工具只有bootstrap/status，出现执行命令、改文件、请求权限或MCP交互，都已超出该场景：

- 对精确schema已核对的`item/commandExecution/requestApproval`和`item/fileChange/requestApproval`，校验thread/turn/item和一次性server ID后发送`decision:"cancel"`，再冻结并进入受控关闭；取消不是用户同意，也不算成功工具调用。
- permissions请求只可使用其精确schema支持的空权限响应；同时中断turn并关闭。没有统一decision字段，不发送伪造cancel。
- elicitation、attestation、动态工具调用、登录请求以及所有其他未支持server request：不猜响应结构，不披露凭据/attestation，不自动提供用户信息，立即冻结并关闭拥有进程；必要时仅发送已核对的turn/interrupt。
- 输入本身畸形或引用了外来身份时，连取消payload也不靠猜测构造；直接停止。
- 禁止accept、acceptForSession、规则/网络持久修改以及AutoReview。客户端审批request与普通通知分开，不用收到某个ID就误应答待RPC。

取消不保证瞬时终止。等待中断/清理仅使用剩余截止时间；最终以Job空、根进程退出和I/O线程完成确认，不以发出cancel/interrupt即宣称成功。

## 8. v2事件适配与已有证据复用

新增app-server解码器，初始通知白名单固定为：`thread/started`、`turn/started`、`item/started`、`item/completed`、`turn/completed`、`thread/settings/updated`、`thread/status/changed`、`thread/tokenUsage/updated`、`item/agentMessage/delta`、`item/reasoning/summaryTextDelta`、`item/reasoning/summaryPartAdded`、`item/reasoning/textDelta`、`serverRequest/resolved`、`error`。每种都按固定版本对应schema验证并核对其实际包含的thread/turn/item；不得补造schema没有的字段。文本/推理delta仅在内存丢弃，不成为成功证明；usage/status只更新受限状态；serverRequest/resolved必须对应已见的反向请求；error立即失败且不自动重试。未知方法/状态停止，不静默忽略。具体事件样本由既有ServerNotification.schema核对，不能用被测解码器生成预期。

app-server wire MCP项使用`type:mcpToolCall`、`status:inProgress/completed/failed`、`result.structuredContent`。保留原始v2校验，不能通过递归改键把它伪装成exec。工具正文在受限内存解码后，只生成既有结果normalizer需要的白名单证明/哈希；共享正文normalizer可做小范围提取，但两种wire decoder不混用。

复用NativeRunState的固定五步唯一调用/结果判断与业务审计validator；不假定canonical item.id必等于MCP metadata callId。若源码和后续证据能建立直接关系，可额外验证，但不能删掉现有独立ID/参数哈希检查。传输审计与业务审计各5条，不能合并成10次调用。缺证据unknown，明确矛盾failed，缺失不掩盖其他独立矛盾。

新增独立`AppServerSessionEvidence`用于policy/reviewer/root/model/turn/关闭证明，不给现有exec的`validate_evidence`传一个虚构`process_exit=0`。业务证明与宿主会话完成是两层：

- 业务层：身份、5步、真实DTO、双审计、哈希和映射；可从现有validator提取共享函数，不改原exec的进程退出条件。
- 会话层：真实线程配置、一次Started turn、turn.status=completed且无error、没有审批/设置漂移、正常EOF/自然退出0、Job空与I/O完成。两层都通过才可将该**候选场景**标为verified。

`execution_origin`继续区分offline_simulation/native_codex，并新增固定`transport_kind=app_server_stdio_0.163.0`；两者只能由驱动提供，不从模型或清单任意覆盖。

## 9. 关闭语义

成功路径：收到合法turn/completed并完成5步证据 → 关闭stdin发送EOF → 在剩余预算内观察自然进程exit0及stdout/stderr EOF → stop确认Job无后代、全部I/O完成 → 最终核验与记账。

异常路径：必要时发送一次已审阅turn/interrupt → 关闭stdin → 等待剩余清理预算 → TerminateJobObject兜底。因监督器杀进程得到的退出码不是自然成功；业务完成但无法正常关闭也不能verified。现有exec的exit0条件保留，不把turn completed改名成process exit。

app-server是否在该实际版本/环境下按EOF正常退出仍需原生验收；离线假进程通过不证明真实宿主关闭行为。若实际宿主依赖未实现交互或不能确认退出，维持unknown/unsupported，不造0或延长时限。

## 10. 文件布局与模式隔离

仅增加C1专用模块，不建设通用宿主总线：

- `scripts/codex_c1_process.py`：小范围新增可选双向stdin，所有Job/限量/清理复用；默认不变。
- `scripts/codex_c1_appserver_rpc.py`：双向消息分类/ID/白名单/有界收发，独立于业务。
- `scripts/codex_c1_appserver_session.py`：线程/turn状态机、策略核对、取消与关闭。
- `scripts/codex_c1_appserver_events.py`：v2 wire解码、会话/业务证明适配。
- `scripts/codex_c1_appserver_candidate.py`：组合上述模块、既有名额和最终锁内授权核验；真实factory默认拒绝，阶段仅程序化离线模拟。
- `scripts/codex_c1_appserver_runtime.py`：Task5经协调器批准的候选局部辅助；一帧实际管道lookahead、同截止内预检watchdog、既有授权/审计复用及exactly-once最终化，不修改已批准RPC/Session/Event/Process producer或暴露通用RPC入口。
- `scripts/codex_c1_lock.py`：Task5修复轮经协调器批准的C1专用绝对截止锁；复用既有`.coordination`目录、SessionPointerStore固定键编码和同一OS字节锁，不修改受保护src或建立第二锁域。小范围重复固定键/原语带来互操作维护成本，必须保留真实旧后端双向互斥测试。
- `scripts/prepare_codex_c1_appserver_review.py`：只输出新被动bundle和通道合同sidecar，调用现有prepare生成schema2基础材料，不改旧schema2含义。
- tests对应上述协议/进程/SDK集成与假app-server fixture。

新sidecar为`appserver-contract.preview.json`，固定schema_version=1、transport_kind、target_version、base_manifest_sha256、请求/响应/取消白名单、policy/reviewer/sandbox、时限/字节/名额、source_sha256和native_entry_enabled=false。所有源码摘要由当前生成器实际计算；不将模型内容复制为合同。基础bundle和sidecar都要求新目录，不能覆写旧材料。

Task5落地schema1的精确顶层字段为：`schema_version`、`transport_kind`、`target_version`、`base_manifest_sha256`、`client_requests`、`client_notifications`、`server_notifications`、`server_cancel_responses`、`policy`、`limits`、`source_sha256`、`native_entry_enabled`、`native_entry_status`。取消映射只有command/file的decision=cancel和permissions空权限/scope=turn；profile不含任意命令/导入路径或真正原生授权。source_sha256保持既有基础生成器的采集集合并包含新候选/辅助/导出器/测试文件，不改变schema2 executor字段含义。sidecar以排序键/紧凑分隔/有限JSON/UTF-8规范字节独占写入；核验先保留原review-only检查，再进行有界严格JSON解析和完整profile/规范字节比较，拒绝重复键、reparse、已有/超限/变化材料。基础摘要取实际清单字节，sidecar摘要也取同一次有界校验读取，不再无界重读。

Task5落地时经协调器批准，既有 `reserve_attempt` / `finish_attempt` 增加可选 `deadline=None`：新候选始终传原始绝对截止，真实锁等待最多min(既有10秒,剩余预算)，过期在等待/阶段写入前静态拒绝；旧省略调用和锁内expected_approval语义保持。修复轮不再把预先计算的剩余时长交给设置后才启动相对计时的旧锁API：显式deadline使用同一文件/固定键/OS锁的专用绝对循环，在open/fdopen/初始字节/flush等全部设置完成后、每次非阻塞尝试/重试/sleep前核对同一个原始截止，sleep取剩余预算上限，延迟设置返回后不得再得到一段新相对等待。实际reservation已取得/安全恢复时即使过期也只调用一次finish并明确未确认，不重试或退款；不能安全读取的未知reservation不猜DTO，保留null/unknown与原字节。操作系统文件写入/fsync不可安全抢占，不宣称对任意文件系统停顿的通用硬墙钟保证。

本阶段不扩展原生授权scope/actions、不增加真实run CLI开关。已有exec `NATIVE_DISPATCH_SUPPORTED=False`和原因不改；app-server候选在公开入口仍为`C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING`。未来诊断启动器必须单独审阅合同sidecar/源码/人类授权并仍先reserve后thread/start，不是将这个字段改true即可通过。

## 11. 测试与审阅门槛

1. 双向进程：实际Python父子进程、恢复前Job成员、往返、stdout/stderr上限、阻塞stdin写、EOF自然退出、超时、关闭中途断管、根先退但后代未退、I/O未完成。
2. RPC：两个方向数值相同ID不混淆；未知/重复/错ID；响应/通知交错；重复键/超大/畸形/超时；取消只用精确schema；未知attestation/elicitation不回复接受。
3. 生命周期：名额文件先于thread/start发送；policy/reviewer/sandbox/root/model返回不符不发turn/start；单turn；额外thread/turn、steer、settings漂移、取消后继续通知均不能成功。
4. v2证明：真实SDK bounded fixture的5步、预期失败状态、camelCase wire字段、结果一致性、item/callId独立、两类审计、false/unknown组合、重放、无正文日志。
5. 集成：真实本地假app-server+真实MCP SDK，完整线程响应/交错事件/EOF与Job回收；控制器后续完整回归。假宿主不得绕过准入或给validator塞一套手工成功audit。
6. 逐任务独立审阅+最后整轮审阅。所有日志/样本标synthetic/offline，不能叫原生passed；skip仍未验证。

最近C1-E完整回归277项、270通过、7跳过是此前2026-10-19交付，本轮设计未重跑。实施时重新发现数量，不写死未来总数。

## 12. 原生验收仍独立

C1-T离线交付不解除exec阻塞，不启用生产，不证明真实app-server保留策略、EOF关闭、原生callId、账户或模型行为。后续先审阅明确范围的原生生命周期/诊断入口，再分别授权线程创建和最多A/B各一次的模型任务；thread/start可能预热网络，不能包装成无副作用预检。原生记录必须使用实际策略快照和结果，不复用旧3次尝试授权或模拟证明。

## 13. 依据

- [thread/start配置和快照](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/request_processors/thread_processor.rs#L1630-L1711)
- [thread/start可能预热](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/request_processors/thread_processor.rs#L1500-L1529)
- [turn/start提交](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/request_processors/turn_processor.rs#L640-L676)
- [审批响应处理](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/bespoke_event_handling.rs#L1973-L2052)
- [canonical v2通知](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/bespoke_event_handling.rs#L1077-L1137)

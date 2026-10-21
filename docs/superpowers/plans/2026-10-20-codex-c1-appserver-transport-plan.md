# C1-T app-server 受控通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans after implementation approval. Steps use checkbox (`- [ ]`). 沿用用户明确选择的执行方式；不自动提交、推送、创建worktree或扩展原生授权。

**Goal:** 交付可完全离线验证的app-server双向stdio、线程策略门槛、v2结果适配与C1五步候选编排，同时保留原生入口关闭。

**Architecture:** 对既有OwnedProcess增加默认不变的可选stdin管道，共用Windows Job/限量/截止/清理；新建C1专用RPC、生命周期、v2证据及候选编排模块。复用既有真实SDK夹具、名额/授权/最终锁内记账及业务证明，不用直接工具调用或伪造进程退出证明填补原生证据。

**Tech Stack:** Windows、stdlib/unittest、mcp==2.3.0；Python声明>=3.10，当前实际运行基线3.13.1；Codex合同目标仅rust-v0.163.0。本计划不调用真实Codex。

**Spec:** [C1-T确认范围的具体设计](../specs/2026-10-20-codex-c1-appserver-transport-design.md)。2026-10-20用户在审阅两份文件后确认实施，现执行本计划离线编码/测试/审阅；不授权任何真实宿主/模型操作或原生入口开放。

## 执行进度

- [x] Task 1：Job拥有进程双向stdin。独立审阅两项重要发现（超限输入先复制/读取deadline非sticky）修复并复审通过；最终进程25项+既有执行器30项通过。完整298项/291通过/7跳过为修复前证据，后续重新回归。
- [x] Task 2：RPC方向/ID/取消。错误通知锁存、请求完成关联、可选/嵌套审批schema三项重要发现修复并复审通过；最终针对性15项通过。此前完整313项/306通过/7跳过为修复前证据。
- [x] Task 3：线程策略/单turn状态。4项重要发现和未知来源掩盖线程冲突的修复回归经2轮修复复审批准；最终状态机51项通过。共享审批predicate及严格沙箱边界已明确，完整353项结果为修复前证据。
- [x] Task 4：v2业务/会话证据。完成快照校验、未知后重放检查及可选schema经3轮修复复审全部批准；最终事件74项、旧结果46项通过。完整418项结果为修复前历史；原exec进程门槛保留。
- [x] Task 5：离线候选/真实假宿主SDK/被动材料及绝对截止锁修复复审完成。整轮最后两项响应schema/线程重复幂等问题统一修复后Spec/Quality均Approved，无剩余发现。控制器最终完整回归520项、513通过、7项未验证跳过，1088.950秒，退出0；123项代码快照一致，39项保护文件未变。最新被动材料未安装、零原生授权，两个原生门槛保持。详见 [最终交付验证及18项裁决](../../verification/codex-c1-appserver-transport.md)。

具体审阅/测试/裁决以本计划ignored SDD ledger记录，不覆写C1-E证据。

## Global Constraints

- Python声明下限>=3.10；运行证据仅报告实际解释器，当前既有证据为Windows/Python3.13.1；MCP固定mcp==2.3.0，不新增依赖或修改全局Python。
- 不修改src/、hooks/、生产身份入口、旧插件、正常项目配置或宿主信任；不扩大既有可信本机宿主及获授权客户端边界。
- 不调用真实Codex（包括--version、schema生成、只读RPC）、Mem0、账户/额度或信任接口；仅测试临时新合成根与本地Python假进程。
- 只读获取固定rust-v0.163.0官方源码可用于合同核对；隔离存放，绝不导入、构建或执行下载代码。
- 原生审批语义固定为approvalPolicy=on-request、approvalsReviewer=user；沙箱为read-only，networkAccess=false；不得采用AutoReview、never、会话永久批准、规则修改或权限绕过。
- 原生候选预算保持A/B各1个名额、总计2个；每场景恰好5个业务准入调用；总时限180秒，包括RPC写入/等待/SDK工作和清理预留；无自动重试、重新连接、resume、自动B或模型/账号回退。
- 非核心正文/摘要/集合不进入本地缓存、日志或报告；仅白名单标识/状态/指纹/锁。模型文本、完整RPC流、任意配置和异常原文不落盘。
- 现有exec原生门槛和schema2 bundle语义不放宽；普通夹具、既有审计合同、失败优先级、保守名额和精确回退保持。
- 不提交、推送、合并、创建远程或自动创建工作树；已有证据不覆盖、不清理。
- 本阶段不交付开放的原生run CLI；公开app-server候选状态固定C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING。仅显式程序化本地测试factory可运行offline_simulation；不能将origin/factory暴露给CLI或bundle任意导入。

---

## 0. 输入、文件与跨任务合同

### 实施前必读的现有接口

- `scripts/codex_c1_process.py:87-145,192-316`：stdin当前DEVNULL；Job恢复前所有权、events、stop、自然exit_code、stream_summary。
- `scripts/codex_c1_bundle.py`：ReviewSnapshot、verify_snapshot、reserve_attempt、finish_attempt的expected_approval关键字；批准动作inspect/run_project；不得额外放宽scope或账本。
- `scripts/codex_c1_results.py`：NativeEvent、NativeRunState、现有私有DTO规范化/业务/映射函数、validate_evidence；exec仍必须自然exit0。
- `scripts/codex_c1_host.py`：纯validate_preflight和已有schema/特征检查；新RPC可复用纯验证，不启动ReadOnlyProbe真实进程。
- `tests/fixtures/codex_c1_server.py`、`codex_c1_policy.py`：SDK前准入、严格metadata.callId、5步、两类审计及非阻塞重叠冻结。
- `tests/unit/test_codex_c1_review.py`：以磁盘当前内容为准。此前有外部变更通知；不得按旧记忆覆盖或回滚。

最近交付277项、270通过、7未验证skip是2026-10-19历史，不当作本次测试结果。实施前另建本计划快照/账本，既有C1-E账本和任何旧bundle只读。

### 文件拆分

| 文件 | 操作/职责 |
| --- | --- |
| `scripts/codex_c1_process.py` | 增量：可选pipe、限量写入、逐条读取、EOF及I/O清理；默认devnull不变 |
| `scripts/codex_c1_appserver_rpc.py` | 新建：JSON-RPC分类、双方向ID、固定方法和消息字节预算 |
| `scripts/codex_c1_appserver_session.py` | 新建：thread/turn状态、返回策略、取消请求、一次性生命周期 |
| `scripts/codex_c1_appserver_events.py` | 新建：v2 wire、规范化结果、独立会话证明和业务证据组合 |
| `scripts/codex_c1_appserver_candidate.py` | 新建：只允许程序化offline模拟的编排/名额/最终记账 |
| `scripts/codex_c1_appserver_runtime.py` | 经协调器批准的局部辅助：真实一帧lookahead、预检watchdog、授权/审计复用与一次最终化；不改RPC/Session/Event/Process producer |
| `scripts/codex_c1_bundle.py` | Task5协调器批准窄扩展：reserve/finish/锁的可选deadline=None，原始绝对预算约束锁等待；旧省略调用/授权/记账语义不变 |
| `scripts/prepare_codex_c1_appserver_review.py` | 新建：新schema2基础bundle+固定sidecar被动导出/核验，不安装/运行 |
| `scripts/codex_c1_results.py` | 小范围提取共享body normalizer/业务验证；exec wire和exit条件保持 |
| `tests/unit/test_codex_c1_appserver_rpc.py` | 新建：帧/ID/取消/RPC边界 |
| `tests/unit/test_codex_c1_appserver_session.py` | 新建：返回策略与生命周期状态机 |
| `tests/unit/test_codex_c1_appserver_events.py` | 新建：v2证据/旧exec对照 |
| `tests/unit/test_codex_c1_appserver_review.py` | 新建：sidecar来源/非覆盖/变化拒绝 |
| `tests/contracts/test_codex_c1_rpc_process.py` | 新建：真实Python双向进程/阻塞/EOF/Job |
| `tests/contracts/test_codex_c1_appserver.py` | 新建：真实假app-server+真实SDK/失败预算/回退 |
| `tests/fixtures/codex_c1_appserver_fake.py` | 新建：本地双向假宿主，实际SDK调用bounded fixture |
| `tests/fixtures/codex_c1_appserver_samples.json` | 新建：独立字面固定版本样本与源码出处，明确synthetic |
| 既有执行器合同/结果单元测试 | 仅新增防回归，不能删掉既有门禁/失败用例凑通过 |
| 当前设计/计划导航、兼容/操作/证据文档 | 只写实际结果/限制；C1-E历史数和原生unsupported保留 |

不增加通用注册器、远程控制服务、自动审批UI、模型任务队列或任意命令/RPC执行入口。

### 固定协议和数据

客户端请求白名单：initialize、config/read、hooks/list、account/rateLimits/read、thread/start、turn/start、turn/interrupt；唯一客户端通知initialized。生命周期操作只能由状态机发出，不提供CLI任意method参数。RPC每方法最多一次；turn/interrupt仅异常路径一次，不重试超时请求。

`thread/start`固定输入：cwd为本轮canonical合成根、approvalPolicy="on-request"、approvalsReviewer="user"、sandbox="read-only"、ephemeral=true、allowProviderModelFallback=false、experimentalRawEvents=false。不发送historyMode/dynamicTools/permissions等替代权限入口。

入站通知白名单固定为thread/started、turn/started、item/started、item/completed、turn/completed、thread/settings/updated、thread/status/changed、thread/tokenUsage/updated、item/agentMessage/delta、item/reasoning/summaryTextDelta、item/reasoning/summaryPartAdded、item/reasoning/textDelta、serverRequest/resolved、error。逐一按对应0.163.0 schema核对真实字段，不要求原schema没有的身份字段。文本/推理delta受限内存丢弃；usage/status不当成功证明；resolved只对应已见server request；error失败。其他通知/不认识状态停止，不能扩成任意method透传。

`turn/start`固定输入：threadId为已验证返回值、input=[{"type":"text","text":固定五步prompt}]、approvalPolicy="on-request"、approvalsReviewer="user"、sandboxPolicy={"type":"readOnly","networkAccess":false}。不传_client_metadata、附加工具结果、arbitrary config；不允许steer/resume/第二个turn。

所有通道共享同一个绝对deadline（最多180秒，含清理预留），收/发单行262144字节（含LF），stdout+stderr累计2097152字节、发送累计2097152字节，队列16条。readOnly缺省networkAccess按固定schema默认false解释，显式true或非bool拒绝，报告标明来自显式值还是schema默认。

## Task 1: 为Job拥有进程增加受限双向stdin

**Files:** Modify `scripts/codex_c1_process.py`；Create `tests/contracts/test_codex_c1_rpc_process.py`；运行既有`tests/contracts/test_codex_c1_executor.py`作回归。

**Consumes:** 当前OwnedProcess(argv,cwd,env,deadline)、stop()/exit_code/stream_summary。

**Produces:**

```text
OwnedProcess(argv, *, cwd, env, deadline, stdin_mode="devnull")
OwnedProcess.send_line(raw: bytes) -> None
OwnedProcess.read_line() -> bytes | None
OwnedProcess.close_input() -> None
OwnedProcess.wait_for_exit() -> int | None
```

wait_for_exit仅在剩余operation_deadline内等待自然退出，未观察到则None，不杀进程、不制造0。

send_line只接受单条LF结尾消息，无内部LF；成功返回必须已实际写完并flush，不是仅入队。未知部分写入抛静态OwnedProcessError，不重发。read_line返回完整stdout行，EOF为None；events()继续用同一底层读取，不能双消费者。devnull模式send/close明确拒绝，不改变旧进程语义。stream_summary增加单独stdin计数/哈希，旧stdout/stderr键不改。

- [ ] **Step 1 — 写真实进程RED。** 用`sys.executable -I -c`假子进程逐行读取stdin并只输出合成JSON。实际检查Job成员、2条往返、EOF自然exit0、后代清理；单独验证默认devnull不接受写入、stdout读取双消费者拒绝。

```python
process = OwnedProcess([sys.executable, "-I", "-c", echo_program],
                       cwd=root, env=dict(os.environ), deadline=time.monotonic()+5,
                       stdin_mode="pipe")
try:
    process.send_line(b'{"id":1,"method":"initialize"}\n')
    self.assertEqual(json.loads(process.read_line()), {"id": 1, "result": {"ok": True}})
    process.close_input()
    self.assertIsNone(process.read_line())
    self.assertEqual(process.wait_for_exit(), 0)
finally:
    cleanup = process.stop()
self.assertTrue(cleanup["root_exited"])
self.assertTrue(cleanup["descendants_exited"])
```

echo_program在该测试中为固定字面Python：读取每行，json.loads，仅返回id及上述ok；不是业务结果替身。禁止测试在异常后漏掉finally。

- [ ] **Step 2 — 运行RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_rpc_process.py
```

预期缺少stdin_mode/send_line/read_line接口，不接受凭空mock通过。

- [ ] **Step 3 — 最小实现。** 默认分支仍DEVNULL；pipe分支Popen stdin=PIPE，所有权建立与恢复流程不改。单个写线程和16条队列，消息携带已写完/失败通知；调用者等待实际通知或operation_deadline，停机关闭/唤醒队列并确认写线程退出。read_line复用当前有界读队列；events()通过同一消费者所有权逻辑循环，不复制读线程。stderr仍只摘要。

```python
if stdin_mode not in ("devnull", "pipe"):
    raise ValueError("C1_STDIN_MODE_INVALID")
stdin_target = subprocess.PIPE if stdin_mode == "pipe" else subprocess.DEVNULL
```

- [ ] **Step 4 — GREEN/边界。** 写入前限制行/累计字节；真实不读stdin的子进程制造阻塞，send_line按deadline退出且Job/所有I/O线程确认停止。close_input幂等但不能假装自然exit；终止代码不记自然0。确认assignment/resume故障路径仍不执行子程序。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_rpc_process.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_executor.py
```

- [ ] **Step 5 — 审阅。** 关注锁跨写入/await、读写队列耗尽、关闭中途写、句柄泄漏和旧模式兼容。记录真实RED/GREEN/耗时及代码摘要，不提交。

## Task 2: 固定双向RPC分类、ID与取消合同

**Files:** Create `scripts/codex_c1_appserver_rpc.py`、`tests/unit/test_codex_c1_appserver_rpc.py`、`tests/fixtures/codex_c1_appserver_samples.json`。

**Consumes:** Task1实际有界send_line/read_line/close_input；已保存0.163schema/source只读数据。

**Produces:**

```text
RpcMessage(kind: str, method: str | None, rpc_id: int | str | None, payload: dict)
  frozen dataclass；kind=response/error/notification/server_request。
classify_message(raw: bytes) -> RpcMessage
AppServerPeer(process: OwnedProcess)
AppServerPeer.send_request(method: str, params: dict) -> int
AppServerPeer.send_initialized() -> None
AppServerPeer.next_message() -> RpcMessage
AppServerPeer.reply_cancel(message: RpcMessage) -> None
AppServerPeer.pending_client_id -> int | None
```

payload只在内存，报告只存白名单摘要。client ID递增整数、严格排除bool、最多一个未完成；server ID独立set，可为非空字符串/整数，不能与客户端ID共享回调表。server reply只能由固定取消方法生成，没有任意result/accept API。

- [ ] **Step 1 — 写RED。** 独立字面样本：客户端待id=1时server approval也id=1，必须先识别server_request，取消不会清除客户端待响应；稍后response id1完成客户端请求。再测未知/已应答/重复/冲突ID、response同时method/result、不匹配响应、重复键、NaN、超行和不支持method。

```python
request = classify_message(b'{"id":1,"method":"item/commandExecution/requestApproval","params":{"threadId":"t","turnId":"u","itemId":"i"}}\n')
response = classify_message(b'{"id":1,"result":{}}\n')
self.assertEqual(request.kind, "server_request")
self.assertEqual(response.kind, "response")
self.assertEqual(request.rpc_id, response.rpc_id)  # 同值不同方向，不能合并
```

真实审批样本须补齐固定schema必需字段并由独立字面写入samples；此例只演示分类，不代替完整审批校验。

- [ ] **Step 2 — RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_rpc.py
```

- [ ] **Step 3 — 实现互斥消息分类和有界发送。** JSON-RPC字段可省略；存在时只能2.0。method+id是server request，method无id是notification，无method且result/error恰一且匹配pending是response/error；其他拒绝。原始消息不能直接repr/log。

固定取消payload：命令/改文件为`{"decision":"cancel"}`；权限为`{"permissions":{},"scope":"turn"}`，不写strictAutoReview。两者都不是批准；permissions空响应后仍须中断/停止。未知请求（含attestation/generate、mcpServer/elicitation/request、item/tool/call、登录）无猜测响应路径。

- [ ] **Step 4 — GREEN/真实管道。** 通过Task1本地双向假子进程测试交错server request/notification/response，记录临时收到的method/response类别；断言从未发送accept、acceptForSession、session scope或网络/规则变更。写失败后pending仍标未知，不清空后重新发送。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_rpc.py
```

- [ ] **Step 5 — 审阅。** 精确schema方法/字段、双方向同值ID、无任意RPC入口、未知默认停止、脱敏和预算。samples引用官方标签/文件位置，标synthetic，不宣称原生捕获。

## Task 3: 线程策略、一次turn与异常冻结状态机

**Files:** Create `scripts/codex_c1_appserver_session.py`、`tests/unit/test_codex_c1_appserver_session.py`；Extend独立samples，不重写旧exec/RPC样本。

**Consumes:** ReviewSnapshot、纯validate_preflight、AppServerPeer、固定thread/turn schema。

**Produces:**

```text
AppServerSession(snapshot: ReviewSnapshot, project: str, *, model: str, provider: str)
AppServerSession.bind_reservation(reservation: dict) -> None
AppServerSession.thread_start_params() -> dict
AppServerSession.accept_thread_response(result: dict) -> None
AppServerSession.turn_start_params() -> dict
AppServerSession.observe(message: RpcMessage) -> None
AppServerSession.on_server_request(message: RpcMessage) -> str
AppServerSession.interrupt_params() -> dict | None
AppServerSession.summary() -> dict
```

状态与设计§6一致；params方法只能调用一次，检查顺序并标记已发送意图，未知发送不返还。bind_reservation验证本轮nonce/manifest/project/attempt/state，不根据文件存在猜有效性；协调器仍须实际reserve落盘。

- [ ] **Step 1 — RED矩阵。** 每次独立Session，改变响应中的policy=never、reviewer=auto_review、外来cwd、sandbox写/联网、非bool网络、model/provider漂移或缺字段；必须拒绝，随后turn_start_params仍不能产生。thread_start_params无reservation时拒绝。相同thread/started重复幂等，冲突冻结；额外turn、Steered/active turn、settings漂移及审批后续成功消息不能解除冻结。

```python
session.bind_reservation(reservation)
params = session.thread_start_params()
self.assertEqual(params["approvalPolicy"], "on-request")
self.assertEqual(params["approvalsReviewer"], "user")
wrong = dict(valid_thread_response, approvalPolicy="never")
with self.assertRaises(ValueError):
    session.accept_thread_response(wrong)
with self.assertRaises(ValueError):
    session.turn_start_params()
self.assertEqual(session.summary()["state"], "failed")
```

valid_thread_response在setUp从独立完整synthetic样本绑定临时根，不由被测函数生成期望；必须含thread.status.type=idle。TurnStartResponse只有turn对象，没有started/steered标志；通过新idle线程、无既有活动turn、唯一turn/started和响应turn ID匹配核验，不能造字段来分辨内部enum。相同通知的精确重复幂等，额外不同turn拒绝；通知/响应交错只做有界待核验，不先宣布成功。

- [ ] **Step 2 — RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_session.py
```

- [ ] **Step 3 — 实现固定策略。** 参数字面按本计划§0；返回值按schema和设计核验，身份/策略由响应得出。允许schema定义缺省false但记录来源，不把null/0当false；不允许请求data补齐缺失返回字段。thread/settings/updated来自绑定thread才可接受，缺少/不认识权限结构不猜合并。

```python
if result["approvalPolicy"] != "on-request" or result["approvalsReviewer"] != "user":
    self.freeze("C1_APPSERVER_POLICY_MISMATCH")
    raise ValueError("C1_APPSERVER_POLICY_MISMATCH")
```

freeze为本任务私有方法，只能失败/unknown锁存，后续消息不能重开。`on_server_request`先验证schema/方向ID/归属，再冻结并返回`cancel`（命令/文件）、`deny_permissions`（空权限）或`stop`（其他交互）；valid已知审批不在返回动作前抛异常，避免客户端来不及发送准确取消。畸形/外来身份直接抛静态错误并停止。peer只有前两种动作可调用reply_cancel按Task2固定payload回应；之后中断/关闭，没有继续正常收集的路径。interrupt只在thread+turn均已核验时生成一次。

- [ ] **Step 4 — GREEN。** 加入thread通知先于RPC response、启动期间未知attestation、完整响应后同值重复、已知字段矛盾+另一个字段缺失等回归。纯缺失unknown不掩盖独立失败；不开始真实线程。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_session.py
```

- [ ] **Step 5 — 审阅。** 对照source实际返回/转换、默认网络语义、单turn且非steer、不可逆冻结、预热前reserve和通知/响应交错。ThreadSettingsUpdateResponse是空对象，不能拿它冒充有效线程配置证明。

## Task 4: v2工具事件与独立会话完成证明

**Files:** Create `scripts/codex_c1_appserver_events.py`、`tests/unit/test_codex_c1_appserver_events.py`；Narrow Modify `scripts/codex_c1_results.py`提取共享业务函数，补既有`tests/unit/test_codex_c1_results.py`对照；Extend独立samples。

**Consumes:** 既有NativeEvent/NativeRunState与真实业务normalizer、双审计/哈希合同；Task3 session.summary。

**Produces:**

```text
AppServerEvent(thread_id: str, turn_id: str | None, kind: str, item: NativeEvent | None)
  frozen dataclass；kind由已审阅通知转换，不能来自模型文字。
decode_appserver_notification(message: RpcMessage) -> AppServerEvent
validate_business_evidence(snapshot, project, reservation, native, transport_rows, outcome_rows) -> dict
validate_appserver_evidence(snapshot, project, reservation, business: dict, session: dict,
                           shutdown: dict, *, execution_origin: str) -> dict
```

validate_business_evidence从既有validator提取业务/范围/调用数/审计映射，不要求process_exit；旧validate_evidence继续组合原生exec退出条件+同一业务函数，不能删进程退出检查。共享正文normalizer命名`normalize_c1_body(tool: str, body: dict) -> dict`，只返回现有白名单证明/哈希，不返回原始正文/句柄。

shutdown由拥有进程驱动填，精确字段：input_eof_sent(bool)、stdout_eof(bool)、stderr_eof(bool)、natural_exit_code(int|None)、forced_termination(bool)、cleanup{root_exited,descendants_exited,reason}。不能用stop返回值制造自然exit0。

- [ ] **Step 1 — 写RED。** v2字面`method:item/completed`、params{threadId,turnId,item{type:mcpToolCall,status:failed,result{content:[textJSON]}}}承载真实SESSION_INVALID；缺字段unknown，错误线程/非预期失败failed。structuredContent与text不一致拒绝；snake_case exec字段不能补v2缺失。thread/turn/item均独立核对。

```python
proof = validate_appserver_evidence(snapshot, "a", reservation, valid_business,
                                   valid_session, dict(valid_shutdown,
                                   natural_exit_code=None, forced_termination=True),
                                   execution_origin="offline_simulation")
self.assertEqual(proof["status"], "unknown")
self.assertFalse(proof["native_evidence_claimed"])
```

valid_business必须由真实SDK fixture的DTO/audit或独立完整合成样本产生；至少一个整合用例使用真实fixture，不手工塞成功audits。另测turncompleted但exit不明、自然exit0但turn失败、Job非空、worker未退出、审批已触发、policy漂移、nonce/hash/重放、completedcounts与缺证据组合。

- [ ] **Step 2 — RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_events.py
```

- [ ] **Step 3 — 单独wire解码，复用业务。** app-serverresult读取structuredContent、status映射inProgress/completed/failed；不通过JSON整体改键伪装exec。只将已核对的MCP项构成NativeEvent送原有reducer；thread/turn会话由Task3管理。业务error只有步骤3/4的SESSION_INVALID符合预期；过程中的审批/attestation不是工具成功。

```python
verified = (business["status"] == "verified"
            and session["turn_status"] == "completed"
            and session["policy_verified"] is True
            and session["approval_seen"] is False
            and shutdown["natural_exit_code"] == 0
            and type(shutdown["natural_exit_code"]) is int
            and shutdown["forced_termination"] is False)
```

完整实现还需检查所有§shutdown字段和thread/turn/root/approval/source关系；这是可读的核心条件，不取代缺失/矛盾分类。所有明确contradiction优先failed；仅关闭证据缺失/强制清理不能verified，正常业务完成但forced termination为unknown。不要给原validate_evidence塞process_exit0来复用。

- [ ] **Step 4 — GREEN/旧路径回归。** 确认exec字段/自然退出和全部现有失败优先级测试保持，v2有独立解码限制。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_events.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_results.py
```

- [ ] **Step 5 — 审阅。** 检查跨协议键混用、假process0、两种ID空间、双审计计数、缺失掩盖矛盾和原始数据泄露。没有实跑app-server，最多源码/模拟合同通过。

## Task 5: 组合离线候选、真实假宿主与被动sidecar

**Files:** Create `scripts/codex_c1_appserver_candidate.py`、`scripts/prepare_codex_c1_appserver_review.py`、`tests/contracts/test_codex_c1_appserver.py`、`tests/unit/test_codex_c1_appserver_review.py`、`tests/fixtures/codex_c1_appserver_fake.py`；Modify设计/计划导航/兼容/操作文档；小范围补现有producer调用点测试。

**Consumes:** Tasks1-4全部接口，既有snapshot/approval/install/reserve/finish(expected_approval)/rollback，SDK bounded fixture。

**Produces:**

```text
run_appserver_simulation(snapshot, project, approval, receipt, contract: dict,
                         *, process_factory, version_proof: str,
                         execution_origin="offline_simulation") -> dict
transport_contract(snapshot: ReviewSnapshot) -> dict
prepare_appserver_review(output: Path, run_nonce: str) -> dict
verify_appserver_review(output: Path) -> dict
```

version_proof只用于本阶段本地假进程，不能标为真实版本证据。process_factory是必传可信程序化测试缝；execution_origin必须offline_simulation、approval必须offline-simulation，其他组合拒绝；无default真实factory、无新native CLI开关。固定返回`transport_kind=app_server_stdio_0.163.0`、native_evidence_claimed=false。

- [ ] **Step 1 — 先写完整RED。** 假app-server为真实本地Python脚本，双向JSON-RPC；启动时只读本轮安装/临时模拟记录。thread/start发送前检查实际reservation文件已存在；创建线程响应后才允许一次turn/start。turn内用真实MCP SDK调用实际bounded fixture、以构造meta生成5步，投影v2事件，正文只在内存。关闭stdin后自然退出0；另有sleepchild和阻塞模式。

```python
report = run_appserver_simulation(snapshot, "a", approval, receipt, contract,
             process_factory=local_fake_factory, version_proof="codex-cli 0.163.0")
self.assertEqual(report["status"], "verified")
self.assertEqual(report["execution_origin"], "offline_simulation")
self.assertEqual(report["transport_kind"], "app_server_stdio_0.163.0")
self.assertFalse(report["native_evidence_claimed"])
self.assertEqual(report["transport_count"], 5)
self.assertEqual(report["business_count"], 5)
self.assertFalse((snapshot.bundle / "attempts/b-reservation.json").exists())
```

还要实际测试：线程policy不符不发turn；thread失败仍消费reservation；未授权不创建进程；未知attestation不答accept；审批cancel后不继续；天然EOF不退出/后台子进程挂起unknown；晚授权撤销；写入/证据/最终化冲突仍exactly once finish；A失败阻止B；A/B回退保留字节；旧exec默认nativegate保持。

- [ ] **Step 2 — RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_review.py
```

- [ ] **Step 3 — 编排顺序。** verify installed +冻结approval +sidecar源摘要 → 创建本地owned pipe进程/initialize → 同连接读取config/hooks/usage并调用现有纯validate_preflight（假宿主提供synthetic版本） → 再核对字节/approval → reserve_attempt落盘 → thread/start → response策略核对 → 一次turn/start → v2工具/结果 → EOF自然关闭与Job确认 → 独立业务/会话证据 → 最终锁内authority和exactly once finish。

所有向thread/start的发送集中在单一受守卫分支：

```python
reservation = reserve_attempt(snapshot, project, approval)
session.bind_reservation(reservation)
peer.send_request("thread/start", session.thread_start_params())
```

该顺序不能因thread创建不消耗模型token就推迟reserve。进程启动前/预检不符且未reserve不消耗模型名额，但不假装无进程副作用；报告分别记录process_started/attempt_reserved/thread_start_sent/turn_start_sent，未知发送不默认false。保留既有最后锁内expected_approval检查，失败路径不能为了写report重试finish。

- [ ] **Step 4 — 被动导出器与GREEN。** 调用既有prepare创建全新schema2基础bundle，不改其executor字段含义；在同一新目录独占写`appserver-contract.preview.json`。sidecar精确字段见设计§10；base_manifest_sha256使用实际字节、source_sha256与基础bundle源码映射一致，native_entry_enabled=false。verify先执行原review-only校验，再按固定profile重新生成sidecar期望并比较；大小/重复键/reparse/已有文件拒绝。源代码稳定后导出新目录，旧材料不覆盖。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver*.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1*.py
```

- [ ] **Step 5 — 完整验证、审阅、交付。**

```bash
.venv/Scripts/python.exe scripts/test.py
```

记录实际total/pass/skip/exit/耗时及过程失败，不能把历史277或预想新总数当结果。每任务做规格/质量审阅，最后做整轮交叉审阅；修复后重新运行覆盖测试与控制器完整回归。验证源码摘要、旧execgate、生产src/hooks与正常配置未变；文档区分源码/SDK模拟/原生四层证据。保留账本/源快照/失败与被动预览，不提交Git。

## 6. 自查与交接

| 设计要求 | 任务 |
| --- | --- |
| Job复用、真实双向I/O、阻塞写/EOF/线程清理 | 1 |
| 双方向ID/固定RPC/取消/未知交互 | 2 |
| 实际线程policy/reviewer/root、单turn、预热前reserve | 3 + 5 |
| v2wire独立、真实DTO/双审计、无假exit0 | 4 |
| 真实本地假宿主+实际SDK、保守最终账本/回退 | 5 |
| sidecar、旧schema2/exec语义不改、无native公开入口 | 5 |
| 无原生/真实API、正文不落盘、无Git提交 | 每任务+最终审阅 |

实施审批后，推荐沿用此前逐任务子Agent实现、独立规格/质量审阅、整轮审阅与控制器最终回归；也可在用户明确选择时改为单会话逐任务执行。此时无需重新讨论已确认的安全边界，但不得沿用旧原生尝试授权。

完成本计划仅交付离线C1-T候选，不声称本机app-server已通过：实际thread/start预热、策略快照、审批取消、EOF退出、callId/原生结果与模型行为仍需后续独立原生验收计划。公开原生入口的设计/授权/实现另立检查点，不自动扩展本计划。

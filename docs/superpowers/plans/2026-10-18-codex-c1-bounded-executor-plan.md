# Codex C1 有界执行器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task after user approval. Steps use checkbox (`- [ ]`) syntax for tracking. 不自动选择多 Agent、创建工作树或提交；执行方式与权限沿用用户明确选择。

**Goal:** 构建可完全离线验证的固定 C1 合成场景执行器，能够在服务前限制调用，并安全管理安装状态、原生尝试、截止时间与脱敏证据。

**Architecture:** 夹具内的五步场景门禁负责业务前准入，外部脚本负责不可复用的尝试名额、只读宿主预检和子进程监督。复用现有生产身份解析、绑定、应用服务和五工具；不改变生产授权路径，不建设通用工作流/插件框架。

**Tech Stack:** Windows、项目 `.venv/Scripts/python.exe`、实测 Python 3.13.1、声明下限 Python 3.10、mcp==2.3.0、stdlib/unittest/asyncio、现有 SDK stdio 合同。宿主合同目标仅 Codex 0.163.0，不执行自动升级或降级。

**Spec:** [后续开发设计与阶段路线](../specs/2026-10-18-development-roadmap-design.md)，重点第 4 节；继承 [C1 接线审阅说明](../../codex-c1-native-wiring-review.md)。2026-10-19 用户明确“开始执行”，现授权本计划 C1-E 的离线编码/测试/审阅；不授权实际宿主配置安装、真实 Codex 预检/模型分派或 Mem0 调用。进度与裁决记录在本计划 ignored SDD ledger，不覆盖旧证据。

## Global Constraints

- MCP SDK 固定为 mcp==2.3.0；不修改全局 Python、旧插件或正常项目配置。
- Mem0 是非核心正文、摘要和控制集合的持久来源；插件本地只保存标识、状态、指纹和锁。
- 完整 additionalContext 默认不超过 8192 UTF-8 字节，包含固定协议、项目核心、句柄、标记及区域包装；不是 token 预算。
- 核心自身超预算时失败，不截断核心；非核心按 P1/P2/P3、最旧 U、ASCII ID 顺序整条淘汰。
- 仅确认的有效采用推进 U；查询、读取、恢复、压缩和同一逻辑操作重试不推进 U。
- pending/unknown 不是成功；先定案原操作，不因搜索暂时无命中另起写入。
- 同项目经验可在 Codex 与 Claude Code 间共享；回执、句柄、U、窗口和检查点不跨宿主会话共享。
- 可信身份来自经过核验的宿主通道，不来自工具参数、任意句柄、启动 cwd 或历史会话扫描。
- 支持范围限单机器协调写入；不声明远端事务、CAS、跨机器锁或断电耐久。
- 信任本机 Codex 及获授权的 app-server 客户端；不宣称解析器识别任意恶意客户端编造的一致 metadata。
- 原生启用、在线合成写入及项目切换分别取得范围明确的授权。
- 不打包、不发布；未经明确要求不提交、推送或创建 Git 远程。
- 本计划实施阶段只运行本地测试替身/子进程，不调用真实 Codex、Mem0、账户、信任或额度接口。未来真实预检、安装和分派分别需要授权。
- 保持普通夹具模式的 15 项新增合同及已有全套行为。只在显式 bounded 模式限制业务，不能把 fixture-only bootstrap 放入生产五工具。
- 原生提案上限保持 A/B 各 1 次尝试、总计 2 次；每次 5 个准入调用、总计 10 个；单原生进程 180 秒；不自动重试、不自动运行 B。
- 用户未要求 Git 操作；各任务的提交检查点改为文件摘要/测试/审阅记录，不运行 git add/commit/push。旧 bundle、失败和 unknown 证据不覆盖。

---

## 执行进度（2026-10-19）

- [x] Task 1：服务前五步门禁。独立审阅发现根callId来源偏差，经真实SDK回归修复并复审批准；最终针对性29项通过。此前完整回归151项、144通过、7跳过，发生在该修复前，最终整体验收时重新执行。
- [x] Task 2：bundle/安装状态/保守尝试账本。审阅发现悬空路径、回退替换竞争和锁目录链接越界，经修复复审批准；受影响bundle16项、review6项通过。回退保留隔离配置字节，不直接删除；本地恶意管理员竞态不在保证内。
- [x] Task 3：只读宿主预检。修复Python3.10导入依赖、未知功能启用、缺失初始化/额度字段和清理预算后，规格/质量获批准。最新完整回归188项、181通过、7跳过；未运行真实宿主或Python3.10运行时。合成样本路径标注有1项Minor，交Task4同文件修整。
- [x] Task 4：事件状态机/证据核验。独立审阅的失败优先级问题经2轮修复后获规格/质量批准；最终针对性43项通过，原生itemID/metadata callId与传输/业务审计分别核验。完整225项结果发生在修复前，最终阶段重新执行。
- [x] Task 5：CLI/进程监督/整体交付。五任务与整轮修复复审均通过，重要发现全部修复，无parked residual；控制器最终完整回归277项、270通过、7项未验证跳过，262.547秒，退出0。108项源码与审阅快照一致，生产src/hooks未变。

**已确认的计划调整：原生exec驱动unsupported。** 固定0.163.0的exec harness将审批策略强制为Never，请求on-request不能满足既定人工审批边界。真实native run在预检、名额保留和启动之前明确阻断；离线进程/SDK/状态/证据软件已完成，但审批可验证的原生transport尚未实现。不能直接授权旧exec跑两个任务，下一步需另行审阅transport设计。详见 [最终交付验证与全部裁决](../../verification/codex-c1-executor.md)。

## 0. 输入基线、拆分与验收口径

已有最近交付：140 项、133 通过、7 项未验证 skip，2026-10-18 实跑。实施时重新运行，不把该数量写成未来固定预期。已有生产 `src/experience_memory/__main__.py:46-52` 仍默认拒绝；整个计划结束也不改变它。

先读：

- `tests/fixtures/codex_c1_server.py`：当前 bootstrap、种子、request_host、生产 create_server 复用。
- `scripts/prepare_codex_c1_review.py`：被动清单、预览、source_hashes、严格 review-only verify。
- `tests/contracts/test_codex_c1_stdio.py`、`tests/unit/test_codex_c1_fixture.py`、`tests/unit/test_codex_c1_review.py`：现有合同。
- `src/experience_memory/application/session_binding.py`、`interfaces/mcp_server.py`：真实授权和错误编码，原则上只读。
- `src/experience_memory/infrastructure/session_pointer.py`：现有单机锁与安全路径模式；不得拿 ControlPointer 存任意执行器账本。

历史 ignored `run_c1_once.py` 和 `inspect_host.py` 仅供理解已观察的 0.163.0 接口，不直接复制为生产级工具，不继续使用其旧授权计数或硬编码可执行路径。

### 文件职责

| 文件 | 操作 | 职责 |
| --- | --- | --- |
| `tests/fixtures/codex_c1_policy.py` | 新建 | 请求信息、串行五步门禁、真实服务结果审计、业务前拒绝 |
| `tests/fixtures/codex_c1_server.py` | 增量修改 | 可选 bounded 模式，保留 raw 服务供内部引导核验；将 MCP 五工具接到门禁代理 |
| `scripts/codex_c1_bundle.py` | 新建 | 不可变 review 快照、installed 校验、安装回执/精确回退、单机尝试账本 |
| `scripts/codex_c1_host.py` | 新建 | 固定只读 RPC 白名单、版本和有效宿主配置/额度预检、脱敏 |
| `scripts/codex_c1_results.py` | 新建 | 精确版本事件解码、唯一调用状态机、审计关联与结果判断 |
| `scripts/codex_c1_process.py` | 新建 | Windows进程所有权、限时/限量读取及确认停止；不解析业务结果 |
| `scripts/run_codex_c1.py` | 新建 | dry-run/inspect/install/run/rollback 薄 CLI与任务编排，组合前述模块 |
| `scripts/prepare_codex_c1_review.py` | 增量修改 | 新清单 schema、bounded 参数、门禁/执行器摘要与提案；仍不安装/执行 |
| `tests/unit/test_codex_c1_policy.py` | 新建 | 准入/状态/并发/审计失败测试 |
| `tests/unit/test_codex_c1_bundle.py` | 新建 | 状态校验、非覆盖安装/回退、名额保留与重启/并发测试 |
| `tests/unit/test_codex_c1_host.py` | 新建 | 固定 RPC、有效配置、版本/额度/缺字段/消息预算测试 |
| `tests/unit/test_codex_c1_results.py` | 新建 | 唯一调用计数、严格结果/证据关联和未知事件测试 |
| `tests/contracts/test_codex_c1_executor.py` | 新建 | CLI 全流程与真实本地假宿主/子进程关闭测试 |
| `tests/fixtures/codex_c1_fake_host.py` | 新建 | 固定离线假宿主，实际调用夹具 SDK，构造事件/挂起/失败；明确非原生证据 |
| `tests/fixtures/codex_c1_event_samples.json` | 新建 | 按 0.163.0 类型核对后手工脱敏的事件/RPC 合同样本，不含旧会话正文 |
| `docs/codex-c1-native-wiring-review.md`、`docs/contract-probes.md`、`docs/compatibility.md`、导航计划 | 增量修改 | 只记录实跑结果和未开放边界 |

不改 `src/`、`hooks/`、全局配置、旧原生证据、正常项目；不引入通用执行引擎、任意目标 RPC 或远程控制服务。

## Task 1: 在真实服务前实现五步门禁

**Files:** Create `tests/fixtures/codex_c1_policy.py`、`tests/unit/test_codex_c1_policy.py`；Modify `tests/fixtures/codex_c1_server.py`、`tests/contracts/test_codex_c1_stdio.py`，增加 bounded 场景而不替换普通模式测试。

**Interfaces:**

```text
RequestInfo(session_id: str, call_id: str, tool: str, arguments: dict)
  frozen dataclass；session_id取自current_host，call_id取自传输根callId，tool由路由提供。
ScenarioPolicy(run_nonce: str, audit_dir: Path)
ScenarioPolicy.invoke(request: RequestInfo, operation: Callable[[], dict]) -> dict
ScenarioPolicy.reject_transport(error_code: str) -> None
ScenarioPolicy.snapshot() -> dict
```

这些是本任务要建立的接口合同，尚未实现。`snapshot` 输出 phase、admitted、completed、session_id、failure_reason，不输出句柄。phase 为 ready/active/complete/rejected/unknown。Policy 拒绝抛领域 `MemoryError(INVALID_INPUT)`；传输缺身份仍使用原 HOST_SOURCE_UNVERIFIED。不得新增生产域错误码。

门禁代理提供现有 memory.search/read/write/status 与 retention.use 方法；只有 status 可按协议通过。`request_host` 在解析身份后，将元数据根 callId 与已核验 HostContext 放入夹具自己的请求 ContextVar，并在 finally 恢复；代理从该 ContextVar 取得会话/callId，不能由方法参数或最近一次请求变量补造。内部 bootstrap/种子 owner_status 使用保留的 raw 服务，避免内部 status 被误算为一个 MCP 步骤；bootstrap 对真实绑定操作显式调用同一个 policy.invoke。新构造参数 `bounded_scenario: bool = False`、CLI `--bounded-scenario`；生产 API 不增加此参数。

- [ ] **Step 1 — 写真实夹具回归。** 在测试 setUp 中用现有 make_projects 建 A/B，构造 bounded fixture；本地元数据包含现有四个一致身份/版本/根字段，并给每次请求非重复 callId。用真实 bootstrap/status 执行五步，第三/四步验证原异常码并继续，随后第六次 status 必须在仓储前拒绝。

```python
# ready 由第 1 步真实 bootstrap 返回，前五步已按设计逐项验证。
before = len(fixture.api.calls)
with fixture.request_host(dict(valid_meta, callId="call-six")):
    with self.assertRaises(MemoryError) as caught:
        fixture.memory.status(ready["session_handle"])
self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
self.assertEqual(len(fixture.api.calls), before)  # 不是只验证最终 U 没变
self.assertEqual(fixture.policy.snapshot()["admitted"], 5)
self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")
```

测试还要分别命名：`test_write_is_blocked_before_backend`（先合法引导，再传可成功写入的合成 candidate）、`test_expected_session_invalid_advances_only_its_step`、`test_wrong_handle_or_extra_argument_freezes`、`test_duplicate_call_or_session_switch_freezes`、`test_concurrent_calls_cannot_both_pass_one_step`、`test_audit_failure_freezes_unknown`、`test_bad_transport_freezes_bounded_session`。普通模式既有五工具写读采用仍通过。

- [ ] **Step 2 — 观察 RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_policy.py
```

预期因缺少 bounded policy/参数失败；先排除测试导入路径或语法错误。

- [ ] **Step 3 — 实现最小状态机和代理。** 核心顺序固定为 bootstrap/status/status/status/status。用非阻塞准入锁拒绝同时到达的第二个请求，而不是排队后偷偷把它当下一步；争用者设置线程安全 stop_requested 信号，当前请求在提交步骤前检查该信号并冻结。持锁期间覆盖验证、真实调用、结果校验、审计和状态更新。真实 operation 在准入之后才执行；结果与步期望不符时冻结。预期 SESSION_INVALID 审计并推进后原样重抛，使生产 `_invoke` 继续产生真实错误 DTO。业务审计使用独占新文件与白名单字段，必要字段严格类型核对；失败即 unknown。

```python
if not self.lock.acquire(blocking=False):
    self.stop_requested.set()
    raise MemoryError(ErrorCode.INVALID_INPUT)
try:
    self.check_request_before_operation(request)
    self.admitted += 1
    try:
        value = operation()
    except MemoryError as error:
        self.finish_error(request, error.code.value)
        raise
    self.finish_value(request, value)
    return value
finally:
    self.lock.release()
```

`check_request_before_operation` 验证stop_requested、阶段/步骤、精确参数集合、会话、callId 唯一性和正确句柄；`finish_error` 只允许步骤 3/4 的 SESSION_INVALID；`finish_value` 核验步骤 1/2/5。两个finish方法均在提交前复核stop_requested并先完成审计再推进completed，争用导致整场景rejected，第二请求不进入operation。三者为本任务在 ScenarioPolicy 内定义的私有方法。错误会冻结，不把不符结果转成空库成功。

- [ ] **Step 4 — GREEN 与集成复验。** 增加真实 SDK bounded 模式场景，检查 5 条业务审计与 5 条 transport 审计各自的含义，不把二者相加作调用数。重复/额外请求只生成拒绝记录，不进真实服务。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1*.py
```

- [ ] **Step 5 — 审阅检查点。** 核查 src/未变；审阅门禁能否在业务前阻断、内层 status 是否被误计数、并发与 ContextVar 是否隔离；记录 RED/GREEN 和新文件摘要，不提交 Git。

## Task 2: 区分 review/installed 状态并实现保守尝试账本

**Files:** Create `scripts/codex_c1_bundle.py`、`tests/unit/test_codex_c1_bundle.py`；Modify `scripts/prepare_codex_c1_review.py`、`tests/unit/test_codex_c1_review.py`。

**Interfaces:**

```text
ReviewSnapshot(bundle: Path, manifest_sha256: str, run_nonce: str, manifest: dict)
  frozen dataclass。
load_snapshot(bundle: Path) -> ReviewSnapshot
verify_snapshot(snapshot: ReviewSnapshot, *, phase: str, receipt: dict | None = None) -> dict
validate_approval(snapshot: ReviewSnapshot, approval: dict, action: str, project: str | None) -> None
install_configs(snapshot: ReviewSnapshot, approval: dict) -> dict
rollback_config(snapshot: ReviewSnapshot, project: str, receipt: dict) -> dict
reserve_attempt(snapshot: ReviewSnapshot, project: str, approval: dict) -> dict
finish_attempt(snapshot: ReviewSnapshot, reservation: dict, outcome: dict) -> None
```

approval 是已获用户确认后独立保存的范围记录，不能修改被动清单冒充授权。

`phase` 只能为 review/installed；review 时原 exporter.verify 严格语义不变。installed 时必须核对同一个不可变清单、源码/预览/标记和独立安装回执，以及实际 config bytes，无 hooks；不提供 skip-checks。

approval schema 精确含 schema_version=1、scope=native-c1-wiring、run_nonce、manifest_sha256、projects=[a,b]、max_attempts_total=2、max_attempts_per_project=1、max_tool_calls_per_task=5、task_timeout_seconds=180、允许的 actions、user_confirmation_reference。后者只是可追溯的人类确认引用，不是数字签名；实现阶段只用 `scope=offline-simulation` 的测试授权，不能据此分派真实 Codex。CLI 入口验证 action 与项目，不能仅检查一个 granted=true。

- [ ] **Step 1 — 写 RED 用例。** 使用临时目录真实导出器生成 bundle，传本地模拟授权安装。验证 review-only 在安装后拒绝、installed 校验成功；修改目标一个字节后校验和 rollback 都拒绝且保留修改；已有 config（含外链/reparse）不覆盖。测试失败不得清除样本凑通过。

```python
snapshot = load_snapshot(bundle)
receipt = install_configs(snapshot, simulation_approval)
self.assertEqual(verify_snapshot(snapshot, phase="installed", receipt=receipt)["state"], "installed")
config = bundle / "projects/a/.codex/config.toml"
config.write_bytes(config.read_bytes() + b"\n# local edit\n")
before = config.read_bytes()
with self.assertRaises(ValueError):
    rollback_config(snapshot, "a", receipt)
self.assertEqual(config.read_bytes(), before)
```

另测两个真实本地进程竞争 reserve_attempt(a) 只有一个成功；保留后模拟崩溃无 final report，重启不能重取；a 的 failed/unknown 冻结整轮 b；a verified 后 b 仍需显式 reserve；第三次始终拒绝。以磁盘记录/进程结果断言，不只断言 mock 调用。

- [ ] **Step 2 — 观察 RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_bundle.py
```

- [ ] **Step 3 — 实现固定 bundle 生命周期。** 新被动清单 schema_version=2，预览加入 --bounded-scenario，门禁/执行器源码进入既有 source_hashes；native_authorization 仍 false/0。不得重写旧 bundle，源代码变化后创建新目录。

独立安装回执记录 manifest/config/marker 摘要和本轮新建文件；只创建缺失的合成 config。尝试记录用独占创建；复用 SessionPointerStore.lock 的单机互斥能力，账本写自有严格 JSON，不伪装 ControlPointer。顺序 A→B；reserve 在进程创建前落盘，缺失/损坏/崩溃不回收名额。失败/unknown 发布整轮 stopped 标记；原始 reservation 和最终 report 分文件，均不覆盖既有证据。

```python
with coordination.lock(snapshot.run_nonce, "native-attempts"):
    check_manifest_approval_and_run_state(snapshot, project, approval)
    reservation = {"project": project, "attempt": 1, "state": "reserved",
                   "run_nonce": snapshot.run_nonce,
                   "manifest_sha256": snapshot.manifest_sha256}
    write_exclusive_reservation(snapshot, reservation)
    return reservation
```

上述两个私有函数均由本任务实现：前者严格验证预算/顺序/stopped/既有 reservation，后者在固定 bundle 子路径独占写入并 flush/fsync。JSON 拒绝重复键/超限/外链；原子替换仅用于本轮自己拥有的状态，不覆盖用户文件。安装中断保留部分回执/错误状态，下一轮拒绝，不自动当全装成功。

- [ ] **Step 4 — GREEN 与兼容。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_bundle.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_review.py
```

- [ ] **Step 5 — 审阅检查点。** 特别检查安装后是不是偷偷放宽原 verify、授权是否绑定清单和 action、Popen 失败后是否错误返还名额、回退是否碰用户修改/信任。仅登记摘要和测试，不提交。

## Task 3: 移入可维护的只读宿主预检

**Files:** Create `scripts/codex_c1_host.py`、`tests/unit/test_codex_c1_host.py`、`tests/fixtures/codex_c1_event_samples.json`；读取而不执行旧 ignored inspect_host 和精确版本 schema/source。

**Interfaces:**

```text
ReadOnlyProbe(command: list[str], overrides: list[str], *, timeout_seconds: float = 30)
ReadOnlyProbe.inspect(snapshot: ReviewSnapshot, project: str) -> dict
ReadOnlyProbe.close() -> None
validate_preflight(snapshot: ReviewSnapshot, project: str, *, version: str,
                   effective_config: dict, layers: list[dict], hooks: dict, usage: dict) -> dict
```

真实 CLI 将 command 固定构造为 `[str(executable)]`；只有测试工厂可以传入 Python+本地假宿主前缀，不从 bundle 接受可执行命令。

输出仅 root/版本、检查位、预期 MCP 的命令/args 摘要、disabled 状态、usage_allowed 及必要错误码。原始配置、密钥/账户信息、消息原文不落盘。公开网络不在范围内；生产读取使用明确传入的绝对可执行文件路径，不信任清单自报 executable。

- [ ] **Step 1 — 先核对合同，再写 RED。** 从已有 rust-v0.163.0 schema/source 核对 initialize、config/read、hooks/list、account/rateLimits/read 的精确响应。样本只保留合成路径/状态，记录来源和目标版本；需要补源文件时下载到新空目录并只读，不执行。实现时不能用一份自造响应同时生成断言和期望。

独立字面预期覆盖：正确0.163.0通过；0.163.1拒绝；usage false/缺失拒绝；项目层 disabled 拒绝；有效配置新出现一个未知启用 MCP 拒绝；层中 disabled 不得推翻 effective config；命令/args/env/超时不符拒绝；任意启用 Hook 或权限/sandbox不符拒绝。

```python
result = validate_preflight(snapshot, "a", version="codex-cli 0.163.0",
                            effective_config=valid_config, layers=valid_layers,
                            hooks=disabled_hooks, usage={"ordinaryUsageAllowed": True})
self.assertEqual(result["state"], "preflight_ready")
for usage in ({}, {"ordinaryUsageAllowed": False}, {"ordinaryUsageAllowed": 1}):
    with self.subTest(usage=usage), self.assertRaises(ValueError):
        validate_preflight(snapshot, "a", version="codex-cli 0.163.0",
                           effective_config=valid_config, layers=valid_layers,
                           hooks=disabled_hooks, usage=usage)
```

valid_config/valid_layers/disabled_hooks 在测试 setUp 从上述人工核对的合成样本加载，不由 validate_preflight 的辅助函数生成。

- [ ] **Step 2 — 观察 RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_host.py
```

- [ ] **Step 3 — 实现固定协议。** RPC 只允许 initialize、initialized、config/read、hooks/list、account/rateLimits/read；禁止 thread/start、turn/start 和直接 MCP tool call。methods 在发送前白名单核对；单回复/行最多 262144 UTF-8 字节，单预检总接收最多 2097152 字节、总时限 30 秒，满额或畸形即停止。拒绝重复 JSON 键，回复 ID 必须匹配；通知只接受已核对的无业务副作用类型。

```python
ALLOWED = {"initialize", "initialized", "config/read", "hooks/list", "account/rateLimits/read"}
if method not in ALLOWED:
    raise ValueError("C1_READ_ONLY_RPC_REJECTED")
```

版本检查只允许 executable --version；模拟 process/stdio 使用本地脚本，无真实 Codex。有效 config 为判断主依据，layers用于验证项目来源/禁用原因；未知结构不能递归合并猜结果。读 usage 不输出账户/凭据余额，不执行登录或回退。

- [ ] **Step 4 — GREEN 与真实本地协议测试。** 本地假 RPC 进程必须记录收到的方法到临时 ID-only 文件：任何 thread/turn 方法会让它测试失败。加入超长行、错误 ID、超时、重复键、stderr含秘密的脱敏断言；确认 close 后进程结束。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_host.py
```

- [ ] **Step 5 — 审阅检查点。** 核查未知新 MCP/全局入口能否漏过、配置优先级是否错判、是否提前启动任何模型，以及输出是否泄露原配置。只记录合同证据，不把模拟结果标为真实宿主。

## Task 4: 构建事件状态机与独立证据核验

**Files:** Create `scripts/codex_c1_results.py`、`tests/unit/test_codex_c1_results.py`；Modify `tests/fixtures/codex_c1_event_samples.json`，加入精确版本 exec event 合同样本。

**Interfaces:**

```text
NativeEvent(kind: str, item_id: str | None = None, thread_id: str | None = None,
            tool: str | None = None, arguments_sha256: str | None = None,
            terminal_status: str | None = None)
  frozen dataclass；本项目归一化类型，不声称宿主线上字段就叫这些名字。
decode_exec_line(raw: bytes) -> NativeEvent
NativeRunState(run_nonce: str, project: str)
NativeRunState.consume(event: NativeEvent) -> None
NativeRunState.summary() -> dict
validate_evidence(snapshot: ReviewSnapshot, project: str, reservation: dict,
                  native: dict, transport_rows: list[dict], outcome_rows: list[dict]) -> dict
```

归一化 kind 限 thread_started/call_started/call_finished/turn_completed/turn_failed/ignored_known/unexpected_tool。真实 raw 字段首先核对精确版本 exec_events 源类型；缺字段不能通过 fallback 默认值变成成功。summary 区分 unique_calls、completed_calls、native_thread_id、turn_completed、failure/unknown 原因。

- [ ] **Step 1 — 写独立字面样本 RED。** 每个唯一 item 的 started/completed 各一条，预期 unique_calls=1；相同通知重复不二次计数，内容冲突则拒绝。对同一个工具的两个不同 item ID 必须计2；第6个唯一调用立即拒绝。样本 body不包含真实句柄/正文；规范参数摘要期望从人工固定 JSON 算一次并写字面，不用被测 reducer 计算期望。

```python
state = NativeRunState("c1-offline", "a")
state.consume(NativeEvent("thread_started", thread_id="thread-one"))
state.consume(NativeEvent("call_started", item_id="item-1", tool="c1_bootstrap",
                          arguments_sha256="a" * 64))
state.consume(NativeEvent("call_finished", item_id="item-1", tool="c1_bootstrap",
                          arguments_sha256="a" * 64, terminal_status="completed"))
self.assertEqual(state.summary()["unique_calls"], 1)
self.assertEqual(state.summary()["completed_calls"], 1)
```

证据测试分别破坏一个必要事实：只有5个transport无业务结果；旧 nonce；其他 thread/root；业务 U=true/1；种子proof=false；两个种子相同；非预期错误码；第3步成功；重用业务audit invocation；原生未完成/退出非0；callId与itemId不等但一一映射明确；映射歧义。不能单凭模型结束标记通过。

- [ ] **Step 2 — 观察 RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_results.py
```

- [ ] **Step 3 — 实现固定 reducer / evidence validator。** 独立记录原生 item ID 与传输 callId摘要，不假定相等；用串行步骤+工具+规范参数摘要+各自唯一ID映射。bootstrap结果只取ID、后端、种子proof字段到内存，报告只留摘要。状态completed/failed只是原生调用终态，业务成败以真实结果/夹具白名单业务审计核验；预期SESSION_INVALID不等于整轮失败。

```python
checks = {
    "native_finished": native["turn_completed"] is True and native["process_exit"] == 0,
    "five_unique_calls": native["unique_calls"] == 5 and native["completed_calls"] == 5,
    "transport_linked": transport_identity_matches(snapshot, project, native, transport_rows),
    "business_verified": five_step_results_match(snapshot, project, native, outcome_rows),
}
return {"status": "verified" if all(checks.values()) else "failed", "checks": checks}
```

两个内部核验函数由本任务实现上述所有严格关联，任何必要数据缺失/无法解析时返回 unknown而不是补true；上面是完整数据分支。报告同时保留 execution_origin=native_codex/offline_simulation；只能从执行器运行模式传入，不能从模型或 bundle自报。validator不能独自证明材料真伪，原生声明仍需审阅实际捕获。

- [ ] **Step 4 — GREEN。** 测试raw未知type、重复键、超大行、错误JSON、out-of-scope工具、只输出C1_WIRING_DONE、缺completed、提前turn.completed、审计重放；全部拒绝或unknown。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_results.py
```

- [ ] **Step 5 — 审阅检查点。** 强调两个ID命名空间不可偷换、started/completed不得双计、工具expected error不得按transport completed误判；严格区分结构合同和真实宿主证据。

## Task 5: 接通薄 CLI、真实本地子进程监督与整体验收

**Files:** Create `scripts/codex_c1_process.py`、`scripts/run_codex_c1.py`、`tests/contracts/test_codex_c1_executor.py`、`tests/fixtures/codex_c1_fake_host.py`；Modify exporter（最终可运行配置预览）、相关文档与导航。前4任务模块不在本任务合并成单个巨型文件；OwnedProcess位于process模块，CLI只负责组合。

**Interfaces:**

```text
OwnedProcess(argv: list[str], *, cwd: Path, env: dict, deadline: float)
OwnedProcess.events() -> Iterator[bytes]
OwnedProcess.stop() -> dict  # root_exited、descendants_exited、reason；不确认则unknown
run_project(snapshot: ReviewSnapshot, project: str, approval: dict, receipt: dict,
            *, executable: Path, process_factory=OwnedProcess,
            probe_factory=ReadOnlyProbe, execution_origin: str = "native_codex") -> dict
main(arguments: list[str] | None = None) -> int
```

两个 factory 只供测试注入真实本地假子进程；真实 CLI 固定使用 OwnedProcess/ReadOnlyProbe，不从 bundle 动态导入工厂。

CLI 子命令：dry-run（默认；只读bundle，不调用宿主）、inspect（须单独只读宿主授权）、install（须安装授权）、run（须原生任务授权+项目）、rollback（须本轮回退授权）。run不提供all/loop/retry/force/skip-checks选项。所有路径绝对且限定bundle/受审阅根；不能从任意清单执行shell字符串。

- [ ] **Step 1 — 写本地进程合同 RED。** fake_host为本地Python脚本，可模拟 --version、白名单RPC及exec；完整成功模式通过真实MCP SDK调用bounded夹具，用构造metadata产生离线五步和事件，输出明确offline_simulation。其他模式：第6次、禁止工具、A失败、额度false、超时、超长stdout、stderr秘密、启动后崩溃、孵化子进程后挂起。模拟输入不改变生产编码器。

```python
result = run_project(snapshot, "a", simulation_approval, receipt,
                     executable=Path(sys.executable), process_factory=fake_process_factory,
                     probe_factory=fake_probe_factory, execution_origin="offline_simulation")
self.assertEqual(result["status"], "verified")
self.assertEqual(result["execution_origin"], "offline_simulation")
self.assertFalse(result["native_evidence_claimed"])
with self.assertRaises(ValueError):
    reserve_attempt(snapshot, "a", simulation_approval)
```

fake_process_factory在该合同文件实现：返回运行`sys.executable -I tests/fixtures/codex_c1_fake_host.py`的OwnedProcess；fake_probe_factory返回以同一本地脚本为command前缀的ReadOnlyProbe，使版本/RPC测试也实际经过本地stdio，绝不启动真实Codex。不能使用MagicMock吞掉launch/close副作用。CLI subprocess测试另断言默认dry-run无原生进程/无安装/无名额消耗；无授权run拒绝；A失败不创建B reservation。

- [ ] **Step 2 — 观察 RED。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_executor.py
```

- [ ] **Step 3 — 接通有界监督。** 顺序必须是校验approval/snapshot/installed → 只读预检 → 再校验字节和回执 → reserve_attempt落盘 → Popen → reducer → evidence → finish。进程创建异常也保留reserved并发布unknown/stop，不返还名额。子进程环境移除MEM0_API_KEY；日志仅保留白名单与哈希，不输出全部env。

```python
verify_snapshot(snapshot, phase="installed", receipt=receipt)
validate_approval(snapshot, approval, "run", project)
preflight = probe.inspect(snapshot, project)
if preflight["state"] != "preflight_ready":
    raise ValueError("C1_PREFLIGHT_BLOCKED")
verify_snapshot(snapshot, phase="installed", receipt=receipt)
reservation = reserve_attempt(snapshot, project, approval)
try:
    report = execute_reserved_process(reservation)
finally:
    cleanup = owned_process_stop_if_started()
# execute_reserved_process / owned_process_stop_if_started由本任务实现，
# 异常路径同样构造unknown报告，cleanup和最终报告只记录到本轮独占文件。
```

真实启动argv显式为已核对的Codex executable、--no-daemon、exec、--ephemeral、--json、只读沙箱、on-request审批、固定-C合成根、清单进程级隔离参数和固定prompt；不切模型或账户。stdout/stderr各自读取队列有界；每行262144字节、总量2097152字节、截止180秒，溢出/未知event即停止。

Windows进程监督使用本轮拥有的Job Object管理自身启动的进程/后代，不按进程名批量终止。创建原生进程时先受控挂起、加入Job后才恢复执行，关闭时终止该Job并核验；创建/附加/恢复失败都不得继续原生执行。只使用Windows标准API/stdlib，不新增跨平台框架；若环境不支持安全Job所有权，真实分派报unsupported，不以仅杀父进程宣称已清理。OwnedProcess合同测试必须实际启动挂起的父/子Python进程并确认二者退出；无法确认即unknown、整轮停止。此接口不承诺瞬时终止或硬token预算。

- [ ] **Step 4 — 验收CLI全部失败分支。** 新schema预览必须明确bounded模式，source_hashes包含新实现；代码冻结后生成全新bundle并校验，旧bundle不覆盖。模拟安装→inspect→run→精确rollback可以全程离线；修改配置后rollback拒绝且保留修改。raw stdout/stderr、候选正文、账户字段与异常原文均不出现在结果/audit文件中。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_executor.py
```

```bash
.venv/Scripts/python.exe scripts/test.py
```

记录实际发现、通过、skip和退出码，不预填未来总数；现有7项未验证skip仍不能升级为通过。

- [ ] **Step 5 — 独立审阅与交付。** 逐条对照Spec：服务前五步限制、installed验证不弱化、A/B名额保守、未知拒绝、超时/树退出、ID映射、日志无正文、无真实宿主调用、生产默认拒绝不变。修复重要问题后重跑完整测试，保存审阅摘要并只更新真实结果。deliver源码/配置预览/执行清单及限制，不自动进入原生阶段。

## 6. 计划自查与需求映射

| 设计要求 | 承担任务 |
| --- | --- |
| 服务前工具/顺序/身份/参数/第6次限制 | Task 1 |
| 并发与业务审计、真实预期拒绝 | Task 1 + 4 |
| 不可变清单、安装后校验、不覆盖与精确回退 | Task 2 |
| 先保留名额、崩溃不重取、A失败停止B | Task 2 + 5 |
| 版本/有效配置/Hook/额度/白名单RPC | Task 3 |
| 唯一调用数、错误与完成分离、ID映射/重放 | Task 4 |
| 默认dry-run、过程限时/字节上限/所有权关闭 | Task 5 |
| 不运行真实Codex或Mem0、没有生产源码修改 | 每任务检查点 + 最终源码快照 |
| 四层证据分开、文档/摘要/不提交 | 每任务检查点 + Task 5 |

实现前用户审阅本计划。设计仍未知的后续宿主能力不在这里猜实现：C1-P生产候选、C2、H1、A1/A2和X1各自另立计划。它们已在路线设计中有目标/门槛，但不属于本次有界执行器任务。

## 7. 未来原生运行前的独立交接

即使本计划全部完成，也只交付离线验过的执行器。需要另行明确确认：新bundle/source/config摘要、两个合成根、正常项目信任、安装/回退范围、只读预检范围、A/B各一个任务上限、合成种子而非历史原生句柄、停止/保留规则。

届时先重读实际Codex版本与ordinary usage；版本漂移/额度不允许/项目未信任都停止。不得复用2026-10-16的3次原生尝试授权，也不得把本轮“整理计划”解释成新的模型或真实API授权。

# Experience Memory 插件实现计划

> 2026-10-14：本文保留历史任务索引；后续实施以[新的后续计划](2026-10-14-experience-memory-followup-plan.md)为准。真实功能仍未实施，当前不打包。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可在多个项目使用的经验记忆插件，使非核心经验经 Mem0 查询、精确读取、显式维护和有效使用登记，并在 Codex 压缩后按确认协议恢复。

**Architecture:** 独立插件包包含两个 Skill、一个 MCP 工具入口和 command Hooks，共用一套服务层与 Mem0 Adapter。Mem0 是非核心经验及活动窗口的持久来源；本地仅保留控制指针与锁。正式留存区决定当前有效非核心集合。

**Tech Stack:** Python 3.10+、标准库 dataclasses/urllib/unittest、官方 MCP Python SDK `mcp==2.3.0` 设计基线、Mem0 Platform HTTPS API；首期 Codex Windows 根会话、单宿主。

**Spec:** [设计稿](../specs/2026-10-12-mem0-compaction-retention-design.md)，协议和状态约束读取第 1—9 节，跨项目封装与能力读取第 10—11 节。

## Global Constraints

- 核心固定遍历；非核心正文/摘要只在 Mem0 持久保存，不能在本地配置、日志、指针或缓存写入规则集合。
- 标记为 `【留存:P1|经验ID|U42】一行摘要`；P1>P2>P3，低等级先淘汰、同级 U 最小先淘汰、同级同 U 按 ID ASCII 字典序决定。
- U 由已提交的本会话有效使用事件分配；压缩、恢复、纯重复召回不刷新。同操作重试不生成第二次逻辑效果。
- 本扩展整体上下文上限为 8192 UTF-8 字节，包含固定提示词、项目核心载荷和包装；不冒充 token 数。核心超限报错，不淘汰核心。
- 所有服务端操作先校验仓库/会话/版本；保存结果未知先核对 operation_id/event_id，不能盲目重复写入。
- 正式留存区为有效依据，旧摘要不能激活已淘汰或失效规则；不承诺抹除不透明压缩状态中的全部旧经验。
- PreCompact 不能追加压缩提示词；正式恢复使用 SessionStart(source=compact)。首期不修改 compact_prompt。
- 多项目机制通用，数据默认隔离；同仓库 worktree 的项目经验共享，U/窗口/检查点按会话分开。
- 不改旧 Mem0 插件缓存源码，不导入其私有函数；原生 Hook、记录保真和输出配置必须实测后才能声明支持。
- 本计划定义 T01—T08 功能步骤。2026-10-13 已确认先实施 S1—S5，用户随后要求不打包、在 SLS 同级初始化独立 Git；当前根目录是 `experience-memory/`。未安装、停用、提交或推送。

## Review Focus

1. 网络写成功但响应丢失：新 U/新检查点不能继续分配，先定案；测试归 T03/T05/T06。
2. 不同项目使用相同 EXP-ID，或同仓库不同 worktree：经验与会话隔离必须符合范围；测试归 T02/T08。
3. 格式示例、跨会话读取回执、纯召回和重复 Hook：不能登记或推进 U；测试归 T05/T07。
4. 第二次压缩、记录改版、预算缩小：不能复活旧项或把旧版本恢复为有效；测试归 T06/T07。
5. 宿主输出落盘、插件与旧 Hook 双重生效、控制记录进入语义候选：不能宣称链路满足存储及留存要求；测试归 T01/T03/T08。

## 文件职责与类型契约

2026-10-13 补充：用户已确认并启动[设计第 12 节](../specs/2026-10-12-mem0-compaction-retention-design.md)的 S1—S5，执行记录见[骨架计划](2026-10-13-experience-memory-skeleton.md)。下列路径统一为分层包；T01—T08 的能力顺序和验收边界保持不变，骨架通过不勾选真实功能验收。

下列路径相对于当前独立项目根 `experience-memory/`。已创建的是骨架入口与最小模型/Port；其余文件在对应真实用例出现时建立。

```text
experience-memory/
├─ plugin.json、mcp.json、pyproject.toml
├─ hooks/hooks.json、hooks/adapter.py
├─ skills/memory-query/SKILL.md
├─ skills/memory-intake/SKILL.md
├─ prompts/core.md、register.md、restore.md、compact-supplement.md
├─ src/experience_memory/
│  ├─ domain/models.py、errors.py、ports.py、selector.py（T06）
│  ├─ application/contracts.py、services.py
│  │  └─ session_binding.py、registry.py、checkpoint.py（后续）
│  ├─ interfaces/mcp_server.py、hooks.py、diagnostics.py
│  ├─ infrastructure/unconfigured.py
│  │  └─ mem0_client.py、experience_repository.py、control_repository.py
│  │     scope.py、session_runtime.py、session_pointer.py（后续）
│  └─ bootstrap.py、config.py、__init__.py、__main__.py
├─ tests/unit/、tests/architecture/、tests/contracts/、tests/native/（后续）
├─ tests/fixtures/（只放合成数据）
└─ docs/compatibility.md、operations.md
```

下表是目标类型合同，不能当作全部已实现。领域模型在 `domain/models.py`，配置在根 `config.py`；OperationResult/SearchResult/MemoryCandidate/HookResult/DiagnosticReport 在 `application/contracts.py`，MCP/Hook DTO 在 `interfaces/`。新增类型随功能任务补齐，不能另造同义类型或让基础设施导入应用 DTO。

| 类型 | 必需字段/约束 |
| --- | --- |
| Scope | project_key、app_id、project_id、legacy_project_ids、user_id；不允许通配身份 |
| Config | enabled、mem0_api_url、project_scope、core_paths、retention_budget_bytes、search_limit、request_timeout_seconds、hook_deadline_seconds；默认值见设计第 11.3 节 |
| SessionContext | scope: Scope、session_id、session_handle、host_id；必须来自绑定校验 |
| MemoryRecord | record_id、experience_id、status、body、retention_text、fingerprint、metadata |
| ReadReceipt | receipt_id、record_id、experience_id、fingerprint、session_handle；保存并回读后才返回 |
| MemoryCandidate | experience_id、text、metadata、admission_basis；不允许无来源候选自动 active |
| OperationResult | status: confirmed/pending/unknown/failed、operation_id、record_ids、event_id、error_code |
| SearchResult | succeeded、items: tuple[MemoryRecord, ...]、error_code；succeeded=true 且 items 空才是成功无命中 |
| WindowEntry | record_id、experience_id、priority、last_used_seq、fingerprint；持久表示不带正文 |
| ActiveWindow | record_id、session_handle、generation、u_cursor、entries: tuple[WindowEntry, ...]、parent_window_id |
| UseReceipt | operation_id、entry: WindowEntry、window_id；只在活动窗口回读确认后成功 |
| MaterializedEntry | entry: WindowEntry、line: str；仅当前进程内存使用，不写本地 |
| RetentionSelection | selected、dropped(reason)、rendered_context、payload_bytes |
| Checkpoint | record_id、schema_version=1、session_handle、generation、source_window_id、entries、budget_bytes |
| HookResult | continue_: bool、stop_reason、additional_context；由 Adapter 转成对应事件合法 JSON |
| DiagnosticReport | supported_version、credentials_available、scope、pending_operations、active_window_id、compatibility_conflicts；不含正文或密钥 |

所有测试命令在插件根使用专用环境解释器执行 `python -m unittest ... -v`；下方功能模块检查仍待实现。每个编码任务先运行断言失败，再完成最小实现并确认通过；没有请求 Git 交付时不提交或推送。

## 任务依赖与里程碑

```text
T01 接口验证
  → T02 范围/配置/会话绑定
  → T03 检索与精确存储
  → T04 经验录入维护
  → T05 使用登记与活动窗口
  → T06 选择器与检查点
  → T07 原生 Hook/提示词
  → T08 项目切换与跨项目验收
```

第一阶段 T01—T06 完成可测试的记忆与留存内核；第二阶段 T07—T08 才形成实际压缩恢复插件。无界面、无新向量数据库、无新的中心编排服务。

### T01：验证宿主与 Mem0 控制状态契约

**Files:** Create `tests/contracts/test_platform_contracts.py`、`tests/native/test_hook_contracts.py`、`tests/fixtures/synthetic_records.json`、`docs/compatibility.md`。

**Interfaces:** Produces `CompatibilityReport`：host_version、mcp_sdk_version、native_compact_events、session_binding_method、exact_filter_verified、immutable_verified、output_cap_verified、status。状态仅 supported / unsupported / unverified。

- [ ] 编写接口用例：`test_infer_false_roundtrip` 断言合成 JSON 原样回读；`test_immutable_control_record` 断言指定控制记录在后续提炼场景不改写；`test_control_filter_before_top_k` 断言大量控制记录不挤占经验名额；`test_id_read_preserves_scope` 断言返回原范围和稳定 ID。
- [ ] 在专用合成测试范围运行 `python -m unittest tests.contracts.test_platform_contracts -v`，记录官方接口与当前账号实际结果。不得对当前项目真实经验做更新或删除以验证假设。
- [ ] 用最小 Hook 探针验证手动/自动压缩，断言 PreCompact、compact SessionStart 关联本次会话，并且后一 Hook 的合成哨兵在立即续跑上下文可见；验证 additionalContextLimit=0 和严格 8192 字节上限不会落盘。
- [ ] 验证 session_handle 绑定途径及 MCP 官方 SDK 的 stdio 初始化/工具调用；不得假定宿主元数据一定提供某个 session 字段，必要时使用 Hook 发出的句柄并核对工作区。
- [ ] 编写 `docs/compatibility.md`，列清通过、失败与未验证项。任一必需契约未成立时停止后续“可交付”路径，先调整设计，不写成 supported。

**验收：** 构建清楚的能力矩阵；不以二进制字段存在代替原生压缩证据。真实探针需在实施阶段审阅和启用，不在本轮执行。

### T02：项目范围、配置与会话绑定

**Files:** Modify `src/experience_memory/domain/models.py`、`config.py`、`bootstrap.py`；Create `infrastructure/scope.py`、`session_runtime.py`、`session_pointer.py`、`application/session_binding.py`、`tests/unit/test_scope_binding.py`。已有包与入口只增量维护。

**Interfaces:** Produces `resolve_scope(cwd: Path, overrides: dict) -> Scope`、`load_config(cwd: Path) -> Config`、`bind_session(scope: Scope, session_id: str, host_id: str) -> SessionContext`、`verify_session_handle(handle: str, cwd: Path) -> SessionContext`、`SessionPointer.read/write_control_pointer(...)`。

- [ ] 编写失败断言：SSH/HTTPS 的同仓库 identity 等价；同名不同 host 仓库不同；同仓库 worktree 的 project_key 相同、session_handle 不同；无远程歧义或通配身份被拒绝；外部 core_paths 被拒绝；同名 EXP-ID 不跨范围。
- [ ] 运行 `python -m unittest tests.unit.test_scope_binding -v`，确认未实现行为失败。
- [ ] 实现类型、配置默认值（8192、5、10、30）、规范化范围和宿主绑定。既有 namespace 使用显式兼容映射；不能仅按目录名或 app_id 猜身份。
- [ ] 实现当前宿主同会话串行锁与原子控制指针；加入 `test_pointer_contains_no_rule_data`，断言文件仅有 ID、代次、待处理状态/操作标识，不含规则集合、正文或摘要。
- [ ] 重跑同模块；期望全通过，并确认没有修改旧插件配置。

**验收：** 项目范围与会话身份能确定解析，跨项目/旧会话句柄不能写入或登记。远端绑定记录由 T03/T05 接通。

### T03：Mem0 Adapter、任务检索与精确读取

**Files:** Create `src/experience_memory/infrastructure/mem0_client.py`、`experience_repository.py`、`control_repository.py`、`tests/unit/test_mem0_store.py`、`tests/fixtures/platform_responses.json`；Modify `domain/ports.py`、`application/contracts.py`、`application/services.py`、`bootstrap.py`。

**Interfaces:** Consumes Scope/SessionContext，基础设施实现领域 ExperienceRepository / ControlStateRepository，返回领域记录或明确失败/待定契约，不依赖应用 DTO；MemoryApplicationService 负责 SearchResult / OperationResult 编码、精确核验及 ReadReceipt。控制追加/结果定案由领域 Port 在真实接口验证后补齐，公共用例仍经 ApplicationService。

- [ ] 写失败用例：成功无命中与 HTTP/认证失败不同；NOT 控制类型进入服务端过滤；分页完全读取精确匹配；按 ID 读到另一项目被拒绝；自动摘要缺字段返回 needs_review；凭据与正文不进诊断日志。
- [ ] 运行 `python -m unittest tests.unit.test_mem0_store -v`，确认失败后实现 HTTPS、超时/总截止时间、scope 校验、结构化返回和指纹。非核心正文不写本地。
- [ ] 写入路径用 `infer=false`，控制记录追加用 `immutable=true`。返回 ID/事件状态后回读；只有确认后返回 confirmed。
- [ ] 加入 `test_write_response_lost_is_unknown` 和 `test_unknown_operation_not_blindly_retried`：响应丢失保留待处理 operation_id/event_id，精确核对；暂时空列表仍不得重新 add。重复同键记录报告 conflict。
- [ ] 生成并回读读取回执，保存 record_id/指纹/会话绑定，不把原生格式化查询正文当可靠身份。重跑全模块，期望通过。

**验收：** 查询、按 ID 回读和控制记录保真具有独立证据；不依赖旧插件私有模块，也不声称 Mem0 有事务/CAS。

### T04：显式经验录入与维护

**Files:** Modify `src/experience_memory/application/services.py`、`contracts.py`、`infrastructure/experience_repository.py`、`skills/memory-intake/SKILL.md`；Create `tests/unit/test_memory_intake.py`。

**Interfaces:** Produces `MemoryApplicationService.write(session_handle, action: Literal['upsert','invalidate'], candidate: MemoryCandidate, operation_id) -> OperationResult`。候选包含 expected_fingerprint；服务核验句柄并判断新增/更新，不要求 agent 猜测 add/update；不接纳任意 session_id。

- [ ] 写用例：同稳定 ID 不重复新增；语义相近候选输出合并建议，不自动新建；校验过时指纹/缺少来源/跨项目操作被拒绝；失效保留 ID 与来源；技术未验证内容不能被标成已验证。
- [ ] 运行 `python -m unittest tests.unit.test_memory_intake -v`，确认失败后实现语义验重线索＋精确稳定 ID 查询、更新和失效标记。
- [ ] 对同宿主同项目写操作串行化；写前后核对 expected_fingerprint，发现远端竞争时停止并报告冲突，不声称跨宿主并发写安全。
- [ ] 编写 Skill：判断纳入/已覆盖/无需纳入/待核实，已覆盖复用唯一记录；不确定则向用户询问。规范正文已存在时只存导航经验。
- [ ] 重跑同模块，并验证 pending/unknown 未冒充已保存。首期工具不暴露全库清空或自动硬删除。

**验收：** 用户认可的经验能明确保存、更新或失效，保存状态可回读；Skill 决定价值，代码负责身份和写入机制。

### T05：有效使用登记、U 与活动窗口

**Files:** Create `src/experience_memory/application/registry.py`、`tests/unit/test_registry.py`；Modify `application/services.py`、`session_binding.py`、`infrastructure/control_repository.py`、`domain/ports.py`。

**Interfaces:** 公共入口为 `RetentionApplicationService.use(session_handle, receipt_id, priority, reason, operation_id) -> UseReceipt`；内部 UseRegistry Case 可复用 `register_use(ctx, ...)` 与 `get_active_window(ctx)`，不由 MCP/Hook 直接调用。

- [ ] 写失败断言：缺回执、跨会话回执、无效等级、空用途、旧指纹不得登记；格式示例不得触发；相同 operation_id 重试返回同 U/窗口，不重复推进。
- [ ] 运行 `python -m unittest tests.unit.test_registry -v`，确认失败后实现 Mem0 读取回执核对和有效使用事件追加；活动集合及 U 权威状态在 Mem0，本地仅控制指针与锁。
- [ ] 首次会话提交空活动窗口；每次成功使用从 confirmed 游标分配 U+1。父窗口冲突、同键多记录或结果未知时冻结推进，先定案。
- [ ] 提交实际活动窗口并回读成功后返回 UseReceipt；模拟保存成功但指针发布前进程中断，断言按 operation_id 定案，不重复事件、不损失已确认 U。
- [ ] 重跑同模块，确认纯查询/读取/恢复路径不调用 register_use。文档表明用途是 Agent 声明，不作为规则正确执行的证明。

**验收：** 活动集合不会随普通召回不断扩张，重复工具/Hook 不更新 U。

### T06：留存选择器与检查点

**Files:** Create `src/experience_memory/domain/selector.py`、`application/checkpoint.py`、`tests/unit/test_selector.py`、`tests/unit/test_checkpoint.py`；Modify `application/services.py`、`contracts.py`、`domain/models.py`、`ports.py`。

**Interfaces:** 领域产生 `select_retention(...) -> RetentionSelection`（领域选择结果，不含应用/Hook DTO）；内部 Checkpoint Case 承担 prepare/restore，外部只经 RetentionApplicationService 的 Hook 用例，最终转为应用 HookResult。

- [ ] 写选择器断言（标记与摘要为整条）：`test_priority_then_oldest`、`test_ascii_tie`、`test_duplicate_id_newest_version`、`test_utf8_budget_includes_fixed_context`、`test_core_only_over_budget_fails`。例如成本相同且只容纳一条 P1 时，保留 U 较大者。
- [ ] 运行 `python -m unittest tests.unit.test_selector -v`，确认失败后实现精确 UTF-8 成本计算和协议排序；不调用模型裁剪摘要。
- [ ] 写检查点断言：本次 ready 必须已回读；失败不取上次 ready；P1 读取未知/改版停止，P2/P3 记录未恢复并允许剩余集合；明确失效和按预算淘汰不复活。
- [ ] 运行 `python -m unittest tests.unit.test_checkpoint -v`，确认失败后实现 prepare/restore。边界 ID、generation、父窗口、schema_version=1、操作状态明确；有界截止时间内完成保存确认。
- [ ] 恢复重新计算预算，将实际恢复集合先追加为新的 confirmed 活动窗口，再生成 HookResult；加入 `test_second_compaction_does_not_resurrect_evicted`、`test_restoration_keeps_u` 和 `test_actual_window_commit_failure_blocks_emission`。
- [ ] 运行两个模块，期望通过。读取控件的工作量仅取决于当前窗口及之后事件，不遍历全库非核心经验。

**验收：** 内核通过跨两次压缩的连续性测试。它证明逻辑，不证明原生 Hook 已执行。

### T07：MCP、command Hooks 与提示词

**Files:** Modify `src/experience_memory/interfaces/mcp_server.py`、`hooks.py`、`application/services.py`、`bootstrap.py`、`hooks/hooks.json`、`hooks/adapter.py`、`prompts/*.md`、`skills/memory-query/SKILL.md`；Create `tests/unit/test_codex_hooks.py`、`tests/contracts/test_mcp_tools.py`。

**Interfaces:** Consumes T02—T06 服务。Produces `handle_hook(event: dict) -> dict`；MCP 暴露 `memory_search`、`memory_read`、`memory_write`、`memory_use`、`memory_status`。工具参数与 SessionContext 相互独立，句柄经服务校验，不接纳自由 session_id 作为身份。

- [ ] 写失败用例：PreCompact/PostCompact 只出合法公共字段；SessionStart(source=compact) 出 additionalContext；其他 source 只初始化/注入固定核心；pending/unknown/P1 失败出 continue=false；无源码示例扫描。
- [ ] 运行 `python -m unittest tests.unit.test_codex_hooks tests.contracts.test_mcp_tools -v`，确认失败后实现官方 SDK stdio 入口和 Adapter。Hook 直接调用服务层，不等待 MCP 连接或递归调用其他 Hook。
- [ ] Hook 超时配置 30 秒，内部调用共用操作截止时间；PostToolUse 只校验真实 UseReceipt，不重复提交有效使用事件。
- [ ] 从设计第 8 节维护三段提示词：固定核心/登记、压缩补充、恢复说明。compact-supplement 仅保留为未启用资源，禁止把它单独写入 compact_prompt 覆盖原任务。
- [ ] 编写查询 Skill：每个独立问题/范围变化触发检索；核验来源、状态和适用性后采用；核心只读明确入口。加入独立合成场景：新子任务、工具示例、旧摘要诱导、纯召回，检查采用行为。
- [ ] 重跑同模块；通过后在 T01 的原生测试会话验证手动和自动压缩续跑哨兵、指纹/U 一致及完整载荷；静态 JSON 检查不计原生通过。

**验收：** 实际宿主收到正式留存区；不以提示词或安装成功代替此证据。

### T08：源码接入、旧插件衔接与双项目验收

**Files:** Modify `plugin.json`、`mcp.json`、`src/experience_memory/interfaces/diagnostics.py`、`application/services.py`、`docs/operations.md`、`docs/compatibility.md`、`tests/contracts/test_package.py`；Create `tests/native/test_cross_project.py`。

**Interfaces:** Produces `diagnose(ctx: SessionContext) -> DiagnosticReport`：supported_version、credentials_available、scope、pending_operations、active_window_id、compatibility_conflicts；不含正文或密钥。

- [ ] 写源码接入断言：manifest 路径均在项目内，运行时不含 SLS 名称/业务路径/旧缓存私有 import；标准配置不含密钥或经验正文；只有两个 Skill；诊断不泄漏敏感内容。
- [ ] 运行 `python -m unittest tests.contracts.test_package -v`，确认失败后完成 portable manifest 和直接按源码目录接入的说明；适配本机 Python，不硬编码作者解释器路径。不创建分发包或发布 marketplace。
- [ ] 用两个合成项目测试：A/B 同名 EXP-ID 不串读；A 两个 worktree 共享项目经验但 U/检查点独立；在 B 停用不影响 A；未知配置/旧协议版本拒绝使用并显示原因。
- [ ] 检查原生旧 Mem0 自动 Hook/工具冲突。真正启用新插件前按项目停用旧主动入口、保留云端经验和已确认范围映射；不全局卸载、不直接改缓存源码、不删除真实经验。启用/回退说明列出精确影响。
- [ ] 在仅有新责任方的测试链路检查本地指针/日志/临时 Hook 输出没有非核心正文缓存；旧历史缓存只报告位置与影响，未经明确清理指令不删除。
- [ ] 执行 `python -m unittest discover -s tests/unit -v` 与 `python -m unittest discover -s tests/contracts -v`；原生项目测试在批准的测试会话独立运行，并填写支持矩阵及回退验证记录。

**验收：** 同一插件可在至少两个项目正确启停、隔离及恢复；支持范围只填写实际验证环境，不把 Windows 证据推广为其他 OS/客户端。

## 首次交付与后续扩展

首期交付：独立插件源码仓库、两项 Skill、五个工具、command Hooks、项目接入/回退说明、合成数据测试、原生手动/自动压缩和双项目证据。不打包、不发布。

后续另立任务：自动提炼纳入、全局共享经验、子 Agent 独立生命周期、分布式会话写入、其他客户端/系统、控制记录归档清理。它们不以“多项目通用”为理由提前加入首期。

当前状态：S1—S5 骨架已建立；按用户要求迁至 SLS 同级目录并初始化独立 Git，设计与计划随源码独立维护，当前不打包。T01—T08 的真实功能保持待办。未创建新聊天、未安装/信任/切换插件、未提交或推送；下一步从 T01 的真实接口证据开始，不以 MCP 冒烟代替 Mem0 或原生 Hook 验证。

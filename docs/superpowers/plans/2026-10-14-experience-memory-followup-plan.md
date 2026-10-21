# Experience Memory 后续实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 按任务实施；用户明确选择并行 Agent 时再采用 superpowers:subagent-driven-development。步骤使用 checkbox 跟踪，未获实施指令时只维护本计划。

**Goal:** 在现有独立源码骨架上，完成项目经验读写、有效使用登记及压缩后正式留存区恢复，并验证两个项目之间的数据隔离。

**Architecture:** 延续 `interfaces → application → domain`、`infrastructure → domain`，MCP/Hook 经 ApplicationService，共用 bootstrap。Mem0 保存非核心经验、回执、活动窗口和检查点；本地只保留标识指针、操作待定状态与锁。

**Tech Stack:** Python 3.10+、标准库 urllib/dataclasses/unittest、当前固定 MCP SDK 2.3.0、Mem0 Platform HTTPS API；首期验证 Windows Codex 根会话和单宿主写入。

**Spec:** [已确认设计](../specs/2026-10-12-mem0-compaction-retention-design.md)，第 1—9 节为留存协议，第 11—12 节为能力和结构。本计划是后续实施唯一入口；[旧计划](2026-10-12-experience-memory-plugin-plan.md)保留为历史任务索引。

日期：2026-10-15。状态：用户明确“先完成整体功能再验收G1”，授权继续 T03—T08 功能开发；G1 和 G4 的真实宿主验收延后，不作为本轮编码阻塞条件。功能通过本地应用/SDK与真实合成 API 分别验证，不能据此声明原生可用。

## 执行前基线（历史，当前状态见兼容矩阵）

| 已核验现状 | 对后续的影响 |
| --- | --- |
| 独立 Git 项目 `experience-memory`，分支 `codex/skeleton`，无提交、无远程 | 不代替用户提交/推送/添加远程；无远程身份须配置明确的稳定项目身份 |
| 五个应用用例仍明确报 NOT_IMPLEMENTED，三类 Adapter 未配置 | 不能把骨架检查当成经验或压缩能力 |
| MCP `_invoke` 当前丢弃服务返回值并统一报未实现 | 每项能力接通时同步实现真实结果编码，不能只改服务 |
| 配置加载仅支持显式文件，未实现项目发现/身份/核心文件读取 | T02 补齐；保持现有 `load_config(path)`，不将 cwd 偷换为配置文件 |
| 27 项骨架检查有 2026-10-13 的通过证据 | 本轮只读核验源码和 Git；文档规划不重复产品测试 |
| 用户明确不需要打包 | 源码直接维护；不恢复打包脚本、分发产物或发布流程 |

## Global Constraints

- 只遍历固定核心规则；非核心按任务检索，不枚举全库校验。新独立问题、任务范围改变和压缩后缺少必要依据是检索触发点。
- 标记 `【留存:P1|经验ID|U42】一行摘要`；P1>P2>P3。超预算先踢低等级，再踢同级 U 最旧项，最后按 ID 的 ASCII 升序淘汰；整条处理。
- U 仅由已确认的有效使用分配；纯查询、读取、重复展示、压缩和恢复不更新 U。同一逻辑操作重试复用 operation_id。
- 本扩展完整 additionalContext 上限默认 8192 UTF-8 字节，含固定协议、项目核心、正式区域及包装；核心自身超限失败，不淘汰核心。
- 正式留存区决定有效非核心集合；旧摘要不能复活淘汰/失效内容。再次采用需从 Mem0 重读核验登记。
- pending/unknown 不是保存成功；未知结果先定案，暂时无命中不允许另起写入。Mem0 未证明提供事务/CAS，不承诺跨宿主并发安全。
- 项目经验默认隔离；同仓库 worktree 共享项目经验，会话 U/窗口/检查点分开。不自动迁移或共享旧项目经验。
- 非核心正文、摘要和集合不进入插件本地缓存、配置、日志或指针。宿主原生会话记录不作为本地经验库；其持久行为单独记录，不冒称插件能消除全部宿主记录。
- 真实 Hook 信任/启停须按宿主审阅流程操作；不修改旧插件缓存。实施时先准备可审阅探针，再启用专用测试会话，不能自动切换正常项目。
- 本阶段执行 T03—T08 功能开发，原生 G1/G4 验收置后；API 写入测试限定明确的合成测试范围，不修改真实经验。未经要求不提交、推送、添加远程、打包或发布。

## 阶段与门槛

```text
T01 风险契约探针
  → T02 身份/配置/核心入口/本地运行时
  → T03 真实仓储 + 远端绑定 + 查询读取
  → T04 显式维护闭环                 [G2：读写内核]
  → T05 有效使用/U/活动窗口
  → T06 选择器/检查点/恢复             [G3：留存内核]
  → T07 原生 MCP/Hook/提示词接通
  → T08 双项目与旧插件切换验收         [G4：实际插件]
```

| 门槛 | 必须具备的证据 | 未通过时 |
| --- | --- | --- |
| G1，T01 | Mem0 保真/过滤/结果定案与宿主事件/绑定来源的真实证据矩阵 | 按用户顺序先完成整体功能；原生身份/边界适配保持默认拒绝，最终补 G1 |
| G2，T03—T04 | 合成范围的绑定、检索、精确读取、维护及真实 MCP 返回 | 功能开发可以继续；分别补本地、SDK及真实合成 API 证据，不冒充原生插件可用 |
| G3，T05—T06 | U 幂等、窗口确认、预算与连续两次压缩逻辑测试 | Hook 不发布正式留存正文 |
| G4，T07—T08 | 原生续跑立即可见、无额外正文缓存、双项目隔离及回退 | 不切换正常项目的旧责任方 |

T02 只完成本地身份、核心读取和锁/指针合同；远端绑定在 T03 接通。任何 MCP 用例在远端绑定未确认前拒绝句柄，避免 T02/T03 循环依赖。

## 文件与接口约定

所有路径相对于项目根。既有 `services.py`、`bootstrap.py`、`config.py` 和 `interfaces/` 增量维护，不重建骨架。公共入口保持五工具；Cases 仅在存在可复用完整能力时拆出。

| 新增位置 | 职责与关键合同 |
| --- | --- |
| `infrastructure/scope.py` | `resolve_scope(cwd: Path, project_scope: str | None) -> Scope`，Git 远程规范化或显式稳定身份 |
| `infrastructure/session_runtime.py`、`session_pointer.py` | 可信工作区定位、同项目/会话锁、原子受限指针；只存标识和操作状态 |
| `infrastructure/core_loader.py` | `load_core(project_root: Path, relative_paths: tuple[str, ...]) -> str`，解析真实路径、拒绝外部链接/越界 |
| `infrastructure/mem0_client.py` | 公开 API、请求/总截止时间、认证桥接、脱敏错误；不引用旧插件私有代码 |
| `infrastructure/experience_repository.py`、`control_repository.py` | 实现领域 Port，返回领域记录/保存状态，不引用应用 DTO |
| `application/session_binding.py` | 初始化/校验远端绑定；核对工作区、scope、host 和根会话；不相信调用方自报 session_id |
| `application/registry.py`、`checkpoint.py` | 有效使用及检查点 Cases，由 RetentionApplicationService 调用 |
| `domain/selector.py` | 纯函数选择、排序和字节预算，无网络/SDK |

T03 将唯一 `OperationStatus` 枚举迁入 `domain/ports.py`，`application/contracts.py` 重新导出兼容名称；同处定义 `WriteOutcome(status, operation_id, record_ids, event_id, error_code)`。基础设施只返回该 Port 类型，应用映射为 `OperationResult`，不重复定义状态词表。confirmed 的依据仍是回读，不是 HTTP 200。

补齐目标模型时沿用已有 Scope/SessionContext/ReadReceipt/WindowEntry/ActiveWindow/Checkpoint；MemoryRecord 增加 metadata。应用 DTO 包括 SearchResult、MemoryCandidate、ReadResult(record, receipt)、OperationResult、UseReceipt 与诊断结果。T06 在领域定义 MaterializedEntry(entry, line) 和 RetentionSelection(selected, dropped, rendered_context, payload_bytes)，仅当前进程使用，不本地持久化。MCP 边界转成自有 JSON，不能直接序列化领域对象。

## Review Focus

1. 写成功但响应丢失/搜索暂时无命中：不能重新 add、重复 U 或取旧 ready；测试归 T03/T04/T05/T06。
2. 多工作区共享 MCP 或窃取旧句柄：仅凭知道 handle 不足以授权，必须校验宿主工作区；来源不足则相关功能 unsupported；归 T01/T03/T08。
3. 合法语义结果中夹入控制记录或旧自动摘要：排除必须发生在服务端 top_k 前，缺少有效身份不得升级 active；归 T01/T03。
4. 核心路径经 Windows 根相对路径、junction/symlink 或目录迁移逃逸：语法与真实路径都验证；归 T02。
5. 第二次压缩、缩小预算、原生输出被截断/落盘：恢复集合先确认，淘汰不复活，8 KiB 不当成 token；归 T06/T07/T08。

## Task 1 / T01：验证风险契约

**Files:** Create `tests/contracts/test_platform_contracts.py`、`tests/native/test_hook_contracts.py`、`tests/fixtures/synthetic_records.json`；Modify `docs/compatibility.md`。

**Interfaces:** 产出能力矩阵（supported/unsupported/unverified）、实际 API 路径/字段/事件状态、host/SDK 版本、句柄可信来源与压缩边界关联方案，供 T02/T03/T07 使用。测试探针只用合成哨兵，不能包含真实非核心正文。

- [x] 先编写 `test_infer_false_roundtrip`（合成 JSON 逐字回读）、`test_immutable_control_record`（指定提炼场景后原记录不变）、`test_control_filter_before_top_k`、`test_id_read_preserves_scope` 和异步事件定案检查。真实 API 4/4 通过；异步事件含 RUNNING，探针按实际响应适配。
- [x] 明确独立合成 A/B 范围和记录清单，验证缺凭据时显式 skip/未验证；专用开关才运行真实 API，不能因跳过就记为通过。app 为 experience-memory-contract-test，成功 run 见兼容矩阵。
- [ ] 准备最小 command Hook 探针，验证启动/恢复、手动/自动压缩事件及边界关联、下一次请求可见性、工作区来源；探针配置完成后经审阅/信任才运行。

  探针脚本与 [可审阅配置](../../../examples/native-probe-hooks.json)已准备，离线边界检查通过；原生审阅/信任/运行及可信来源验证尚待完成。
- [ ] 在专用会话验证 additionalContextLimit 与输出截断/落盘；记录插件输出与宿主原生会话记录的区别。无可靠关联或工作区来源时不靠全历史扫描、cwd 猜测或任意句柄绕过。
- [ ] 运行 `python -m unittest tests.contracts.test_platform_contracts -v` 和独立原生用例；写明实测、失败与未验证。**Expected:** 已执行的必需契约通过；未运行项仍为 unverified，不笼统宣称 G1 完成。

## Task 2 / T02：身份、配置、核心与本地运行时

**Files:** Create `infrastructure/scope.py`、`session_runtime.py`、`session_pointer.py`、`core_loader.py`（均在 `src/experience_memory/` 下）；Modify `config.py`、`domain/ports.py`、`bootstrap.py`；Create `tests/unit/test_scope_runtime.py`、`test_core_loader.py`。

**Interfaces:** 保留 `load_config(path: Path | None) -> Config`；增加 `load_project_config(project_root: Path, explicit_path: Path | None) -> Config`，缺显式文件时只发现根 `.experience-memory.json`，不读取任意上级文件。新增 `NamespaceMapping` 配置类型保存显式旧命名空间映射；核心与运行时能力通过领域 Port 注入。

- [x] 编写 SSH/HTTPS 同身份、同名不同 host、无远程必须显式身份、同仓库 worktree 同项目不同会话的失败断言；不为本仓库自动添加远程。
- [x] 编写重复配置键/未知字段、越界/junction/Windows 根相对路径和不存在核心文件的断言；真实路径限定项目根，核心超预算不截断。
- [x] 编写 `test_pointer_contains_no_rule_data`、锁覆盖两进程、原子指针替换中断检查；本地文件只允许控制标识/代次/待处理状态，不含条目集合。
- [x] 运行 `python -m unittest tests.unit.test_scope_runtime tests.unit.test_core_loader -v` 观察未实现断言，再实现最小能力并重跑。**Expected:** 所有已定义边界通过；只产生本地准备状态，不能返回已确认远端绑定。Windows 真路径/junction/双进程实测通过，doctor 本地准备检查已接入。

  独立审阅后补充：指针读写同为 16384 UTF-8 字节上限，超限不覆盖；自动 SSH→HTTPS 归一只限 git@github.com 默认端口，其他 SSH 服务显式配置稳定身份，避免用户相对路径合并。完整离线 56 项，50 通过＋6 未验证 skip；真实过滤对照另行运行通过。

## Task 3 / T03：真实仓储、绑定、检索与精确读取

**Files:** Create `infrastructure/mem0_client.py`、`experience_repository.py`、`control_repository.py`、`application/session_binding.py`；Modify `domain/models.py`、`ports.py`、`application/contracts.py`、`services.py`、`bootstrap.py`、`interfaces/mcp_server.py`、`diagnostics.py`；Create `tests/unit/test_mem0_store.py`、`test_session_binding.py`、`tests/contracts/test_memory_results.py`。

**Interfaces:** 应用 `search(handle, query, limit) -> SearchResult`、`read(handle, record_id) -> ReadResult`、`status(handle) -> DiagnosticReport`；读取回执保存/回读后才可用于 use。绑定初始化走 SessionBinding Case 的 `bind(scope, session_id, host_id) -> SessionContext`，对外经 RetentionApplicationService 的 SessionStart 用例；不新增任意 session_id 的 MCP 授权入口。

- [x] 先测试空结果与认证/网络失败不同、分页精确查询、跨范围拒绝、缺字段旧记录 needs_review、控制记录不占 top_k、凭据/正文不进日志。
- [x] 补 WriteOutcome 与操作定案 Port；测试响应丢失为 unknown、同键重复为 conflict、暂时无命中不能再次提交；每个 HTTP 请求 10 秒，总 Hook 截止 30 秒。
- [x] 接通公开 API 与远端绑定的写入/回读，实现受信工作区来源 Port；真实来源契约待 G1，默认拒绝未验证适配。测试 MCP 多工作区/旧会话句柄拒绝；来源不可信时不暴露“已绑定”成功结果。
- [x] 先写 MCP 成功结果被骨架误报的失败用例，再实现显式 DTO 编码：成功无命中含 succeeded=true；失败不生成空库成功；未知/待定状态保留。doctor 按真实装配报告，不继续静态写“全部未实现”。
- [x] 运行 `python -m unittest tests.unit.test_mem0_store tests.unit.test_session_binding tests.contracts.test_memory_results -v`，观察 RED 后完成最小实现，重跑并执行合成范围精确回读。**Expected:** 查询/读取/status 真正接通，write/use 与压缩恢复仍明确未实现。

## Task 4 / T04：显式维护闭环

**Files:** Modify `domain/models.py`、`ports.py`、`application/contracts.py`、`services.py`、`infrastructure/experience_repository.py`、`interfaces/mcp_server.py`、`skills/memory-intake/SKILL.md`；Create `tests/unit/test_memory_intake.py`。

**Interfaces:** `MemoryCandidate(experience_id, text, metadata, admission_basis, expected_fingerprint)`；`write(handle, action='upsert'|'invalidate', candidate, operation_id) -> OperationResult`。应用完成纳入判断后映射到领域 `ExperienceDraft(experience_id, body, retention_text, status, metadata)`；经验仓储 `write(scope, draft, record_id: str | None, expected_fingerprint: str | None, operation_id) -> WriteOutcome`，record_id=None 仅表示经验重的新增，不能为满足旧 MemoryRecord 合同伪造服务端 ID。invalidate 路径先读真实记录再维护；T03 的同步读取合同不变。

- [x] 先测试同 ID 复用、语义相似需合并判断、来源/旧指纹/跨项目拒绝、失效保留 ID、未证实材料不自动 active；语义无命中不能跳过稳定 ID 精确验重。新增在真实回读前无成功记录 ID，领域已保存记录与待写草稿分开。
- [x] 测试 pending/unknown 未定案时复用原 operation_id 且不另起写入；同宿主同项目写串行，远端指纹竞争报告冲突，不宣称 CAS。
- [x] 实现新增/更新/失效，提交和回读后才 confirmed；同步 MCP write 成功/待定/失败编码。
- [x] 补齐维护 Skill 的原操作 ID 重试约束，做合成采用场景检查；不自动提炼全会话、不硬删除或清空全库。
- [x] 运行 `python -m unittest tests.unit.test_memory_intake tests.contracts.test_memory_results -v`，先 RED 后 GREEN，再合成范围回读确认。**Expected:** G2 功能闭环；真实原生来源与压缩支持仍待 G1。

## Task 5 / T05：有效使用、U 与活动窗口

**Files:** Create `application/registry.py`；Modify `application/services.py`、`contracts.py`、`domain/ports.py`、`infrastructure/control_repository.py`、`interfaces/mcp_server.py`；Create `tests/unit/test_registry.py`。

**Interfaces:** `RetentionApplicationService.use(handle, receipt_id, priority, reason, operation_id) -> UseReceipt`，内含真实 WindowEntry 与 confirmed window_id；非核心集合和 u_cursor 只在 Mem0 保存。

- [x] 先测试跨会话/过期/失效回执、空用途、非法等级拒绝；相同操作重试返回同 U/窗口，查询/读取/恢复不登记。
- [x] 实现首次 confirmed 空窗口、有效采用 U+1、回读后发布指针；未知结果或父版本冲突冻结推进。
- [x] 测试“服务器成功但指针未发布”进程中断、同父窗口竞争和格式示例；恢复时按原操作定案，不盲目追加。
- [x] 接通 MCP use 结果；用途是 agent 声明，回执证明登记完成，不证明 agent 已理解/遵守规则。
- [x] 运行 `python -m unittest tests.unit.test_registry tests.contracts.test_memory_results -v`，先 RED 后 GREEN。**Expected:** 只从真实有效采用扩张窗口；没有本地集合或额外 U。

## Task 6 / T06：选择器、检查点与恢复内核

**Files:** Create `domain/selector.py`、`application/checkpoint.py`；Modify `domain/models.py`、`ports.py`、`application/contracts.py`、`services.py`、`infrastructure/control_repository.py`；Create `tests/unit/test_selector.py`、`test_checkpoint.py`。

**Interfaces:** 纯领域 `select_retention(entries, fixed_context, budget_bytes) -> RetentionSelection`；应用 `prepare(ctx, boundary_id) -> Checkpoint` 与 `restore(ctx, generation) -> HookResult` 由 RetentionApplicationService 调用。只读当前窗口及必要事件，不遍历历史经验。

- [x] 先测试等级、最旧 U、ASCII 平局、同 ID 最新有效版本、中文 UTF-8、单条超限、固定协议/核心/包装计入、核心自身超限；摘要须保持一行，拒绝伪造正式区域/标记的字段，算法不调用模型缩写摘要。
- [x] 测试本次未 ready 不取上次 checkpoint、ready 前必须保存/回读、P1 读取未知/改版停止；P2/P3 未恢复项记录原因。并发读取有界且受总截止时间约束。
- [x] 测试恢复保持 U、预算缩小后重新淘汰、实际恢复集合先提交/回读成活动窗口，提交失败不发布正文。
- [x] 用连续两次压缩场景证明已淘汰项不复活、已失效记录不恢复、进程中断与边界重试不复制逻辑效果。
- [x] 运行 `python -m unittest tests.unit.test_selector tests.unit.test_checkpoint -v`，先 RED 后 GREEN。**Expected:** G3 的内核逻辑通过；仍不计原生 Hook 验证。

## Task 7 / T07：MCP、原生 Hook 与提示词

**Files:** Modify `interfaces/hooks.py`、`mcp_server.py`、`diagnostics.py`、`application/services.py`、`bootstrap.py`、`hooks/hooks.json`、`hooks/adapter.py`、`prompts/*.md`、`skills/memory-query/SKILL.md`；Create `tests/unit/test_codex_hooks.py`、`tests/contracts/test_mcp_tools.py`；扩展 `tests/native/test_hook_contracts.py`。

**Interfaces:** SessionStart(startup/resume/clear) 加载固定核心并绑定；PreCompact 准备本次检查点；SessionStart(compact) 注入已确认正式区域。PostCompact 只关联边界（仅在 T01 确認必须且可用时启用）；PostToolUse 若启用，只核对回执，不重复 use。

- [x] 先测试事件合法字段、边界对应、缺核心/绑定/pending/unknown/P1 失败阻断；MCP/Hook 全部经 ApplicationService，stdout 不混日志或部分恢复内容。
- [x] 接通真实用例，严格计算完整 additionalContext；只有实测配置生效才用 additionalContextLimit=0。PreCompact 不追加提示词，compact-supplement 保持未启用资源，不覆盖 compact_prompt。
- [x] 强化查询 Skill：每个独立问题和范围变化触发检索；核心固定入口先校验，候选精读后实际采用才登记。缺少上下文不等于没有经验；检索仍不承诺全库零漏召回。
- [x] 运行本地 `python -m unittest tests.unit.test_codex_hooks tests.contracts.test_mcp_tools -v`，先 RED 后 GREEN。
- [ ] 再在专用原生会话复验手动/自动/连续两次压缩、立即续跑哨兵、正式集合指纹/U 和输出限制。
- [ ] **Expected:** 宿主实际纳入完整正式留存区，失败时没有下一次带旧规则的模型续跑；没有原生证据时不声明支持。


## Task 8 / T08：双项目、接入与回退

**Files:** Create `tests/native/test_cross_project.py`；Modify `tests/contracts/test_package.py`、`test_workspace.py`、`docs/operations.md`、`docs/compatibility.md`、`README.md`，必要时补实际 Python 启动配置。

**Interfaces:** 源码目录直接接入；维护支持矩阵和按项目的启停/旧命名空间映射/回退记录，不制作安装包或发布 marketplace。

- [x] 先测试 A/B 同 EXP-ID 不串读，同仓库两个 worktree 共享经验但 U/窗口/检查点不同；A 停用不影响 B、旧句柄不能复用；无身份项目拒绝，不转全用户搜索。
- [ ] 检查旧 Mem0 Hook/工具/缓存；切换前准备精确配置差异与回退材料，经项目级授权/宿主信任后切换测试链路。保留云端数据，不全局卸载或删除旧缓存。
- [ ] 检查指针/日志/额外缓存/临时 Hook 输出无非核心正文；单独记录宿主会话记录行为，不把范围较小的检查扩大为“电脑上绝无正文”。
- [x] 只验声明支持的 Python/OS/客户端；3.10 语法检查不能替代 3.10 运行证据。源码换目录和 Windows 启动不依赖作者解释器路径。
- [ ] 执行 `python -m unittest discover -s tests -v` 与独立原生双项目用例；写实际结果和回退验证。**Expected:** G4 成立，至少两个合成项目完整闭环；未经验证平台保持 unverified。

## 当前后续执行入口（2026-10-15 双宿主续接）

用户已选择分阶段支持 Codex 与 Claude Code，并选择逐任务子 Agent 完成 B0。保留本文件作为任务导航，参见 [双宿主设计](../specs/2026-10-15-dual-host-integration-design.md) 和 [运行基线实施计划](2026-10-15-runtime-baseline-plan.md)。B0 已完成环境、离线入口和诊断改进，控制器最终回归111项、104通过、7项未验证跳过。2026-10-16已获两个合成项目最小C1任务授权，用户已正常信任A/B项目及探针Hook。Codex0.163.0下原A观察到MCP元数据、原B产生Hook审计但受额度阻塞失败；用户另行授权一次B重试，待ordinaryUsageAllowed恢复true后retry-1成功，在同一原生会话核验启动Hook与MCP关联，12项断言通过，原失败记录未覆盖。累计尝试3次，追加授权已使用，不自动继续模型任务。源码合并规则已复核，确认原生模型路径与app-server直接调用的元数据来源保证不同；用户选择可信本机Codex及获授权客户端的边界，受控0.163.0严格解析与离线SDK负例已交付，独立审阅无发现，控制器最终回归125项、118通过、7项原有未验证跳过。生产CLI仍默认拒绝，C1整体验收尚未通过，C2未启动；不把workspaces仓库根误称逐请求cwd，也不承诺仅凭字段解析认证任意客户端。当前证据见 [契约探针](../../contract-probes.md#c1-受控身份探针预检2026-10-16)。

执行依赖为：B0 专用环境与离线诊断 → C1/C2 Codex 原生来源及留存验收 → H1 双宿主会话/操作隔离与同机共同锁 → A1 Claude Code 可行性探针 → A2 Claude 适配 → X1 跨宿主与逐项目回退。B0已完成，不重复实施；后续单元在前置证据明确后分别审阅实施计划，不将未知宿主字段编造为API，也不把当前正向传输关联等同完整身份授权。

本节只扩展宿主方向，不扩大 OS、云端或分布式支持。上文 T03—T08 的功能完成状态和 G1/G4 未验收状态不变；未经单独授权不启动原生模型、在线写入或切换正常项目。

2026-10-18续接：用户确认C1接线的离线准备范围。新增固定合成Git根/本轮标记夹具，复用现有身份解析、真实授权服务与生产五工具；只在夹具中增加nonce引导，种子原所属身份有效后再做当前身份拒绝，明确不是历史原生句柄。新增15项测试，首轮完整离线回归140项、133通过、7项未验证跳过。被动执行清单只输出配置预览和摘要，不安装、不执行；当前新模型任务授权为0，提案上限及停止条件不能冒充已实现的执行器。独立规格/质量审阅均Approved，无发现；控制器最终复验140项、133通过、7项跳过，37.630秒，退出0，已有代码和审阅摘要未变。详见 [C1接线审阅说明](../../codex-c1-native-wiring-review.md)。已有生产源码、旧Hook及信任未修改，C1整体/C2状态不升级。

## 2026-10-18 后续设计与计划整理

新增 [后续开发设计与阶段路线](../specs/2026-10-18-development-roadmap-design.md)，统一当前证据、全局约束与C1-E → C1-N → C1-P → C2 → H1 → A1/A2 → X1门槛；最近一期细化为 [C1有界执行器实施计划](2026-10-18-codex-c1-bounded-executor-plan.md)，分为服务前门禁、bundle/安装/尝试账本、只读预检、结果核验及CLI监督5个可独立测试/审阅任务。

两份新文档均为供审阅草案，不覆盖此前已批准协议，不标记编码任务为完成；本轮只整理文档，未重新执行产品测试、安装配置、调用原生宿主或授权新模型任务。140项/133通过/7跳过仅引用上一轮交付。用户审阅后再单独批准实施与执行方式；实际安装、预检和原生分派仍分别确认范围。

## 2026-10-19 C1-E 离线实施交付

用户明确开始执行后，五任务逐项实现/审阅及整轮修复复审均已完成。控制器最终实际回归277项、270通过、7项原有未验证跳过，262.547秒，退出0；108项源码与最终审阅快照一致，生产src/hooks未变。全新被动材料仅预览、无授权/安装/尝试；本轮真实Codex/Mem0调用为0，未提交或推送。

计划中exec原生路径遇到确切源码限制：0.163.0强制Never覆盖请求on-request，不能满足已确认人工审批要求，因此native run在预检/名额/启动前明确unsupported。离线执行器已交付，但原生驱动尚未交付；下一步先单独审阅审批可验证transport，不直接授权两个原生任务或转C2。历史探针的审批表述已收窄为请求配置，原元数据/Hook/失败证据保留。见 [最终交付与裁决](../../verification/codex-c1-executor.md)。

## 2026-10-20 C1-T 设计与离线实施计划

用户确认改用专用app-server通道的设计范围。已只读核对rust-v0.163.0线程配置快照、审批反向请求、canonical v2工具事件及thread/start预热行为，形成 [C1-T设计](../specs/2026-10-20-codex-c1-appserver-transport-design.md) 与 [五任务离线计划](2026-10-20-codex-c1-appserver-transport-plan.md)：双向Job进程、RPC方向/ID/取消、线程/turn策略、v2会话证据、真实本地假宿主/SDK与被动sidecar。

用户于2026-10-20批准本轮离线实施；2026-10-21五任务与整轮修复复审均批准，无剩余发现。控制器最终完整回归520项、513通过、7项未验证跳过，1088.950秒，退出0；123项代码与最终审阅快照一致，39项保护文件未改。新基础bundle和app-server sidecar只为预览，两层verify通过、未安装、零原生授权；没有真实Codex（含版本/RPC/线程/turn）或Mem0操作，不复用旧原生授权。旧exec仍unsupported、新入口C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING，不能直接开始原生模型任务。下一步单独审阅受控原生诊断入口与生命周期验收，线程创建可能预热外部连接，不视作纯本地只读操作。详见 [C1-T最终交付及裁决](../../verification/codex-c1-appserver-transport.md)。

## 2026-10-21 本项目双宿主自用

用户要求让本项目在自身开发中实际使用经验能力，已选择两端分阶段接入，并确认“先 Codex 本仓库自用，再 Claude 接入，最后双端闭环”的设计。新增 [自用设计](../specs/2026-10-21-project-self-use-design.md) 与 [S0 接入准备计划](2026-10-21-project-self-use-readiness-plan.md)。目标是实际任务检索/精读/采用、确认写入与后续会话再次使用，并独立验收压缩留存；不是增加开发指导文件就算产品接入。

2026-10-21 用户以“开始执行”批准S0当前会话顺序实施，交付disabled配置预览、精简固定核心、本地合同与[自用/责任/回退手册](../../self-use/README.md)，新增8项合同通过，独立只读审阅Spec/Quality均Approved无发现。首次全套出现既有C1耗时断言失败后，用户追加授权离线定位和必要修复：仅修正一个测试方法的计时边界与自然退出夹具窗口，保留原清理/PID/EOF/账本/9秒进程预算断言，生产src/scripts/fixture未改。定向/受控验证与独立审阅批准后，最终全套实际528项、521通过、7项未验证跳过，1910.579秒，退出0；131项冻结代码/资源摘要无变化。S0离线接入准备完成，首次失败与统计控制器CRLF计数校正均保留，见[本轮验证](../../verification/project-self-use-readiness.md)及[修复报告](../../verification/c1-appserver-timing-fix.md)。没有真实Codex/Mem0、实际安装、根自动发现配置或项目切换。下一步恢复为受控原生诊断入口/生命周期验收设计及单独授权，仍逐门槛经过C1/C2/H1/A1/A2/X1；旧exec unsupported、新app-server原生pending不变，本仓库尚未产品自用。

## 原执行顺序（整体功能开发阶段）

2026-10-15 用户明确先完成整体功能再验收 G1。T03—T08 的仓储、读写、U、留存、Hook/MCP 接线和双项目测试先实施；正常会话的安装/信任、可信宿主来源、原生手动/自动/连续压缩及立即续跑、旧插件责任方切换在最终验收阶段执行。

关键边界：默认 MCP 不接受未验证 metadata 契约；本地 SDK 测试通过注入的夹具身份驱动完整服务。Hook 的本次压缩边界只接受显式 compaction_id/turn_id，缺失或不匹配就拒绝，不能回退读取上次 ready。真实宿主是否提供可用来源/一致边界由 G1 定案。

后续暂不纳入：自动提炼纳入、全局共享、子 Agent 独立生命周期、分布式写入、云端宿主、其他系统、控制记录归档。发现协议限制时更新唯一设计，不用新增本地规则库绕过。

本轮自查：八任务覆盖设计；绑定依赖在 T03 闭合；MCP 成功编码和诊断随功能启用；状态类型唯一；所有风险均有任务级断言；原生/在线 skip 不冒充通过；没有提交/推送/打包步骤。

依据（2026-10-14 已核对官方说明，仍需 T01 实测）：[Codex Hooks](https://learn.chatgpt.com/docs/hooks)、[Mem0 Add](https://docs.mem0.ai/api-reference/memory/add-memories)、[Mem0 Search](https://docs.mem0.ai/api-reference/memory/search-memories)。

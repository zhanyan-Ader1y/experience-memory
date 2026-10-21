# 功能与证据边界

2026-10-15：用户指定先完成整体功能，再验收 G1。源码功能已接通，原生能力仍未验收。

| 能力 | 当前证据 | 状态 |
| --- | --- | --- |
| infer=false 原样回读、immutable 合成场景、控制记录排除 | T01 真实 API 4项通过；过滤 top3 对照额外1项通过 | verified，仅实测输入与账号 |
| 应用 Mem0 仓储、远端会话控制、读写/更新/失效 | 随机双合成项目真实 API 闭环 | implemented + API verified |
| 标签、来源、稳定 ID | DTO、持久 Mem0 metadata 与回读 | implemented；不承诺标签提高语义排名 |
| 五 MCP 工具 | 本地官方 SDK 2.3.0 成功 DTO、错误与缺身份拒绝；stdio初始化 | implemented；真实 Codex 适配待 G1 |
| U、有效采用、当前活动窗口 | 有效回执/过期/跨会话/失效、重试、旧结果不回滚新 U | locally verified |
| 选择器/检查点/恢复/resume | UTF-8完整预算、排序、整条淘汰、连续两次、P1阻断/P2-P3降级、发布中断 | locally verified；API合成闭环通过 |
| Git根/配置/核心可信路径/指针/锁 | Windows realpath/junction/双进程测试；配置不跨根复用 | locally verified，单宿主 |
| A/B项目相同ID、同项目不同会话、无本地经验缓存 | 本地适配集成、真实 A/B API；指针仅 ID/状态/哈希 | verified at stated layers |
| 宿主 MCP 每请求可信来源 | 0.163.0真实stdio已观察sessionId/threadId/workspaces；B重试同会话Hook→MCP关联12项断言通过 | partial native observed；生产适配、防覆盖和跨范围拒绝仍待G1 |
| 原生压缩边界与下一次模型请求、落盘/截断 | 合成探针及审阅材料已准备 | unverified，G1 |
| 旧 Mem0 唯一责任方切换、真实双项目与回退 | 准备源码接入与回退说明，未改正常项目配置 | pending G1/G4 |

## 实测记录

- T01 API run `f83fa44da4fc46648b716a91668abcdf`：4/4通过；加强过滤对照 run `d75ab8442c244268aa18c28d6d36cf6d`：1/1通过。
- 功能 API run `b92b8cbbdfba403d99bd50c123aebf0e`：精确查询在首次写入前 HTTP400；多个 metadata 条件须分成独立 AND 项，已用失败回归修正。
- 功能 API run `c06fcb2bee7a44edab45b99a52371c58`：两个合成范围完成绑定、创建/检索/精读、采用、检查点/恢复、更新/失效及跨范围拒绝。
- 带 tags/sources metadata 的 run `43dcb952824d419b9636263be80dbaec`：完整合成 API 闭环通过（129.514秒，最终审阅修复前）。
- 审阅后离线回归：102项，95通过、7项在线/原生未验证 skip；合成 run `5257ae3c9cbb49e187484c71557e3c32` 收到绑定 OPERATION_PENDING，测试夹具原先未续接，不能计为通过。修正夹具后复验结果见 [交付验证](verification/full-function.md)。

所有真实写入测试限定 app=`experience-memory-contract-test`、随机 agent=`contract-<run>-a/b`，不读写生产经验、不自动删除测试数据。infer=false 直接确认仍需精确回读；异步 PENDING/RUNNING/SUCCEEDED/FAILED 单独定案。未知结果只核对原操作，不重新 add。没有事务/CAS或跨宿主安全声明。

公开路径：POST `/v3/memories/add/`、POST `/v3/memories/search/`、POST `/v3/memories/?page=...&page_size=...`、GET/PUT `/v1/memories/{id}/`、GET `/v1/event/{id}/`。metadata 多条件形状是 AND 中多个 `{metadata: {单键: 值}}`；点路径和嵌套多键均不沿用。

本机 Codex CLI/Desktop 0.160.1：只读 config/read 证实专用目录未受信任时项目 Hook 层停用，未据此启动模型或更改信任。MCP 的实际来源契约、压缩事件字段关联、立即续跑与默认输出限制是否导致额外落盘，均留到 G1。缺必要身份/边界时明确拒绝，不从历史扫描、启动 cwd 或任意 handle 推断。

实测 Windows/Python 3.12.14/MCP 2.3.0；Python3.10仅声明语法范围，其他平台、管理员并发篡改目录和断电耐久未验证。源目录移动/空格路径检查不等于其他宿主平台支持。

依据：[Mem0列举](https://docs.mem0.ai/api-reference/memory/get-memories)、[新增](https://docs.mem0.ai/api-reference/memory/add-memories)、[Hooks](https://learn.chatgpt.com/docs/hooks)。运行步骤及原生验收详见 [operations](operations.md)、[contract probes](contract-probes.md)。

审阅修复后真实 API run `43214a77843f4b989fd5a118da02a394`：1/1完整双合成范围闭环通过（235.328秒），已覆盖原操作异步续接；仍不构成 G1 原生来源/注入证据。最终交付记录见 [整体功能验证](verification/full-function.md)。

源码交付后在独立目标目录完整回归102项：95通过、7项明确跳过（12.848秒）。CLI测试使用隔离Git根，覆盖缺身份拒绝和显式身份下未绑定拒绝。

## B0 运行基线复验（2026-10-15，源码直接测试）

本节为运行基线更新后的新增实测记录，不替换上文历史 102 项与 API run 证据。

- 解释器/SDK：Windows/Python 3.13.1/MCP 2.3.0（`.venv/Scripts/python.exe`，显式专用解释器，非全局 Python）。全局 `py -3.13` 同为 Python 3.13.1 但 MCP 1.28.1，未用于测试，仅用于验证 doctor 失败路径。
- 离线合同全套：`.venv/Scripts/python.exe scripts/test.py` → **111 项，104 通过、7 项明确跳过**（11.655 秒，墙钟约 12.6 秒），退出码 0。跳过理由与历史基线完全相同，本次复验未更改任何跳过条件：1 项 live functional（`live functional API opt-in absent`）、4 项 real Mem0 contracts（`real Mem0 contracts not enabled`）、2 项 native host evidence（`native host evidence not supplied`）。
- doctor 结果（两类检查独立记账）：正确解释器下 `local_checks_passed: true`、`runtime_checks_passed: true`，退出码 0；全局错误 SDK 下 `runtime_environment.mcp_compatible: false`、`runtime_checks_passed: false`、退出码 1，但 `local_checks_passed` 仍为 true，且输出结构化 JSON 事实而非堆栈跟踪。
- `mem0_authentication` 仍为 not_checked，`native_compaction` 仍为 not_verified，五工具能力仍显示 implemented_host_contract_unverified；本次复验未将任何 Codex/Claude 能力升级为 verified。
- 插件清单描述已更新为「项目经验读写、有效采用与压缩留存；原生宿主身份和恢复能力仍待验收」，反映真实存储/恢复在 API 层已接通（见上文 API run 证据）、原生宿主身份与恢复仍待 G1 验收的事实；旧描述「真实存储和恢复尚未接入」已不准确。描述变更后清单 JSON 解析与 hook/stdio 接线合同测试（`test_portable_manifest_resolves_hook_and_stdio_entry`）通过，extensions/Hook 路径/MCP 启动方式未变更。
- 限制：本节两组新测试（源码测试入口、运行时环境元数据）均为离线合同与环境检查证据，不证明原生安装、宿主信任启用或真实 Mem0 写入。Python 3.10 仅为声明语法下限，本基线仅实测 3.13.1。
- 最终修复波后复验（2026-10-15）：`.venv/Scripts/python.exe scripts/test.py` → 111 项，104 通过、7 项明确跳过（11.524 秒），退出码 0，跳过条件未变。`test_source_runner` 失败夹具经 RED→GREEN 后在所有路径恢复 sys.path 与导入的夹具模块；全套通过，无新增生产行为。

## C1 原生传输关联证据（2026-10-16）

Windows/Python3.13.1、Codex0.163.0、当前模型gpt-6.1-sol：原A任务观察MCP元数据，原B因额度阻塞在MCP前失败但产生Hook审计；用户追加授权的B retry-1在配额恢复后完成，新增一条启动Hook和一次MCP调用。12项关联断言通过，确认同一原生线程、工作区、审计时序及768字节合成载荷哈希，原失败文件保持不变。该次原生探针结束时生产代码/测试与B0快照哈希一致；之后新增的受控解析增量单独记在下一节。

这是原生启动事件到MCP传输的正向证据，不是完整身份授权、实际模型请求载荷送达或压缩恢复验收。原生字段防覆盖、跨项目旧句柄拒绝和C2仍未完整验收；受控解析及SDK负例的本地证据见下一节，现有provisional生产适配保持关闭。未运行真实Mem0写入、未切换正常项目；总计3次原生任务尝试，追加授权已用完。详细经过及证据边界见 [契约探针](contract-probes.md#项目b授权重试同会话关联核验完成)。

## 受控身份解析与SDK合同（2026-10-16）

用户确认的安全边界为可信本机Codex及获授权app-server客户端，不宣称通过解析识别任意恶意客户端。新增固定0.163.0元数据解析路径，要求根层/嵌套身份一致及唯一规范化仓库根匹配；workspaces不是逐请求动态cwd，缺失/歧义时明确拒绝。

新增13项单元测试、1项真实MCP SDK stdio多场景合同，使用内存后端验证缺字段/冲突、跨根、跨会话/跨项目旧句柄、跨会话回执重用以及工具参数身份伪造的拒绝，并核验异常后上下文清理。独立规格与质量审阅均Approved，无缺陷发现；控制器最终离线回归125项、118通过、7项原有未验证跳过，退出0。详细结果见 [交付记录](contract-probes.md#受控身份解析与离线sdk负例交付)。

本节仅增加本地受控合同证据。生产__main__.py及旧request_host默认拒绝接线未改变；没有新增模型任务、真实Mem0写入、全局配置修改或Git提交。不能把本地SDK测试标为新的原生负例验收，C1整体与C2仍未通过。

## C1 固定根夹具与执行审阅材料（2026-10-18）

新增测试专用固定合成Git根夹具，通过现有解析器/绑定/应用服务和生产五工具接线，加fixture-only nonce引导。15项新增单元/真实SDK合同/被动导出器测试覆盖当前身份成功、有效种子及其他有效会话句柄的拒绝、meta/根/参数伪造、上下文清理、U与ID-only审计、材料不可覆盖及变化拒绝。首轮完整回归140项、133通过、7项原有未验证跳过，退出0；最终结果记在 [契约探针](contract-probes.md#c1-原生接线的离线准备2026-10-18)。

种子句柄为合成绑定，不是此前原生会话句柄。配置只生成预览，当前无新增原生任务授权、无配置安装或宿主版本读取；原生执行器与真实拒绝证据仍待后续。生产源码与旧Hook/信任未改，未调用真实Mem0。不能把被动清单的提案上限称为已实现的运行时限制。详见 [接线审阅说明](codex-c1-native-wiring-review.md)。

独立审阅规格/质量均Approved，无发现；控制器最终完整复验140项、133通过、7项原有未验证跳过（37.630秒），退出0。已有85个代码/资源及6个新文件审阅摘要未变，被动清单校验为review_unchanged，仍无原生授权/安装。

## C1-E 有界离线监督与原生审批缺口（2026-10-19）

新增 Windows 挂起创建→Job 所有权→恢复、父/后代退出确认、有界双流、薄 CLI 和真实 Python 假宿主/真实 MCP SDK 五步验证。模拟仍明确 `offline_simulation`、`native_evidence_claimed=false`；生产 `src/`、旧 Hook、宿主信任/账户/额度配置不变。原有七项未验证 skip 不升级为通过。最终整轮修复复审规格/质量均Approved，无剩余发现；控制器最终完整回归277项、270通过、7项跳过，262.547秒，退出0。108项代码与审阅快照一致，生产src/hooks未变；新被动材料未安装、零原生授权。详见 [交付验证](verification/codex-c1-executor.md)。

固定0.163.0源码明确 exec harness 的 Never override 优先于请求的 on-request 配置，只有不适用于本任务的 AutoReview 例外。故原生分派在名额/启动前代码阻断；没有实际调用 Codex（包括版本/预检），未创建或使用真实原生审批。历史探针的审批表述只证明请求配置，不能证明 exec 有效人工 on-request；元数据/Hook 结果和失败记录不覆盖。详见 [当前执行审阅与源码依据](codex-c1-native-wiring-review.md#原生分派的明确阻塞01630-exec-不保留人工-on-request)。这不是原生执行就绪或 C1/C2 通过。

<a id="c1-t-离线候选2026-10-21待独立审阅"></a>

## C1-T 离线通道交付（2026-10-21）

专用 app-server 候选在本地 Python 假宿主上组合真实 Windows Job 双向 I/O、同连接 synthetic 预检、预热前持久 reservation、实际返回策略/单 turn、原始 v2 消息观察、真实 MCP SDK bounded 五步与实际 5+5 审计。新被动 sidecar 绑定实际 schema2 基础字节及同一源码映射，旧 executor/schema2 语义不改。五任务与整轮修复复审均Approved，无剩余发现；控制器最终完整回归520项、513通过、7项未验证跳过，1088.950秒，退出0。123项代码与最终审阅快照一致，39项受保护文件未变。最新预览未安装、无原生授权，详见 [C1-T交付验证](verification/codex-c1-appserver-transport.md)。

证据分四层：固定版本源码/协议核对；普通 SDK 夹具；真实本地假 app-server 的完整候选模拟；真实原生 Codex。前三层不升级第四层，也不升级 C1整体、C2、生产身份入口、Hook、Mem0 或任意客户端认证。运行 origin 固定 offline_simulation、native_evidence_claimed=false，公开门槛为 `C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING`。无 native run CLI 或实际宿主（含版本、只读 RPC、线程）调用；已有七项原生/在线未验证门禁仍不作为通过。操作与材料边界见 [C1-T说明](codex-c1-native-wiring-review.md#c1-t-离线候选2026-10-21待独立审阅)。

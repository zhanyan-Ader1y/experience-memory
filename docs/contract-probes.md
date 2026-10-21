# T01 契约探针运行与剩余验证

## Mem0：仅合成范围

从项目根运行，解释器需已安装项目依赖：

```powershell
$env:EXPERIENCE_MEMORY_LIVE_CONTRACTS = '1'
python -m unittest tests.contracts.test_platform_contracts -v
```

凭据从 MEM0_API_KEY 或 Windows 用户环境中的同名值读取，不打印。探针固定 app_id=`experience-memory-contract-test`；每次生成新的随机 run ID，A/B 的 agent_id 为 `contract-<run>-a/b`，不读取、修改或删除生产经验。不自动扩大到其他 app、用户或 agent。测试输出仅状态与合成 run ID。

每轮创建合成控制、范围及经验记录，并触发一条合成提炼输入；后续回读 immutable 原记录。成功的结果只能证明该输入场景及账号行为，不能证明所有未来归纳绝不修改。测试记录保留在上述合成范围，不自动清空其他数据。未知响应不重发同一逻辑写入。

未设置开关或缺凭据时显式 skip/unverified。API 执行失败以测试错误返回；禁止以 skip 替代失败。探针是测试辅助代码；T03 应用仓储已实现，验证层次见 compatibility.md。

## 原生 Hook：配置已准备，尚未安装/信任

可审阅配置：[合成探针](../examples/native-probe-hooks.json)，入口：[native_hook_probe.py](../scripts/native_hook_probe.py)。配置只输出合成哨兵；完整上下文严格不超过 8192 UTF-8 字节，additionalContextLimit=0。PreCompact/PostCompact 仅输出公共控制 JSON，SessionStart 输出合成 additionalContext；原生日志只保存事件/ID/字节数/哈希，不复制 transcript、prompt、正文或完整载荷。

每次调用生成随机 invocation_id（32 位小写十六进制），写入哨兵首行与同名审计文件；nonce 只标识一轮验收。同一 nonce/source 的连续压缩也必须产生不同载荷哈希。审计记录不包含完整哨兵，不能单凭审计记录证明模型收到载荷。

此文件不是生产 hooks.json，正常会话不自动安装。运行前在专用测试会话确认目标 Python、PLUGIN_ROOT/PLUGIN_DATA、实际展开的命令及定义哈希，再按宿主的 hook 审阅/信任流程启用。不改信任记录或借用旧插件已信任的命令。原生加载、信任与下一次模型请求的证明不能由手写 stdin 或字段存在替代。

原生验收顺序：

1. SessionStart(startup/resume) 观察实际 session_id、cwd 哈希与进程 cwd 哈希，核对所在项目。
2. 手动压缩：关联 PreCompact/PostCompact/SessionStart(compact) 的根会话及同一边界，不假设 SessionStart 必带 turn_id。
3. 检查紧接着的实际模型请求包含本次 invocation_id，完整哨兵哈希与本次审计对应，未落到 hook_outputs 预览；模型回答复述哨兵或审计存在不能代替请求取证。另行记录宿主正常会话持久化行为。
4. 自动压缩重复同样验证；再在同一根会话完成独立的连续两次手动压缩，保留边界顺序证据，不用上次成功顶替本次。
5. 独立测试 MCP 实际宿主请求的工作区来源，以及在 B 提交 A 句柄被拒绝。远端句柄已实现；本地夹具不能据此宣布原生可信绑定支持。

把经审阅的实际证据整理为一个 JSON bundle（仅证据元数据，存 ignored `local-evidence/`），字段如下；不能自行填 true 代替取证。

| 部位 | 必需内容 |
| --- | --- |
| 顶层 | source=`reviewed_native_host_capture`、host_version、trusted_hook_definition_sha256（64 位小写十六进制）、run_nonce、mcp_workspace_source_verified、cross_workspace_handle_rejected |
| cases.manual / cases.auto | precompact、compact_start、boundary_link_verified=true、workspace_source_verified=true、hook_output_spilled=false、immediate_model_request |
| consecutive | 独立的两次连续手动压缩 case，结构同上，同一 session_id/cwd；不能直接复制 manual case |
| precompact / compact_start | 探针实际元数据，含 invocation_id、nonce=run_nonce、session_id、cwd_sha256；compact_start 含 payload_bytes 与 payload_sha256 |
| immediate_model_request | 从紧接着的真实请求核对 invocation_id 与 sentinel_sha256，均须对应本次 compact_start |

四个 case 的事件 ID、恢复载荷哈希必须互不重复；布尔值严格为 JSON 布尔类型。校验器能拒绝缺少连续场景、旧事件/哈希重用、跨轮 nonce、请求对应错误和预算超限，但不能验证材料真实性、执行先后或“立即”语义，这些仍需审阅原生实录。

```powershell
$env:EXPERIENCE_MEMORY_NATIVE_EVIDENCE = '<reviewed-bundle.json>'
python -m unittest tests.native.test_hook_contracts -v
```

该检查验证证据结构与一致性，证据真伪仍需人工/宿主实录核验；不是原生事件自动运行器。未提供 bundle 时仅 skip/unverified。

## T02 本地接入

项目根 `.experience-memory.json` 被自动发现；显式 --config 仍可指定其他配置文件。只发现项目根，不查任意上级目录。无 Git 远程时必须设置稳定 `project_scope`，例如 `local:experience-memory`；移动检出目录后保持此值。

新版范围使用 canonical project_key 的 SHA-256 命名空间，默认不读取旧 Mem0 范围；需要旧数据时显式 namespace_mapping={app_id, project_id, legacy_project_ids}。不能从同名项目或目录名猜映射，也不迁移/复制旧记录。

SSH 自动归一只支持已明确的 `git@github.com:owner/repo.git`／`ssh://git@github.com/owner/repo.git`（默认端口），与 GitHub HTTPS 同身份；其他 SSH/SCP 远程要求显式 project_scope，避免不同用户 home-relative 路径被合并。HTTPS 自定义服务器身份仍可显式提供。依据：[GitHub 远程格式](https://docs.github.com/en/get-started/git-basics/about-remote-repositories)。

本地指针读写统一使用 16384 UTF-8 字节上限；序列化超限在替换前拒绝，旧指针保持可读。锁覆盖单宿主，同会话与同项目锁是不同键；不声称断电耐久或抵御管理员并发替换可信目录。

```text
python scripts/launch.py doctor --project-root <project-directory>
```

doctor 报告本地 prepared、核心字节数及 remote_binding_confirmed=false，不输出核心正文，也不保存指针。LocalSessionRuntime.prepare 仅保留本地准备用途；远端绑定由 SessionBinding 用例提供，旧任意 session_id 入口不授予权限。T03—T08 已接通功能，完整恢复载荷严格计算 8 KiB；可信来源/边界及实际宿主注入仍待 G1。

## 2026-10-15 原生预检结果

Codex Desktop/CLI 0.160.1 的只读 hooks/list、config/read 已执行：插件源码目录未加载合成探针；专用合成 Git 目录的项目配置层被宿主停用，disabledReason 明确要求先将该目录标记为受信任项目。未启动模型、修改信任状态或绕过审阅。

独立 invocation_id 与证据一致性校验已通过本地回归；这只证明探针和校验器行为。下一步在专用目录用正常 CLI 完成工作区信任，再用 `/hooks` 审阅并信任这三个合成 Hook。配置内容及脚本有变更时重新核验，Hook 定义哈希不能代替脚本内容审阅。之后收集手动、自动、连续两次及紧接模型请求证据。远端句柄已接通，MCP 原生可信来源仍待实测，G1 保持未验证。

依据：[宿主 Hook 信任要求](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)。专用目录和机器解释器路径只是本机 ignored 验收夹具，不进入可移植插件配置。

## 功能开发与原生验收顺序

用户指定先完成功能。用 EXPERIENCE_MEMORY_LIVE_FUNCTIONAL=1 执行 tests.contracts.test_functional_platform 验证真实合成 API 应用闭环；使用本地 test_mcp_tools/test_codex_hooks 验证接线。二者均不能补齐原生 G1。最终再完成本页的信任、事件/来源、完整请求及双项目切换验收。

真实功能联调可能返回异步状态：测试对 pending/unknown 在每项120秒内以相同参数、原 operation_id 续接，只确认原请求，不新建逻辑写入；失败、冲突、认证错误立即失败，超时仍计失败而非 skip。

## C1 受控身份探针预检（2026-10-16）

用户授权两个专用合成项目各一个最小身份任务，启动前审阅配置，项目/Hook信任由用户走正常流程；C2手动/自动压缩、在线Mem0写入及正常项目接入不在本次授权内。

- 准备时Codex CLI为0.162.1，后续已升级为0.163.0，不能把先前0.160.1或0.162.1的静态核验当作新版本原生证据。两版本机实验性app-server schema分别导出并只读核对；app-server的threadId本身不证明它被转发为外部MCP请求的可信身份。
- 官方当前文档为PreCompact/PostCompact列出turn_id，但未保证SessionStart(compact)携带同一边界，也未证明一turn多次压缩可唯一关联；不因字段存在就启用生产恢复。
- 首次config/read与hooks/list发现全局旧Mem0插件及8个插件Hook启用。探针启动使用进程级覆盖禁用插件、内置记忆、无关MCP及非必要工具，不改全局配置；再次检查两个合成根时旧插件Hook已不在列表，只剩5个原本停用的用户Hook，未执行它们。
- 合成根在ignored `.superpowers/codex-c1-20261016/projects/a`、`projects/b`，各有独立Git根及项目配置。一次性stdlib stdio观察器不连接Mem0、不授权生产句柄，仅记录请求字段结构及元数据值的哈希；原始元数据值不写入审计。夹具协议、拒绝错误nonce、元数据脱敏及TOML解析检查通过，只计离线准备。
- 首次预检时两个项目配置层均被宿主按未受信项目停用。此后用户完成登录，并通过正常流程信任项目A及其SessionStart探针Hook，0.163.0的hooks/list已确认trusted/enabled；项目B仍未信任。没有代填凭据或代写信任记录。
- 启动前文件字节哈希保护曾因Windows CRLF与写入前LF摘要不一致而拒绝启动；已核验配置文本未变化，修正生成端按落盘字节计算，临时生成回归及两项目校验通过。启动端未放宽校验，Hook定义仍需正常审阅。
- C1进展见下方首次真实传输观察。没有新增生产源码、没有提交/推送；探针程序和schema均为本轮ignored诊断材料，不构成可安装宿主适配。

临时启动清单记录run nonce、专用路径和脚本/配置哈希；不复制凭据、旧插件经验或完整transcript。后续根据真实外部MCP请求和同会话Hook元数据核对来源，缺证据仍保持HOST_SOURCE_UNVERIFIED，不能仅凭模型回答或合成审计宣布C1通过。

### 首次真实传输观察（Codex 0.163.0，项目A）

完成一个已授权的最小合成任务，使用当前默认模型、只读沙箱、正常审批及临时功能隔离。`codex exec --ephemeral --json`退出0，原生事件显示一次identity_probe调用完成，外部stdio观察器写入一条审计；没有重试任务，也未启动项目B或C2压缩。

实际请求的 `params._meta` 含 `sessionId`、`threadId`、`callId`、`itemId`、`windowId`、`progressToken` 和 `x-codex-turn-metadata`。嵌套信息含 `session_id`、`thread_id`、`turn_id`、`codex_version`、`workspaces` 等。本轮仅保留字段结构与值哈希，不记录原始元数据值或完整模型请求。

通过规范JSON值的SHA-256核对，sessionId/threadId与本次CLI原生thread.started的线程ID一致，codex_version与0.163.0一致，唯一工作区对应项目A；观察器进程cwd也匹配该项目。工具参数仅有合成nonce，未由调用方填入项目或会话身份。这证明该版本及该次原生调用确实传输了这些字段，不再只是app-server schema推测。

边界仍未闭合：此次exec运行没有产生SessionStart Hook审计（数量0），而同配置的hooks功能开关为true、项目定义为trusted/enabled。此处是首次观察时的状态；后续的版本源码和原生环境信息检查已明确事件时机与Windows命令缺陷，见下节。不同会话记录不得拼接为同会话证据。

当前C1仅为部分原生证据：项目B未验证、跨会话/跨项目句柄拒绝未验证、工作区集合与逐请求cwd的保证仍须定案，生产身份适配继续关闭。观察器始终输出authenticated_host_identity=false，不授权真实记忆。项目A任务不重跑；项目B仍需用户正常信任流程，C2仍需单独授权。

### SessionStart时机与Windows命令检查（Codex 0.163.0）

- 版本匹配的源码显示，会话创建先将SessionStart source放入待处理队列，首个turn开始时才消费；因此只执行 `/clear` 或只调用 `thread/start`，没有首个turn时不能要求审计立即出现。先前让用户只clear再检查文件的流程不充分，现已停止重复该操作。
- 只发送initialize、thread/start、environment/status/info的原生API检查没有发送turn/start。local环境返回 `shell.name=powershell`、实际pwsh路径；这些只计环境/生命周期元数据观察，不计新的模型任务。
- `core/src/session/mod.rs` 的build_hooks_config会采用本地环境Shell；不能只看底层command_runner默认COMSPEC/cmd分支，就认定本机会使用cmd。Windows默认cmd包装离线检查可以通过，但实际PowerShell下，原有带引号的Python可执行路径后直接接-I会报ParserError。
- 同一脚本以直接参数向量执行通过；项目B原定义在实际PowerShell下先失败（退出1、无合成审计），仅添加PowerShell的 `&` 调用符作为commandWindows后通过（退出0、一条临时合成审计、768字节上下文）。这是离线命令兼容性回归，不冒充原生Hook验证。
- 仅修改尚未受信任的项目B定义及其启动清单哈希；项目A定义、既有原生证据和任务计数保留。没有代写信任记录、没有信任绕过旗标、没有新增模型任务。项目B终端目前停在正常Folder access信任确认，需用户确认专用根及单个项目Hook后，才可使用其剩余的一个已授权最小任务做最终核验。

源码依据均固定在 `rust-v0.163.0`：[首turn执行队列](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/session/turn.rs#L321-L335)、[环境Shell进入Hook配置](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/session/mod.rs#L5439-L5470)、[命令Shell执行分支](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/hooks/src/engine/command_runner.rs#L384-L408)。源码只读取不执行，下载材料隔离在ignored诊断目录。

### 项目B实际执行与额度阻塞（Codex 0.163.0）

用户通过正常流程信任项目B及修正后的Hook，启动前宿主状态为trusted/enabled，文件哈希和隔离检查通过。随后仅发起项目B已授权的一次最小任务：原生事件为thread.started、turn.started、error、turn.failed，进程退出1，MCP调用数0，Hook审计数1；没有自动重试。

真实SessionStart审计的source为startup，session_id与该次原生thread.started ID逐字一致，cwd_sha256与进程cwd_sha256相同，载荷字节数768。它证明修正后的Windows命令在本次原生会话中确实执行，不再仅是离线兼容性结果；但不证明上下文送达模型，也不能与项目A的另一会话MCP调用拼接为完整链路。

只读account/rateLimits/read独立返回ordinaryUsageAllowed=false、rateLimitReachedType=rate_limit_reached，5小时窗口usedPercent=100，周窗口63。未启用Reserve fallback、兑换额度或切换账户/模型；此次配额读取未分派模型任务。原turn错误内容按既定最小日志策略只留哈希，不能还原其确切错误文本，因此不把独立额度阻塞信息伪称为原错误逐字诊断。

原授权的两次最小任务尝试均已发起。后续暂停原生模型执行，不删除失败记录、不覆盖原始审计、不绕过信任或额度限制。额度恢复时仍需重新读取ordinaryUsageAllowed，不能仅凭reset时间推断恢复；追加项目B诊断重试须单独授权，并保留新的尝试记录。C1、同会话Hook→MCP闭环、跨项目句柄拒绝及C2压缩仍未通过，生产身份适配继续关闭。

自动生成第二版JSON汇总的Bash命令另因本地执行判定服务临时限流而未运行；没有改用其他执行通道绕过。原始a-native-run、b-native-run、B的Hook审计及只读配额快照均已保留，不能声称新汇总文件已生成。

用户随后明确要求重试，授权追加一次项目B诊断尝试。首次重新读取配额的Bash调用仍被Claude Code自动判定服务限流拦下，当时未消耗该授权；后续执行服务与ordinaryUsageAllowed均恢复后，已执行下述唯一一次retry-1。此前旧额度快照、原失败记录均保留，不代表当前状态，也不覆盖新结果。

### 项目B授权重试：同会话关联核验完成

执行前重新读取account/rateLimits/read，ordinaryUsageAllowed=true、5小时窗口usedPercent=0；并复核CLI0.163.0、项目/Hook信任、源文件字节哈希、唯一合成MCP服务及旧记忆入口隔离。仅执行用户追加授权的一次 `retry-1`，独立写入b-native-retry-1.json，未覆盖b-native-run.json，也未自动发起后续任务。

该次原生turn完成、进程退出0、无错误；只调用identity_probe一次。本次新增一条SessionStart审计和一条MCP审计，明确按新增文件ID筛选，未把第一次失败留下的Hook记录计入本次成功。

12项实际断言全部通过：turn完成；Hook session_id对应原生线程；MCP sessionId和threadId各自对应同一线程；嵌套session_id/thread_id一致；唯一工作区对应项目B；Hook与MCP进程cwd哈希一致；审计顺序为本次开始→Hook→MCP→结束；768字节合成载荷重建哈希一致；原失败文件哈希未变；实际版本和模型元数据分别匹配0.163.0与gpt-6.1-sol。生产代码及测试与B0交付快照的哈希比较无变化。

当前可声明：在本机Windows、Codex0.163.0的受控stdio场景中，已观察并核验同一原生会话的启动Hook执行与后续MCP元数据关联；这不再是跨会话拼接或仅查看app-server schema。

仍不能声明：模型请求实际接收了完整Hook上下文、8192字节边界/无额外落盘、元数据不能被调用方覆盖、多工作区与逐请求cwd保证、跨项目旧句柄拒绝，以及手动/自动/连续压缩。这些仍需身份适配的规范解析与负例验收或C2证据。观察器authenticated_host_identity仍为false，生产身份适配继续关闭，C1整体验收不因单个正向关联成功而升级为通过。

总计原生任务尝试3次：原A成功、原B失败、B追加retry-1成功；本次追加授权已使用。未调用真实Mem0、未修改生产配置或提交Git。验证摘要在本机ignored目录c1-b-retry-1-verification.json，原始记录与失败证据保留。

### 身份元数据来源与用户确认的信任边界

用户已明确选择“可信本机宿主”：信任本机Codex进程及获授权的app-server客户端；防止模型工具参数冒充身份、项目或会话串用，不把stdio字段当成面对任意客户端的密码学认证，不承诺抵御同账户恶意宿主程序。MCP实例保持私有stdio、固定一个受审阅仓库根，不作为任意网络客户端的认证服务。

复核 `rust-v0.163.0` 的完整合并函数后确认：

- 原生模型工具路径分别准备arguments与request meta；模型参数中的同名 `_meta`、sessionId/threadId只是参数数据，不替换外层传输meta。核心随后写入宿主threadId/sessionId，保留身份键和workspaces从turn客户端extras中被过滤，codex_version由宿主最后写入。
- app-server直接工具调用是另一条路径：mcp_processor接受客户端 `_meta`，只强制写入threadId，其余meta再交由thread.call_mcp_tool转发。不能将该入口和模型路径的合并保证混为一谈；在本轮边界中，被授权的该类客户端属于可信宿主侧，而非受解析器认证的任意外部调用者。
- workspaces来自Git仓库根发现，不代表逐请求当前目录；未得到有效Git元数据时还可能为空。单仓库适配只能据此核对固定实例根，缺失或多工作区必须明确拒绝，不能退回启动cwd猜测身份。

据此下一项限定为受控解析与本地SDK负例：核对0.163.0、根层/嵌套身份一致、唯一规范化仓库根；验证缺身份、冲突、跨根、旧会话句柄和回执重用。生产CLI默认拒绝路径不在此项中开放，不增加verified布尔开关，不启动新模型任务或真实Mem0写入。完全一致但由任意客户端编造的meta无法仅靠解析辨别，测试和状态不得作相反保证。

依据：[原生request meta与ID写入](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/mcp_tool_call.rs#L1294-L1425)、[保留字段过滤](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/responses_metadata.rs#L68-L106)、[直接调用路径](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/request_processors/mcp_processor.rs#L679-L737)、[工作区根来源](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/turn_metadata.rs#L573-L598)。

### 受控身份解析与离线SDK负例交付

已在既有host_context.py中新增decode_codex_metadata和codex_request_host；只在受控测试夹具中显式选择新路径。生产__main__.py及旧request_host默认拒绝路径未改变，未新增CLI开关或verified布尔开关。

解析要求固定版本0.163.0、根层sessionId/threadId与嵌套session_id/thread_id四者一致，以及唯一有效工作区根与受审阅实例根严格解析后相同。缺失、畸形、冲突、多工作区或无法解析的路径返回HOST_SOURCE_UNVERIFIED；存在但不同的根返回SCOPE_MISMATCH。产生的HostContext.cwd为固定仓库根，不宣称逐请求动态cwd。上下文管理器使用原ContextVar并在正常、异常和嵌套退出时恢复。

新增13项单元测试和1项包含多个正反场景的真实MCP SDK stdio合同测试。后者通过生产create_server、服务及SessionBinding配合内存PlatformDouble验证：有效status/read成功；同项目其他会话旧句柄、跨项目旧句柄、跨会话读取回执均拒绝；外来工作区拒绝；工具参数内伪造身份不能替换传输身份；缺失/畸形metadata后下次有效请求仍可正常处理，U不被错误推进。夹具ready文件只含标识和路径，未保存正文集合，未连接真实Mem0。

先观察缺少新API及夹具的RED，再实现并观察GREEN；独立审阅重试后结论为规格Approved、质量Approved，无Critical/Important/Minor发现。控制器在审阅结束后实际运行 `.venv/Scripts/python.exe scripts/test.py`：125项，118通过、7项原有在线/原生未验证跳过，13.841秒，退出0；审阅快照与实际代码哈希一致，生产CLI接线未变化。

本轮新增4个代码/测试文件的变更范围为：修改src/experience_memory/infrastructure/host_context.py，新建tests/unit/test_codex_host_context.py、tests/fixtures/codex_host_server.py、tests/contracts/test_codex_host_contract.py；没有新依赖、提交、推送、宿主信任变化、原生模型任务或真实API写入。历史B0快照不再代表当前所有源码相同，此项是B0后的限定增量。

新增结论限于可信本机宿主边界下的受控解析和离线SDK负例，不等同任意客户端认证或新的原生拒绝证据。原生身份适配生产入口、实际模型上下文送达、压缩关联/失败阻断和正常项目切换仍未验收；后续需单独审阅原生接线及相应负例，C2不能由本地测试代替。

### C1 原生接线的离线准备（2026-10-18）

用户确认仅实施专用合成固定根夹具、离线授权负例与可审阅原生执行清单，不授权新的模型任务或在线Mem0。新增夹具固定带本轮标记的真实Git根，从请求传输meta解码身份，复用生产create_server五工具、应用服务和SessionBinding；fixture-only c1_bootstrap只声明nonce参数。后端仅为进程内PlatformDouble，不修改生产CLI、现有Hook或信任设置。

两个种子句柄均先在其所属会话/项目通过真实status，再在当前身份下验证SESSION_INVALID；另有两个有效传输会话的交叉拒绝与原所属会话仍有效的SDK合同。种子明确标为synthetic_not_prior_native_sessions，不冒充前轮原生会话旧句柄。覆盖缺身份、畸形meta、外来根、参数伪造、异常清理、幂等引导及U；审计只保留身份标识/哈希/状态，不保存正文或集合。

本项新增6个代码/测试文件，15项测试分别RED→GREEN。首轮完整离线回归140项、133通过、7项原有未验证跳过，37.754秒，退出0。独立规格/质量审阅均Approved，无Critical/Important/Minor发现；审阅者另跑新增15项，全部通过。控制器审阅后完整复验140项、133通过、7项原有未验证跳过，37.630秒，退出0；任务前85个已有代码/资源文件及6个新文件的审阅摘要均未变，生产接线未修改。

本机被动材料位于ignored `.superpowers/codex-c1-wiring-20261018/review-bundle-1/launch-review.json`，两份配置仅为preview；实际导出返回native_model_tasks_started=0、configuration_installed=false，最终只读复核返回review_unchanged及native_authorization_granted=false。未调用Codex读取当前版本，目标0.163.0不冒充本机最新宿主版本。

### 2026-10-19 C1-E 发现的历史审批表述校正

本轮仅阅读固定版本源码，未重跑上述三个原生探针。0.163.0 exec 设置 harness `AskForApproval::Never`，优先于请求的 `-c approval_policy="on-request"`；TUI 的审批选项和 app-server 的配置读取不能证明 exec 最终有效人工审批。因此上文历史“正常审批/on-request”只能作为请求配置记录，不再当作已验证的 exec 审批语义。已有原生元数据、Hook 关联、退出/额度观察及失败证据不变、不覆盖、不拼接；本轮不新增任何原生授权或模型任务。C1-E 原生 `run` 已在 reservation/进程前明确阻断，需另行审阅审批可验证的 transport；[源码依据与离线边界](codex-c1-native-wiring-review.md#原生分派的明确阻塞01630-exec-不保留人工-on-request)。

2026-10-18被动准备阶段的清单只有执行规格；之后C1-E已实现并完成离线审阅。导出器仍只生成新合成根、标记、配置预览和摘要，不运行Codex或安装配置/Hook；新被动清单仍授权false/剩余0。原生A/B各一次的提案不能直接执行：exec审批语义缺口已代码阻断，必须先另行设计审批可验证的transport。

### 2026-10-19 C1-E 最终交付

五任务分别审阅、修复复审，整轮审阅发现的SDK校验前冻结缺口及清单有界读取均已修复；最终规格/质量Approved，无剩余发现。控制器在最终代码实际运行完整离线回归：277项，270通过、7项原有未验证跳过，262.547秒，退出0。108项代码与最终审阅快照一致；生产src/hooks未变。

全新被动材料为ignored `.superpowers/sdd/2026-10-18-codex-c1-bounded-executor-plan/final-passive-reviewed-20261019-2/`；清单摘要f22f5b236ffc0d24edc221d95e91aa0004b0631f90c69d2c510c00cddf942d58。实际verify为review_unchanged；默认dry-run为native_dispatch_supported=false、native_evidence_claimed=false、model_tasks_started=0；config/approval/receipt/attempts均未创建。旧bundle和失败材料不覆盖。本轮没有新原生/真实Mem0调用或正常项目切换，C1整体/C2仍未通过。完整结果、裁决与未交付原生能力见 [交付验证](verification/codex-c1-executor.md)。

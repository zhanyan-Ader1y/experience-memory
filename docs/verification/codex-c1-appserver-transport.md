# C1-T app-server 离线通道交付验证

日期：2026-10-21。范围：已获确认的C1-T离线开发、逐任务审阅、整轮修复复审、控制器回归与被动材料封存。

## 1. 最终结论

**本轮C1-T离线实施已完成；五任务与整轮修复复审均批准，无剩余或parked发现。原生验收仍pending，公开原生入口保持关闭。**

控制器最终实际运行：**520项，513通过、7项未验证跳过，0失败/错误，退出0**；unittest耗时1088.950秒，墙钟1090.030秒。123项代码/资源与最终审阅快照一致，39项受保护的src/hooks/依赖/清单未变。

没有实际调用Codex（包括版本、schema、只读RPC、线程、turn）、真实模型、Mem0、账户/额度或信任接口；没有修改正常项目/全局宿主配置、依赖或生产身份入口；没有提交、推送、合并或创建远程/worktree。当前`codex/skeleton`仍无首次提交。

## 2. 已交付软件

| 单元 | 实现 | 核心合同 |
| --- | --- | --- |
| 双向拥有进程 | [process](../../scripts/codex_c1_process.py) | 可选pipe；默认devnull不变；实际写ACK、单消费者读取、EOF/自然退出观察、Job整树与I/O清理；超限先检查长度，deadline失败锁存 |
| 固定RPC | [rpc](../../scripts/codex_c1_appserver_rpc.py) | 双向ID独立、一个待响应客户端请求、严格帧/JSON/方法、error锁存、resolved关联、精确取消/空权限，无accept路径 |
| 线程与turn策略 | [session](../../scripts/codex_c1_appserver_session.py) | 校验实际返回配置、未知来源拒绝、完整已知字段、一次新idle线程/turn、通知交错与精确重复幂等、不可逆冻结 |
| v2与独立证明 | [events](../../scripts/codex_c1_appserver_events.py)、[shared results](../../scripts/codex_c1_results.py) | 独立camelCase wire；真实DTO、双审计、item/callId区分；部分否定事实、完成快照、不完整阶段、已知可选metadata校验；旧exec自然退出门槛不改 |
| 离线编排 | [candidate](../../scripts/codex_c1_appserver_candidate.py)、[runtime](../../scripts/codex_c1_appserver_runtime.py) | 同连接假预检、预热前真实reserve、原始消息有界重放、已知审批取消、迟到错误、实际EOF/退出/清理、晚授权与一次最终化 |
| 原始截止锁 | [lock](../../scripts/codex_c1_lock.py)、[bundle](../../scripts/codex_c1_bundle.py) | deadline可选且兼容；设置FD后检查原始预算，和原后端同文件/键/OS锁互斥；不在文件准备后重新起算 |
| 被动合同 | [app-server review exporter](../../scripts/prepare_codex_c1_appserver_review.py) | 新schema2基础bundle+schema1 sidecar；真实字节摘要、同一源码映射、规范字节校验；不安装、不授权或运行 |

双向通道仍使用固定180秒绝对预算、预检最多30秒、每行262144字节（含LF）、接收stdout+stderr及发送分别最多2097152字节、队列最多16条。实际OS文件写入/fsync不能安全抢占；不声称任意文件系统停顿下仍有硬墙钟上限，但所有可控锁等待/重试/清理使用原始预算，不更新计时。

固定五步：bootstrap → 当前status成功 → 同项目有效种子拒绝 → 外项目有效种子拒绝 → 当前status再次成功；U为严格整数0。种子在真实服务原所属身份下验证有效，但**不是旧原生Codex句柄**。

## 3. 最终测试与审阅

| 证据 | 实际结果 | 不能扩大为 |
| --- | --- | --- |
| 控制器最终完整回归 | **520总数=513通过+7跳过，1088.950秒，exit0** | 七项在线/原生门禁通过 |
| 整轮最终修复复审 | **W1/W2均Addressed；Spec Approved、Quality Approved；无剩余发现** | 原生兼容性/权限已验收 |
| 最终修复针对性回归 | Session56 + Events76 + RPC15 + 实际进程候选43 = **190通过，0跳过** | 190次原生模型执行 |
| 代码封存 | **123项最终摘要一致；39项受保护文件无变化** | 未实测平台/解释器支持 |
| 真实本地模拟 | 本地Python假app-server→真实MCP SDK→真实bounded fixture→实际5+5审计→自然exit0/EOF/Job/I/O确认→A/B精确隔离回退→临时根移除 | 真实Codex/账户/策略/预热/模型证据 |
| 被动材料 | 两层验证通过；未安装、无approval/receipt/attempts、无原生授权；入口false | 可直接跑原生turn/start |

七项跳过仍是：1项live functional、4项真实Mem0合同、2项native Hook/compaction，明确unverified。

控制器运行的实际内层命令：

```bash
.venv/Scripts/python.exe scripts/test.py
```

原始输出：`.superpowers/sdd/2026-10-20-codex-c1-appserver-transport-plan/controller-full-2.txt`。

SHA-256：`9e4a6e1267076b30ce2bd404cf6942aa3449104d769d934903a53e88d2289cc8`。

机器可读结果：同目录`controller-full-2-result.json`与`controller-closure-verification.json`。未发现Traceback、RuntimeWarning、ResourceWarning、SyntaxWarning、DeprecationWarning、FAIL/ERROR匹配。测试前/后源码摘要均符合最终审阅快照。

504项实现者全套和508项控制器第一次全套均是对应修复前历史；不替代上述520项。整轮审阅识别出的两项问题已修复：返回线程配置已知可选字段必须校验，合法绑定线程通知的精确重复不能误终止；实际驱动负例/正例均覆盖，没有靠删除原回归获得通过。

## 4. 最新被动材料

目录：`.superpowers/sdd/2026-10-20-codex-c1-appserver-transport-plan/final-passive-reviewed-20261021/`。

| 文件/状态 | 核验值 |
| --- | --- |
| launch-review.json摘要 | `20bbc5f97b25d3c3e675b0800bc4be0cd0a2eced34614f1fb98c08adf3e78703` |
| appserver-contract.preview.json摘要 | `821d6d42510be9398dc119689cb3f111158b6d6fb49c53916ba633b3fd403ce3` |
| source_sha256 | 两份材料完全一致，123项 |
| 基础原生授权 | granted=false，remaining_model_tasks=0 |
| app-server入口 | native_entry_enabled=false，C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING |
| 旧exec入口 | native_dispatch_supported=false，C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED |
| 实际文件 | 无active config、approval、install-receipt或attempts |
| 实际只读验证 | appserver_review_unchanged；native_authorization_granted=false |

本轮已实跑且不调用宿主的只读复核：

```bash
.venv/Scripts/python.exe -I scripts/prepare_codex_c1_appserver_review.py --verify "A:/CodingSpace/Ai/experience-memory/.superpowers/sdd/2026-10-20-codex-c1-appserver-transport-plan/final-passive-reviewed-20261021"
```

此前材料/失败/旧源码摘要都保留，不原地更新或回签为新版本。上述新材料也只是预览，不应复制到正常项目或据此推定已授权原生运行。

## 5. 关键证明边界

- 完成、自然退出、实际EOF、Job空、I/O停止、无sticky失败是独立事实，不用turn/completed制造process_exit0。
- RPC服务端请求ID与客户端请求ID可同值但不同方向；执行item ID、MCP metadata.callId、审计invocation ID不互换。
- unknown后只保留有界否定性观察，发现明确冲突可变failed；不会补字段/哈希、增加成功计数或重新激活场景。
- `turn.items`每个观察条目都受校验/协调。notLoaded必须空；summary核对最后已知agent；full缺覆盖unknown，明确额外/禁止/冲突failed；不从快照发明调用。
- 生效会话配置来自已校验响应，不是请求echo；响应/通知的所有已知字段按固定schema校验，严格权限边界不因辅助扩展字段放宽。
- 晚授权取消会在最终锁内阻止verified；名额不返还、不自动B、不重新连接/重试。保留后记录无法安全恢复时不猜DTO，未确认最终化明确报告。
- 所有实际运行origin为offline_simulation、native_evidence_claimed=false；纯DTO一致性不是任意客户端认证，也不是真实宿主观察。

## 6. 执行裁决完整清单

按本计划账本顺序列全，包含原因和代价：

1. 无HEAD工作区以源码快照替代提交范围，不建worktree/commit；遵守既定约束，代价是共享目录风险，靠顺序实现及摘要控制。
2. 保留本计划报告/快照/失败证据；否则无提交可恢复，代价是ignored磁盘占用。
3. 增加只读shutdown_state观察API，旧stop形状不改；为独立EOF/自然/强制事实，代价是新API和回归面。
4. 客户端RPC待响应时仅通过独立服务端ID取消；无已验证turn或仍pending就关闭，不猜interrupt；代价是较早中止而不是假成功。
5. 公开共享审批纯predicate，不复制校验树；代价是早期模块小型API修改与复审。
6. 新shutdown证明必须包括io_failure/io_threads_stopped，不能丢失sticky超时；代价是更严格DTO/测试，旧exec不变。
7. 辅助设置按schema容许扩展，但ReadOnly权限形状仍严格；代价是某些未知沙箱形状可能保守阻断，需要目标合同评审。
8. 增加绑定事件wrapper和必需appserver_event_proof适配；补足turn归属输入，代价是新adapter/DTO耦合，缺少证明不能成功。
9. 用实际Peer.failure_code的固定映射记录失败，不伪造通知；代价是静态映射维护和驱动交接。
10. 支持严格文本userMessage作为非业务观察，不当成功证明/调用；代价是增加小型wire/lifecycle验证，不扩展多模态。
11. 实际stdin_eof与驱动close ACK分开；非业务摘要最多16项，固定transport literal；代价是更严格证明，不扩大旧exec格式。
12. 完成快照携带有界metadata/digests协调实际事件；防止丢失否定事实，代价是更多新proof字段和兼容测试，不能产生调用。
13. 本轮正例使用源码一致notLoaded=[]；full缺覆盖unknown而非武断失败，summary协调最后agent；代价是保守、有限的history兼容，真实持久覆盖仍未验证。
14. 使用候选专用一帧EOF前瞻和可撤销/join的同Job预检watchdog，并允许小runtime helper；代价是额外I/O/定时竞态面，非空帧仍全部校验、不续期。
15. 收集期间仅三个固定未完成原因可用于继续调度，最终证明不删原因；代价是与固定reason名称耦合，结束/异常时不豁免。
16. 持久化后异常只恢复真实严格匹配名额，无法确认保留null/unknown，不猜DTO记账；代价是可能需要人工诊断，不返还或复用名额。
17. reserve/finish可选原始deadline，默认None兼容，过期仍一次最终化尝试但不能成功；代价是小型producer API与锁竞争复审，不承诺OS写入抢占。
18. 绝对截止锁实现留在scripts，使用同一原锁路径/键/OS字节锁并实测互斥；遵守不改src，代价是少量底层锁/key格式重复和维护告警，不引入第二协调域。

无裁决允许原生调用、AutoReview、审批绕过、生产启用、删除历史证据或Git集成。

## 7. 尚未通过的范围与建议下一步

**本阶段不交付开放原生run CLI。** 新app-server入口仍pending；旧exec因0.163.0强制Never继续unsupported，不能通过授权文件或布尔值直接解锁。

建议下一步单独设计/审阅“受控原生诊断启动入口与生命周期验收”：首先确认实际目标版本与隔离范围、明确线程创建/预热副作用和名额，再分别授权真实线程策略/取消/EOF观察及最多A/B各一次模型场景。`thread/start`可能预热外部连接，不能包装成无副作用只读检查。历史3次原生尝试授权已使用，不能复用。

本轮未证明：真实Codex策略保留、模型工具路径/callId、原生取消或EOF行为、账户/额度/信任、真实Mem0、旧原生句柄/回执、Hook立即注入、C2压缩、H1双宿主隔离或Claude Code接入。七项门禁仍未验证；Python3.10及其他OS无本轮运行证据。

源码/SDK/本地假宿主集成和真实原生四层证据分开。不承诺同账户恶意宿主/管理员篡改抵抗、跨机器锁、远端事务/CAS、断电耐久、瞬时停止或硬token预算。

设计：[C1-T设计](../superpowers/specs/2026-10-20-codex-c1-appserver-transport-design.md)；计划：[C1-T实施计划](../superpowers/plans/2026-10-20-codex-c1-appserver-transport-plan.md)。

# 开发、接入与回退

## 开发检查

离线测试使用专用虚拟环境（实测 Windows/Python 3.13.1），不改全局 Python。首次准备时在项目根执行：

```text
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install "mcp==2.3.0"
```

源码离线入口不依赖调用方 cwd，也不依赖 PYTHONPATH；调用前强制 `EXPERIENCE_MEMORY_LIVE_CONTRACTS=0`、`EXPERIENCE_MEMORY_LIVE_FUNCTIONAL=0` 并移除 `EXPERIENCE_MEMORY_NATIVE_EVIDENCE`，在测试模块导入前生效：

```text
.venv/Scripts/python.exe scripts/test.py
.venv/Scripts/python.exe scripts/test.py --pattern test_source_runner.py
```

`scripts/test.py` 接受 `--pattern`（默认 `test*.py`），使用仓库 `tests` 目录与仓库根作为 top_level_dir；成功返回 0，测试失败或发现错误返回非零。它不提供启用在线测试的选项。原始 `unittest discover` 命令并不等价：它不把 `src` 加入导入路径，也不在发现前强制重置在线/原生证据开关，因此不提供该入口的运行隔离；离线测试只经 `scripts/test.py` 运行。

doctor 命令（与测试一样显式使用固定专用解释器）：

```text
.venv/Scripts/python.exe scripts/launch.py doctor
.venv/Scripts/python.exe scripts/launch.py doctor --project-root <project>
```

3.10 语法兼容不等于 3.10 实测。执行测试与 doctor 命令时始终显式使用 `.venv/Scripts/python.exe`，不要依赖裸 `python`/`py` 或假设 shell 已激活虚拟环境，否则会用到未固定版本的 SDK。源码入口不依赖 cwd。

doctor 只做离线环境与捆绑资源检查，不认证或恢复经验；能力显示 implemented_host_contract_unverified。其报告区分两类相互独立的检查：`local_checks_passed`（仅检查插件捆绑资源文件是否存在，如 plugin.json、hooks、skills、prompts 等清单文件）与 `runtime_checks_passed`（运行环境就绪：Python 不低于声明下限 3.10 且已装 MCP SDK 版本与项目固定版本一致）。可选 `--project-root` 另外输出独立的 `local_project` 本地项目检查（项目范围与核心加载），不并入上述两类；doctor 不检验指针锁目录，也不做认证。退出码 0 仅当两者同时为 true；任一不满足则退出码 1，但仍输出含 python_version、mcp_required、mcp_installed、mcp_compatible 等字段的结构化 JSON，而非堆栈跟踪。例如全局 Python 若装的是 mcp 1.28.1 而非固定 2.3.0，doctor 会报 `mcp_compatible: false`、`runtime_checks_passed: false`、退出码 1，但 `local_checks_passed` 仍可为 true——这正说明两类检查互不牵连，不代表捆绑资源检查失败。`mem0_authentication` 始终为 not_checked，`native_compaction` 始终为 not_verified，不因本次运行时环境检查结果改变或自动升级。

在线/API 验收仍走下方「验证与回退」的原有专用命令，不通过此离线入口启用。

### 本仓库自用准备

[本仓库自用手册](self-use/README.md) 提供 disabled 配置预览、精简核心、本地核对和责任/回退检查。显式 preview 与 doctor 只验证本地准备，不代表认证、安装、可信绑定或压缩恢复；目前未切换 Codex/Claude 或旧插件责任方。真实原生、在线与项目切换仍分别批准。

## C1-E 合成执行器（默认只读，原生分派阻断）

专用入口为 `scripts/run_codex_c1.py --bundle <绝对 bundle 路径>`；默认 `dry-run` 只读，无安装/宿主调用/名额消耗。安装、只读 inspect、运行、回退分别检查审批 action 和不可变回执，模拟 scope 不能授权生产 CLI。Windows Job 所有权、180 秒/流上限、五步实际 SDK 证据及精确双项目回退见 [C1-E 执行审阅](codex-c1-native-wiring-review.md#c1-e-离线有界执行器2026-10-19)。

**原生 `run` 当前代码阻断**：0.163.0 exec 的 harness 将有效审批设为 Never，`-c approval_policy="on-request"` 及 app-server 配置读取不能证明 exec 的人工 on-request。没有改用 AutoReview/绕过，也不调用真实宿主检查。离线验证与被动新 bundle 不构成原生安装/预检/任务授权；需另行审阅能满足审批要求的原生 transport。

## C1-T 离线app-server候选（原生入口仍关闭）

新增 `scripts/codex_c1_appserver_candidate.py` 只接受显式可信程序化本地进程工厂、`offline-simulation` scope 和 `execution_origin="offline_simulation"`；没有 native run、factory/import/origin/executable CLI 开关。公开状态固定 `C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING`，`native_evidence_claimed=false`；旧 exec 原生门槛不变。

被动准备/核验入口只创建**全新目录**，不安装、不启动宿主、不产生审批或消耗名额：

```bash
.venv/Scripts/python.exe -I scripts/prepare_codex_c1_appserver_review.py --output "A:/CodingSpace/Ai/experience-memory/local-evidence/c1t-review-new" --run-nonce c1-offline-review
```

```bash
.venv/Scripts/python.exe -I scripts/prepare_codex_c1_appserver_review.py --verify "A:/CodingSpace/Ai/experience-memory/local-evidence/c1t-review-new"
```

基础清单仍是原 schema2、原 executor 语义；新增 `appserver-contract.preview.json` 是固定 schema1 被动合同，绑定实际基础清单字节摘要和同一份当前源码摘要，`native_entry_enabled=false`。核验保留原 review-only 门槛，再重建固定 profile 比较；已有文件/目录、重复键、超限、reparse、字节或源码变化均拒绝，不覆盖旧材料。手改该布尔值不能开放入口。

离线候选复用同一 Job-owned 双向连接做 synthetic 初始化/config/hooks/usage 预检，在预热前实际持久化 reservation，核对返回线程策略后才发唯一 turn。已知审批只能取消/拒绝；未知交互停止；未验证 turn 或仍有客户端 RPC 待响应时不 interrupt。五步证据来自真实 MCP SDK 的 bounded fixture 与实际 5+5 审计，不是手工成功记录。自然 exit0、实际 EOF、Job/I/O 清理、sticky I/O 错误及最后锁内审批核验分别成立才可离线 verified；证据发布冲突仍只尝试一次 finish，保留已消耗名额并阻止 B。协调器批准的 reserve_attempt/finish_attempt 可选 deadline 参数将同一原始绝对预算传入真实锁等待（不超过原10秒或剩余时间），旧省略调用保持不变；过期仍对已观察的真实reservation尝试一次finish并明确unknown/unconfirmed，不猜DTO、不退款。操作系统文件写入/fsync不能安全强行中止，不能宣称对任意文件系统停顿的通用硬墙钟保证。

这些是源码/SDK/真实本地假进程三层离线证据，不证明真实 Codex 的策略、预热网络、callId、EOF、模型或审批行为；原生是第四层独立待验收证据。五任务及整轮修复复审已批准，无剩余发现；控制器最终520项、513通过、7项未验证跳过（1088.950秒），退出0。最新材料摘要和全部裁决见 [C1-T交付验证](verification/codex-c1-appserver-transport.md)，不将旧实现者/预修复数字当作最终结果。

## 配置与控制存储

项目根 `.experience-memory.json` 配置 enabled、稳定 project_scope、core_paths、8192 字节以内预算及显式 namespace_mapping。缺远程必须指定 local:<stable-id>；不从同名目录猜身份。Mem0Client 首期仅连接 https://api.mem0.ai，不向项目指定的其他地址发送凭据。凭据从 MEM0_API_KEY 或 Windows 用户同名环境读取，不写入配置/日志。

运行时控制目录为 `${PLUGIN_DATA}/control`，缺 PLUGIN_DATA 时为用户目录 `.experience-memory/control`。只有 ID、状态、指纹与锁，无经验正文、摘要或集合。目录须由可信本机维护；只支持单宿主写入，不声明远端事务/CAS、抵御管理员改目录或断电耐久。T02 旧临时 prepared 指针不自动迁移为已绑定会话；字段不符明确拒绝。

## 五工具

| 工具 | 行为 |
| --- | --- |
| memory_search | 当前项目及显式旧命名空间的候选检索；服务端先过滤控制记录，旧/缺字段记录 needs_review |
| memory_read | 精确读取，以仅含 ID/状态/指纹/签发时间的本地指针续接远端回执；同会话同记录最多复用一小时，版本变化或过期先定案旧操作再轮换；因此不是无副作用工具 |
| memory_write | upsert/invalidate；同稳定 ID 维护真实记录，标签/来源写入 Mem0 metadata，失效保留 ID |
| memory_use | 核验回执和当前有效版本后登记采用；同一操作保留原 U，不滚回新窗口 |
| memory_status | 当前绑定、窗口、U 和原生未验收状态；不把认证失败报成空库 |

所有工具需要本会话 session_handle。它只由 SessionStart 绑定注入，不增加任意 session_id 的工具入口。MCP 还必须有每请求宿主身份，只有知道 handle 不足以授权。首期每个检出目录单独启动配置实例；实例拒绝其他 Git 根，避免把 A 的显式身份配置套到 B。同仓库不同 worktree 的实例仍通过相同 project_key 共享经验，根会话分别绑定。

维护候选例子（合成示例，不是经验）：

```json
{
  "experience_id": "EXP-EXAMPLE-1",
  "body": "完整经验正文",
  "retention_text": "单行且不含留存标记的摘要",
  "status": "active",
  "sources": ["docs/verified-evidence.md"],
  "tags": ["python", "testing"],
  "metadata": {"evidence_type": "verified"},
  "admission_basis": "可定位核验证据与适用条件",
  "deduplication": {"decision": "new", "reason": "已审阅语义候选并核对稳定ID"}
}
```

更新/失效带真实 record_id、最新 expected_fingerprint，deduplication.decision 使用 replace/merge。active 只接受 verified 或 user_requirement 证据类型；未证实默认 pending_verification。语义候选需 agent 作明确判断，工具不承诺自动判断两条经验是否同义。跨旧命名空间允许查询/核验，写入迁移不自动进行。

operation_id 在每个逻辑操作首次生成后保持不变；pending/unknown 仅定案原操作，不换 ID 重发。新建/更新/采用/检查点都先写 ID-only 操作记录，远端回读后发布。未知窗口、恢复提交失败或 P1 不明时不发布正文。纯召回、读取、恢复不刷新 U；读取仍会产生独立回执。

## 原生契约待 G1

MCP 的 provisional `experience_memory_host={cwd,session_id}` 元数据适配已实现并默认关闭；它不是已确认的 Codex 接口。代码注入的测试身份仅用于受控夹具。当前 CLI 不能凭猜测 cwd 或调用方句柄建立成功身份；最终 G1 应核验实际宿主每请求来源后确定适配，不开放配置布尔开关绕过。

Hook SessionStart(startup/resume/clear) 绑定并恢复当前窗口；PreCompact 准备并确认本次检查点；SessionStart(compact) 精确恢复。当前边界适配接受原生 compaction_id 或 turn_id；缺失或不匹配停止，不用上次 ready。其跨事件稳定性待 G1。PostCompact 没有可靠关联证据前不启用。

完整载荷含固定核心、项目核心、句柄及正式区域包装，严格不超过预算。默认 Hook 保留宿主输出限制；additionalContextLimit=0 仅在 [合成探针](contract-probes.md) 实测配置及无额外落盘后启用。生产 Hook 不覆盖 compact_prompt。

## 验证与回退

```text
EXPERIENCE_MEMORY_LIVE_FUNCTIONAL=1 python -m unittest tests.contracts.test_functional_platform -v
```

Windows 先设置同名环境变量。该测试仅使用 app=experience-memory-contract-test 的随机 contract-<run>-a/b，不碰正常经验、不自动删除数据。离线服务替身、真实 API 和原生宿主证据分别记账。

用户要求不打包；保持源码清单、Skill、提示词及配置。正常项目尚未切换旧 Mem0。最终接入先审阅精确配置差异/命令及回退：专用会话信任验证后，再按项目将新插件设为唯一经验责任方，停用旧插件本地 retrieval cache 链路；保留云端经验和旧配置副本。失败则停用新项目 Hook/MCP、恢复原项目配置，不能全局卸载旧插件或清空经验库。正式切换及回退实测仍待 G1/G4。

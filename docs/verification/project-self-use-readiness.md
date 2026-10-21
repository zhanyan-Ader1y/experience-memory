# S0 本仓库自用接入准备验证

日期：2026-10-21。范围：用户批准 S0 离线实施；不包含真实宿主、在线、安装或项目切换。

## 1. 结论

**S0 离线接入准备已完成。** 两项实现与8项新增合同通过；独立只读审阅Spec/Quality均Approved、无发现。首次全套因既有C1测试合同失败，按用户追加的离线修复授权仅修正该测试方法后，最终完整回归528项、**521通过、7项未验证跳过、0失败/错误，退出0**，unittest1910.579秒；131项冻结代码/资源摘要无变化。首次失败及修复过程保留在下文与[计时修复报告](c1-appserver-timing-fix.md)。

本产品尚未在 Codex 或 Claude Code 中启用，本仓库尚未开始产品自用，旧插件未盘点/未切换。生产身份默认拒绝、exec unsupported、app-server native pending 均保持。

**授权修复结果：** 用户批准既有C1阻塞的离线定位与必要修复。发现原<9秒断言误计入进程前后校验/最终化，以及自然退出模式的5秒夹具窗口可能先杀死父进程；仅修改一个测试方法，真实进程区间仍<9秒、整候选仍180秒、全部安全断言保留，sleepchild使用15秒fallback且必须在9秒内完成。受控/定向、独立审阅与最终全套均通过，详见[耗时合同修复验证](c1-appserver-timing-fix.md)。以下首次失败及当时的诊断限制作为历史保留。

## 2. 首次完整回归的实际结果（修复前历史）

实际入口：

```bash
.venv/Scripts/python.exe scripts/test.py
```

| 项目 | 本轮实际结果 |
| --- | --- |
| 发现/运行方法数 | 528 |
| 成功方法数 | 520（按实际 `... ok` 行核对） |
| 跳过方法数 | 7，仍为未验证 |
| 失败方法数 | 1，该方法内两个子场景失败 |
| unittest 原始汇总 | `FAILED (failures=2, skipped=7)` |
| unittest 耗时 / 墙钟 | 2088.484秒 / 2090.254秒 |
| 退出码 | 1，非控制器超时 |
| 冻结代码/资源摘要 | 131项，无变化 |
| 原始输出 SHA-256 | `fcdfbfcd65c3e496fedf2bd9169e45efc012625c9c43c2b2cf1c87efcf314db2` |

失败位于未改动的 [既有 C1 app-server 合同](../../tests/contracts/test_codex_c1_appserver.py:391)，不是新增 S0 方法：

```text
method: test_stdout_eof_is_not_natural_exit_and_sleeping_descendant_is_not_clean_success
line 398: self.assertLess(time.monotonic() - started, 9)
mode=sleepchild: 9.192098500032444 not less than 9
mode=close_hang: 9.216130999964662 not less than 9
```

两个失败是同一方法内的 subTest，不能用 `528-7-2` 错算成功方法数；实际是520成功、7跳过、1失败方法。8个新增S0方法在全套内也均为 `ok`。

首次控制器 `full-regression-result.json` 只在 OK 汇总时提取通过/跳过数，因此本次该文件保留 `passed=null, skipped=null`；不是“实际跳过数未知”。上表依据原始失败汇总与逐方法结果核对，原始材料未覆盖。

### 单方法复验

完整回归结束后，仅针对失败方法，经同一原离线入口复验一次，没有重新跑全套或修改测试：

```bash
.venv/Scripts/python.exe -c "import runpy,unittest; unittest.TestLoader.testNamePatterns=['*test_stdout_eof_is_not_natural_exit_and_sleeping_descendant_is_not_clean_success']; runpy.run_path('scripts/test.py', run_name='__main__')" --pattern test_codex_c1_appserver.py
```

发现1项，48.568秒，退出1；相同两个子场景的耗时断言再次失败，分别9.304961999994703秒与9.244056800031103秒。说明当前条件下可复现，不能未经诊断说成偶发抖动。

断言覆盖的是 `run_fake(mode, seconds=5)` 整个调用的墙钟时间；代码包括进程前检查及后续证据/最终化，不是只测进程运行。现有证据尚不能将超时归因于具体阶段、文件系统开销或进程清理。且本次失败发生在清理断言之前，不能据此宣称这两个子场景的清理断言已验证通过。

本阶段保护 `scripts/` 与既有 C1 测试，不为凑绿放宽9秒阈值、不跳过失败、不改 deadline/清理/最终化。后续深入定位与修复须单独批准扩展范围；该批准也不包含任何真实原生操作。

## 3. 已通过的针对性结果

所有测试经原 `scripts/test.py` 离线入口：

| 范围 | 结果 | 耗时 | 退出码 |
| --- | --- | --- | --- |
| 新 profile 资源前 RED | 发现6项，6项错误；原因是预览资源缺失（FileNotFound/INVALID_CONFIG），无导入错误 | 0.011秒 | 1 |
| 新 profile/核心 GREEN | 6通过，0跳过 | 0.635秒 | 0 |
| 现有核心加载 | 3通过，0跳过 | 0.053秒 | 0 |
| 现有 scope/runtime | 9通过，0跳过 | 2.390秒 | 0 |
| 新 doctor 合同 | 2通过，0跳过 | 0.708秒 | 0 |
| 两个新文件合计 | 8通过，0跳过 | 1.297秒 | 0 |
| 现有 workspace | 4通过，0跳过 | 1.670秒 | 0 |

profile 测试证明静态 Config 可读、预览不被自动发现、无远程时需显式身份、嵌套/移动仍稳定、不同显式范围不共用命名空间、短核心不会展开链接。不是任意客户端身份或原生跨范围拒绝证据。

doctor guard 使用真实 CLI/Config/本地应用检查，仅对禁止实例化的远端 client、凭据入口、绑定和指针构造器设置失败 guard。另有真实 Python 子进程从目录外运行原 source launcher。不创建远端绑定/控制目录，未调用真实 Codex 或 Mem0。

显式预览 doctor 实跑退出0：`enabled=false`，`local_checks_passed=true`、`runtime_checks_passed=true`，Python3.13.1、MCP2.3.0，项目 prepared、核心686字节、remote_binding_confirmed=false；experience/control 为 NOT_CONFIGURED、session_runtime 为 local_only，认证 not_checked、压缩 not_verified。执行时 MEM0_API_KEY 显式置空，不读取真实凭据值。

项目核心686 UTF-8字节，通用协议与项目核心按bootstrap方式拼接1470字节；不代表包含句柄/留存区/包装的完整additionalContext已验收。

## 4. 审阅与变更边界

独立只读审阅由 `experience-memory-reviewer` 完成：Spec Approved、Quality Approved，Critical/Important/Minor均无。审阅核对当前实现与已提供针对性输出，没有运行命令或独立复测；也没有把最终完整回归判为通过。结论和限制保存在本计划专用 `review.md`。

155个实施前文件在S0初始交付时只有下列4份允许文档改变，151个既有保护文件未改；之后用户明确授权C1阻塞修复，又仅修改一个既有C1测试方法。最终相对该基线为5份既有文件改变、150份不变，所有生产源码、脚本、清单与旧资源仍未改：

- `docs/operations.md`：本仓库自用导航。
- `docs/agent-setup.md`：开发指导与产品自用的区别。
- `docs/superpowers/plans/2026-10-14-experience-memory-followup-plan.md`：S0实际状态。
- `docs/superpowers/plans/2026-10-21-project-self-use-readiness-plan.md`：批准范围与执行进度。

S0原新增6个交付文件：被动preview、固定core、自用手册、两份测试和本验证报告。追加修复新增独立计时验证报告，代码仍只修改 `tests/contracts/test_codex_c1_appserver.py` 的一个方法。没有生产src/scripts/hooks/旧产品skills/清单修改，没有根自动发现配置、MCP或宿主设置。修复后完整回归期间131项冻结代码/资源摘要无变化。

本地链接核对采用实际Markdown链接，代码块或行内代码中的未来示例不冒充现有文件。首次失败交付时53条链接有效的结果保存在上级 `closure-verification.json`；修复后最终摘要/链接核对保存在 `c1-timing-fix/final-verification.json`。6个禁止实际配置路径均未创建。

## 5. 交付和后续门槛

- [自用手册](../self-use/README.md)
- [配置预览](../../examples/self-use/project-config.preview.json)：enabled=false，无根配置安装。
- [固定项目核心](../self-use/core.md)：仅导航和固定边界，不保存动态经验。
- [实施计划](../superpowers/plans/2026-10-21-project-self-use-readiness-plan.md)

完整 additionalContext/宿主送达、真实认证/回读、可信 MCP 绑定、压缩留存、双宿主共同锁、旧责任方切换及真实开发闭环均未由本阶段验证。不以其他已安装 Mem0 工具代替本产品，不新增本地经验备用库。

**既有C1阻塞已解除，S0离线接入准备验收完成。** 下一独立门槛恢复为C1-T之后的受控原生诊断入口与生命周期验收设计/授权；当前没有自动进入原生或在线阶段。

修复后的最终原始汇总为528项、521通过、7跳过，退出0；原始日志SHA-256为 `71fca21dd000a294b827786c022fa7a0d38c7c0717c3d8a7b79347b20e3ad4bc`。后台控制器因未处理Windows CRLF而误计passed=0、自身返回非零，已按splitlines逐项与unittest汇总校正至 `full-regression-reconciled.json`；测试进程本身始终退出0，原日志及原控制器结果未覆盖，没有为统计问题重跑全套。

本计划专用 ignored 证据目录：`.superpowers/project-self-use-readiness-20261021/`。保留 before.json、frozen-code.json、progress.md、review.md、逐项输出、full-regression.txt、原控制器结果和单方法复验输出，不覆盖首次失败、不清理其他计划证据。未提交、推送、合并、创建远程或 worktree。

# C1 app-server 耗时合同修复验证

日期：2026-10-21。范围：用户明确授权“先解决这个阻塞，允许离线定位和必要修复”。仅修复本地 C1 测试合同，不改生产 src、不调用真实 Codex/模型/Mem0/账户/信任、不安装配置、不提交或创建 worktree。

## 1. 当前状态

**阻塞已解除。** 最小修复仅修改 [既有测试方法](../../tests/contracts/test_codex_c1_appserver.py:391)，受控回归、原入口精确方法、S0新增8项合同及完整离线回归均通过。独立只读审阅Spec/Quality均Approved、无发现。

最终全套实际528项：**521通过、7项未验证跳过，0失败/错误，退出0**；unittest1910.579秒，墙钟1912.279秒；131项冻结代码/资源摘要无变化。这完成S0离线接入准备验收，不代表产品已在真实宿主启用。

原 [S0 完整回归失败](project-self-use-readiness.md) 保留：528个方法中520通过、7跳过、1方法两个subTest失败，退出1。此前失败输出及摘要没有覆盖。

## 2. 根因与证据界限

### 2.1 计时对象不匹配

原测试给 OwnedProcess 的夹具看门狗是5秒，但从进入 `run_fake()` 前开始测量，到整个候选返回就要求 `<9秒`。该区间不仅有进程，还包括进程前源码/配置/授权复核，进程停止后的证据读取、sidecar/源码/授权复核和最终记账。

实际候选的合同是180秒原始绝对预算；受信本地工厂可以给它创建的OwnedProcess更短的预算。5秒进程预算不能推出整候选必在9秒内完成。这是测试测量边界错误，不是把真实业务 deadline 延长。

第一次分段诊断使用只记录函数名、父阶段及耗时的透明wrapper，仍经原scripts/test.py发现/执行；真实调用和返回不变。四次候选约5.81—6.24秒，实际stop约0.006—0.051秒，源码/配置/sidecar校验和最终化有独立开销。本次诊断未重现旧9秒失败，因此**不能声称已经还原此前失败运行的每段开销，也不将其直接归因为环境抖动**。

随后进行可控区分：仅在真实 `OwnedProcess.stop()` 已返回后插入5秒延迟，再调用原finalize；不替换其结果，不跳过任何真实授权、源码核对或账本写入。

| 原断言的受控RED | 整候选耗时 | 真实进程构建→Job/I/O stop |
| --- | --- | --- |
| eof_no_exit | 15.649秒 | 4.030秒 |
| sleepchild | 14.244秒 | 4.518秒 |
| close_hang | 19.509秒 | 7.155秒 |
| blocked | 12.264秒 | 4.011秒 |

四个场景都因原整候选 `<9秒` 断言失败，即使所测真实进程生命周期都小于9秒。这证明原断言会因清理完成后的正常阶段开销误报，不证明每个原失败都由同一种OS延迟造成。

### 2.2 自然退出场景不应先被短看门狗杀死

只修正计时范围后的第一次受控复验，保留的 `sleepchild` 断言仍失败：`natural_exit_code=None`，不是0。其5秒工厂预算会在完整SDK启动/五步调用完成前触发终止，原来的耗时断言提前失败可能遮住这个条件。

sleepchild 的目标是证明“父进程自然退出，但睡眠子进程仍需被Job清理”，不是证明5秒超时。它现使用本测试类正常SDK场景已经采用的15秒工厂看门狗，但仍要求真实构建至清理 `<9秒`、父进程自然退出0、子进程确实存在且已退出。若候选错误地等到15秒看门狗才处理睡眠子进程，9秒断言仍会失败。这与仅提高耗时断言上限不同。

其余 eof_no_exit/close_hang/blocked 三个超时场景仍是5秒。没有修改fixture、OwnedProcess、candidate、RPC、事件、授权或最终化代码。

## 3. 最小改动

仅 [test_codex_c1_appserver.py:391](../../tests/contracts/test_codex_c1_appserver.py:391) 的一个方法：

- 使用既有工厂 `mutate` 回调，在实际OwnedProcess构造前记录时间；使用既有 `stopped` 回调，在真实stop返回后记录时间。
- 要求两个回调各观察一次，进程构建至实际Job/I/O stop仍 `<9秒`。
- 整候选独立核对 `<180秒`，不把源码复核/最终化的时间算成短进程预算。
- sleepchild 的工厂窗口按正常SDK场景设为15秒，其余保持5秒。
- 全部原有停止账本、未verified、attempt_reserved、Job根/后代、I/O停止、真实PID退出、EOF、natural_exit_code和sticky deadline错误断言保留，并在计时断言前执行。

差异由当前源码重建旧方法，再与本次修复前SHA-256严格核对，确认diff以真实旧字节为基线；只有该测试方法变化，不以所有untracked为变更范围。

## 4. 已执行的针对性验证

### 同一清理后延迟实验

修改后仍使用同一个诊断脚本，仍在实际stop之后插入5秒并执行原finalize：1方法、四场景全部通过，67.887秒，退出0。

| 场景 | 整候选耗时 | 真实进程构建→Job/I/O stop |
| --- | --- | --- |
| eof_no_exit | 12.327秒 | 4.028秒 |
| sleepchild | 15.236秒 | 5.343秒 |
| close_hang | 13.664秒 | 4.022秒 |
| blocked | 13.253秒 | 4.020秒 |

修改前的RED、边界修正后暴露natural退出问题的中间失败、最终GREEN均独立保存，没有覆盖或只展示最后一次通过。

### 不带wrapper/延迟的原入口

```bash
.venv/Scripts/python.exe -c "import runpy,unittest; unittest.TestLoader.testNamePatterns=['*test_stdout_eof_is_not_natural_exit_and_sleeping_descendant_is_not_clean_success']; runpy.run_path('scripts/test.py', run_name='__main__')" --pattern test_codex_c1_appserver.py
```

实际1方法通过，48.142秒，退出0。四个模式及全部保留断言均执行；不是1次真实原生任务。诊断实验经同一canonical runner，未自行复制离线环境/发现引导。

## 5. 最终完整回归与审阅

独立只读审阅已结束：Spec Approved、Quality Approved，无Critical/Important/Minor发现。审阅确认真实构建至Job/I/O停止的回调边界、保留的9秒断言、sleepchild的15秒fallback及未删除的原安全断言。审阅者未运行测试或独立计算基线摘要，不将提供的控制实验当作其独立复测。

S0新增8项合同再次通过：1.326秒，退出0。最终代码冻结后，仅启动一次修复后全套：

```bash
.venv/Scripts/python.exe scripts/test.py
```

| 最终证据 | 实际结果 |
| --- | --- |
| 原unittest汇总 | `Ran 528 tests in 1910.579s` / `OK (skipped=7)` |
| 通过/跳过/失败/错误 | 521 / 7 / 0 / 0 |
| 测试进程退出码 | 0，无控制器超时 |
| 墙钟 | 1912.279秒 |
| 冻结代码/资源 | 131项，无变化 |
| 告警扫描 | 无Traceback、RuntimeWarning、ResourceWarning、SyntaxWarning、DeprecationWarning匹配 |
| 原始日志SHA-256 | `71fca21dd000a294b827786c022fa7a0d38c7c0717c3d8a7b79347b20e3ad4bc` |

后台控制器最初报非零，不是测试进程失败：它用正则 `$` 匹配 `... ok`，未处理Windows日志CRLF，错误统计passed=0，于是自身核对失败。原日志实际含533个CRLF且测试进程退出0。使用splitlines规范化换行后，实际521个ok、7个skip、无失败/错误，与528汇总完全一致。校正结果保存为 `full-regression-reconciled.json`，原 `full-regression-result.json` 和日志保留；没有为修正统计重新运行全套或改测试结果。

最终仅测试方法这一处代码变更。S0手册/计划/导航与报告根据实际结果更新；原始失败、控制实验中间失败及本次成功均保留，不换签旧证据。最终前后摘要及链接核对记录于 `final-verification.json`。

真实宿主、Mem0、压缩送达、双端共同锁及本仓库产品自用仍未由本修复验收；原生入口和所有授权边界保持。

证据在 ignored `.superpowers/project-self-use-readiness-20261021/c1-timing-fix/`：before.json、progress.md、timing-trace.json、change.diff、诊断脚本、三个独立late-finalization结果及method-green.txt。脚本仅为本次离线诊断，不是生产运行或原生入口。

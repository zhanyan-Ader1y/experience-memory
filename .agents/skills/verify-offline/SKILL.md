---
name: verify-offline
description: Use when offline regression, a specific test file or method, or local diagnostics are requested in experience-memory, especially outside the checkout or with live-test environment flags present.
---

# 离线验证参考

先读仓库根 `AGENTS.md` 的命令与证据规则。以下命令在根目录运行；目录外使用当前检出解释器和脚本的绝对路径。

## 选择最小有用验证

| 请求 | 入口 |
| --- | --- |
| 文件/一组合同 | `scripts/test.py --pattern`；pattern加引号 |
| 一个方法 | TestLoader.testNamePatterns + runpy，仍运行原离线入口 |
| 本地资源/SDK就绪 | `scripts/launch.py doctor`，不是认证/原生验证 |
| 完整交付回归 | 无pattern的scripts/test.py；可能运行较长，等待实际完成 |

单文件：

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_rpc.py
```

单方法示例（按任务替换方法名和文件名）：

```bash
.venv/Scripts/python.exe -c "import runpy,unittest; unittest.TestLoader.testNamePatterns=['*test_layers_obey_dependency_direction']; runpy.run_path('scripts/test.py', run_name='__main__')" --pattern test_dependencies.py
```

目录外把解释器及run_path路径改为该检出的绝对路径。不要改测源码来选择方法，也不要用临时importlib/unittest入口复制环境清理。原脚本只接受`--pattern`；它会在测试导入前重置在线/原生开关并加入源码路径。

## 完成报告

记录实际命令、发现的total、passed、skipped、退出码和本次验证范围。0个测试不算验证；`OK(skipped=S)`中passed=total-S。失败/超时不是skip，也不是成功。后台运行中的测试不重启，冻结快照不在运行中修改。

说明证据层：本地测试/SDK/假宿主不等于真实Mem0或原生Codex。doctor退出0也不代表认证、线程策略或压缩通过。不要开启live开关、连接真实宿主、修改native gate或因历史清单生成原生授权；此skill不授予这些操作权限。

本skill只读取指令，不自动执行脚本。更多背景按需读 [operations](../../../docs/operations.md) 和 [兼容矩阵](../../../docs/compatibility.md)。

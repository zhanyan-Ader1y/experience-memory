# 可复现运行基线与离线诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session after written review. 用户另选逐任务子 Agent 时才使用 superpowers:subagent-driven-development；不自行创建 worktree。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不安装源码包、不中断其他插件的离线测试入口和准确环境诊断，为 Codex 原生验收提供可重复基线。

**Architecture:** 保留现有业务、MCP 和 Hook 接口。scripts/test.py 只负责源码导入、关闭外部验收开关和 unittest 发现；基础设施查询本机环境元数据，由应用诊断 DTO 承载，经现有 doctor 输出，不从接口层直接访问环境适配器。

**Tech Stack:** Windows、Python 3.13.1 专用 .venv、mcp==2.3.0、标准库 unittest/importlib.metadata/venv；不增加生产依赖。

**Spec:** [分阶段双宿主设计](../specs/2026-10-15-dual-host-integration-design.md)，本计划仅实现实施单元 A 的 B0 运行基线，不包含 C1/C2 原生探针和任何宿主适配。

## Global Constraints

- Python 声明下限为 3.10；当前新增运行基线使用 Windows/Python 3.13.1，仅报告实际测试过的版本。
- MCP SDK 固定为 mcp==2.3.0；不得通过降级业务接口迁就全局 MCP 1.28.1。
- Mem0 是非核心正文、摘要和控制集合的持久来源；插件本地只保存标识、状态、指纹和锁。
- 完整 additionalContext 默认不超过 8192 UTF-8 字节，包含固定协议、项目核心、句柄、标记及区域包装；不是 token 预算。
- 核心自身超预算时失败，不截断核心；非核心按 P1/P2/P3、最旧 U、ASCII ID 顺序整条淘汰。
- 仅确认的有效采用推进 U；查询、读取、恢复、压缩和同一逻辑操作重试不推进 U。
- pending/unknown 不是成功；先定案原操作，不因搜索暂时无命中另起写入。
- 同项目经验可在 Codex 与 Claude Code 间共享；回执、句柄、U、窗口和检查点不跨宿主会话共享。
- 可信身份来自经过核验的宿主通道，不来自工具参数、任意句柄、启动 cwd 或历史会话扫描。
- 支持范围限单机器协调写入；不声明远端事务、CAS、跨机器锁或断电耐久。
- 不修改全局 Python、旧插件、正常项目配置或宿主信任；原生启用、在线合成写入及项目切换分别取得范围明确的授权。
- 不打包、不发布；未经明确要求不提交、推送或创建 Git 远程。

## 文件职责与任务边界

| 文件 | 操作 | 职责 |
| --- | --- | --- |
| scripts/test.py | 新建 | 源码离线测试入口，可从任意 cwd 执行，明确禁用在线/原生证据开关 |
| tests/contracts/test_source_runner.py | 新建 | 入口导入路径、开关隔离、发现范围和退出码合同 |
| src/experience_memory/infrastructure/environment.py | 新建 | 查询 Python 与已安装 SDK 元数据，不导入 mcp 或访问网络 |
| tests/unit/test_environment.py | 新建 | SDK 缺失、错版、正确版本与 Python 下限 |
| src/experience_memory/application/contracts.py | 修改 | DiagnosticReport 追加可选 runtime_environment 字段 |
| src/experience_memory/__main__.py | 修改 | doctor 分支装配环境报告并计算退出码 |
| src/experience_memory/interfaces/diagnostics.py | 修改 | 分别报告资源检查与运行环境检查，不扩大能力声明 |
| tests/contracts/test_workspace.py | 修改 | doctor 新字段与 SDK 不匹配的 CLI 退出合同 |
| plugin.json | 修改 | 去掉与源码事实冲突的骨架描述，不改变 Codex wiring |
| docs/operations.md、docs/compatibility.md、docs/verification/full-function.md | 修改 | 新命令、环境前提和新增实测结果，历史证据保留 |

不修改 pyproject.toml 的 SDK pin、五工具、可信身份适配、压缩协议、宿主 Hook 配置、控制键或生产启动解释器选择。正常项目接入时的解释器路径由后续宿主计划审阅；B0 只验证显式 .venv 解释器。

## Task 1：专用环境与可从任意目录执行的离线测试入口

**Files:** Create scripts/test.py、tests/contracts/test_source_runner.py；Modify docs/operations.md 的开发检查部分。

**Interfaces:**
- `scripts/test.py: main(arguments: list[str] | None = None) -> int`。
- 接受 `--pattern`，默认 `test*.py`；使用仓库 tests 目录和仓库根作为 top_level_dir；不提供启用在线测试的选项。
- 调用前设 EXPERIENCE_MEMORY_LIVE_CONTRACTS=0、EXPERIENCE_MEMORY_LIVE_FUNCTIONAL=0，移除 EXPERIENCE_MEMORY_NATIVE_EVIDENCE；在测试模块导入前生效。
- 成功返回 0，测试失败/发现错误返回非零；不改变调用者 cwd。

- [x] **Step 1：创建或核验专用虚拟环境并记录现状。** 先检查 .venv/pyvenv.cfg；已存在时读取其解释器与依赖，不删除或重建用户环境。不存在时在已核对的项目根执行：

```bash
py -3.13 -m venv .venv
```

只向该环境安装声明的 SDK，不 editable install 本项目、不生成本项目 wheel：

```bash
.venv/Scripts/python.exe -m pip install "mcp==2.3.0"
```

```bash
.venv/Scripts/python.exe -c "import sys, importlib.metadata as m; from mcp.server import MCPServer; print(sys.version); print(m.version('mcp')); print(MCPServer.__name__)"
```

若版本不可安装或 API 不存在，停止并报告，不改成 1.x 兼容层。当前只读索引已确认 2.3.0 可见，但不替代安装实测。

- [x] **Step 2：先复现离线基线并保存结果到交付记录，不冒充新功能测试。**

```bash
env -u EXPERIENCE_MEMORY_NATIVE_EVIDENCE PYTHONPATH=src EXPERIENCE_MEMORY_LIVE_CONTRACTS=0 EXPERIENCE_MEMORY_LIVE_FUNCTIONAL=0 .venv/Scripts/python.exe -m unittest discover -s tests -v
```

记录实际数量、耗时和失败；历史 102 项只是对照，不硬编码期望数量。

- [x] **Step 3：编写失败测试。** tests/contracts/test_source_runner.py 使用以下合同，保存/恢复 sys.path，不能污染后续测试模块：

```python
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/test.py"

class SourceRunnerTests(unittest.TestCase):
    def load_runner(self):
        spec = importlib.util.spec_from_file_location("source_test_runner", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_runner_works_outside_checkout_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--pattern", "test_probe_safety.py"],
                cwd=directory, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran ", result.stderr)

    def test_runner_disables_external_evidence_before_discovery(self):
        runner = self.load_runner()
        def discover(*args, **kwargs):
            self.assertEqual(os.environ["EXPERIENCE_MEMORY_LIVE_CONTRACTS"], "0")
            self.assertEqual(os.environ["EXPERIENCE_MEMORY_LIVE_FUNCTIONAL"], "0")
            self.assertNotIn("EXPERIENCE_MEMORY_NATIVE_EVIDENCE", os.environ)
            self.assertEqual(Path(kwargs["start_dir"]), ROOT / "tests")
            self.assertEqual(Path(kwargs["top_level_dir"]), ROOT)
            return unittest.TestSuite()
        flags = {"EXPERIENCE_MEMORY_LIVE_CONTRACTS": "1",
                 "EXPERIENCE_MEMORY_LIVE_FUNCTIONAL": "1",
                 "EXPERIENCE_MEMORY_NATIVE_EVIDENCE": "must-not-read.json"}
        with patch.dict(os.environ, flags), patch.object(sys, "path", list(sys.path)):
            with patch.object(unittest.TestLoader, "discover", side_effect=discover):
                self.assertEqual(runner.main([]), 0)

    def test_runner_propagates_test_failure(self):
        runner = self.load_runner()
        failed = Mock()
        failed.wasSuccessful.return_value = False
        with patch.dict(os.environ), patch.object(sys, "path", list(sys.path)):
            with patch.object(unittest.TestLoader, "discover", return_value=unittest.TestSuite()):
                with patch.object(unittest.TextTestRunner, "run", return_value=failed):
                    self.assertEqual(runner.main([]), 1)
```

- [x] **Step 4：运行失败测试，确认失败来自缺少 scripts/test.py 而不是 SDK 或导入环境。**

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m unittest tests.contracts.test_source_runner -v
```

- [x] **Step 5：实现最小入口。**

```python
import argparse
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

def main(arguments=None):
    parser = argparse.ArgumentParser(description="Run source-only offline tests")
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args(arguments)
    os.environ["EXPERIENCE_MEMORY_LIVE_CONTRACTS"] = "0"
    os.environ["EXPERIENCE_MEMORY_LIVE_FUNCTIONAL"] = "0"
    os.environ.pop("EXPERIENCE_MEMORY_NATIVE_EVIDENCE", None)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    suite = unittest.TestLoader().discover(
        start_dir=str(ROOT / "tests"), pattern=args.pattern, top_level_dir=str(ROOT),
    )
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 6：验证任务合同与完整离线套件。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_source_runner.py
```

```bash
.venv/Scripts/python.exe scripts/test.py
```

- [x] **Step 7：将新命令及显式虚拟环境前提写入 operations.md。** 在线/API 验收仍走原有专用命令，不通过此离线入口启用。审阅差异与命令输出；本任务不提交 Git。

## Task 2：无网络、无 SDK 导入副作用的环境诊断

**Files:** Create infrastructure/environment.py、tests/unit/test_environment.py；Modify application/contracts.py、__main__.py、interfaces/diagnostics.py、tests/contracts/test_workspace.py。

**Interfaces:**
- `inspect_environment() -> dict`，字段 `python_version`、`python_supported`、`mcp_required`、`mcp_installed`、`mcp_compatible`、`ready`。
- `DiagnosticReport.runtime_environment: dict | None = None`，追加到现有字段末尾；不改变已有调用位置参数。
- doctor 保留 local_checks_passed 的资源检查原义，新增 runtime_checks_passed；CLI 仅两者都通过才返回 0。
- doctor 的认证仍为 not_checked、压缩仍为 not_verified、能力仍为 implemented_host_contract_unverified。

- [x] **Step 1：新增元数据和版本边界失败测试。**

```python
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import unittest
from unittest.mock import patch
from experience_memory.infrastructure.environment import inspect_environment

class EnvironmentTests(unittest.TestCase):
    def test_matching_sdk_is_ready_without_importing_sdk(self):
        with patch("experience_memory.infrastructure.environment.version", return_value="2.3.0"):
            report = inspect_environment()
        self.assertTrue(report["ready"])
        self.assertEqual(report["mcp_required"], "2.3.0")
        self.assertEqual(report["mcp_installed"], "2.3.0")

    def test_missing_or_wrong_sdk_is_not_ready(self):
        for installed in (None, "1.28.1", "2.2.0", "2.3.1"):
            with self.subTest(installed=installed):
                kwargs = {"side_effect": PackageNotFoundError("mcp")} if installed is None else {"return_value": installed}
                with patch("experience_memory.infrastructure.environment.version", **kwargs):
                    report = inspect_environment()
                self.assertFalse(report["mcp_compatible"])
                self.assertFalse(report["ready"])
                self.assertEqual(report["mcp_installed"], installed)

    def test_python_below_declared_floor_is_not_ready(self):
        with patch("experience_memory.infrastructure.environment.sys.version_info", (3, 9, 0)):
            with patch("experience_memory.infrastructure.environment.version", return_value="2.3.0"):
                report = inspect_environment()
        self.assertFalse(report["python_supported"])
        self.assertFalse(report["ready"])

    def test_required_sdk_matches_project_declaration(self):
        root = Path(__file__).resolve().parents[2]
        self.assertIn('"mcp==' + inspect_environment()["mcp_required"] + '"',
                      (root / "pyproject.toml").read_text(encoding="utf-8"))
```

- [x] **Step 2：在 test_workspace.py 的现有 doctor 测试中增加真实环境字段断言，并新增进程内 CLI 错版测试。** 保留所有原生未验收断言。

```python
self.assertIs(report["runtime_checks_passed"], True)
self.assertEqual(report["runtime_environment"]["mcp_installed"], "2.3.0")
```

新增测试方法使用下列结构验证非零退出和独立资源检查：

```python
def test_doctor_reports_sdk_mismatch_with_nonzero_exit(self):
    from contextlib import redirect_stdout
    import io
    from unittest.mock import patch
    from experience_memory.__main__ import main
    output = io.StringIO()
    with patch("experience_memory.infrastructure.environment.version", return_value="1.28.1"):
        with redirect_stdout(output):
            code = main(["doctor", "--plugin-root", str(ROOT)])
    report = json.loads(output.getvalue())
    self.assertEqual(code, 1)
    self.assertTrue(report["local_checks_passed"])
    self.assertFalse(report["runtime_checks_passed"])
    self.assertEqual(report["mem0_authentication"], "not_checked")
    self.assertEqual(report["native_compaction"], "not_verified")
```

- [x] **Step 3：分别观察新增测试失败。**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_environment.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_workspace.py
```

- [x] **Step 4：实现 environment.py，仅查询元数据，不导入 mcp 或访问网络。**

```python
from importlib.metadata import PackageNotFoundError, version
import platform
import sys

REQUIRED_MCP_VERSION = "2.3.0"

def inspect_environment():
    try:
        installed = version("mcp")
    except PackageNotFoundError:
        installed = None
    python_supported = sys.version_info >= (3, 10)
    compatible = installed == REQUIRED_MCP_VERSION
    return {
        "python_version": platform.python_version(),
        "python_supported": python_supported,
        "mcp_required": REQUIRED_MCP_VERSION,
        "mcp_installed": installed,
        "mcp_compatible": compatible,
        "ready": python_supported and compatible,
    }
```

- [x] **Step 5：接通 DTO、装配和退出码。** 在 DiagnosticReport 最后追加：

```python
runtime_environment: dict | None = None
```

在 __main__.py 的 doctor 分支、构造报告之前导入并调用环境适配器：

```python
from .infrastructure.environment import inspect_environment
```

保留原 DiagnosticReport 六个位置实参，追加关键字参数：

```python
runtime_environment=inspect_environment()
```

在 diagnostics.py 保留现有输出，追加：

```python
result["runtime_checks_passed"] = bool(
    report.runtime_environment is not None and report.runtime_environment.get("ready") is True
)
```

__main__.py doctor 返回条件改为：

```python
return 0 if output["local_checks_passed"] and output["runtime_checks_passed"] else 1
```

不在 interfaces 中导入 infrastructure，现有架构测试必须继续通过。

- [x] **Step 6：验证定向测试、完整回归与真实离线 CLI。**

```bash
.venv/Scripts/python.exe scripts/test.py
```

```bash
.venv/Scripts/python.exe scripts/launch.py doctor
```

```bash
py -3.13 scripts/launch.py doctor
```

前一 doctor 在专用 SDK 2.3.0 环境应返回 0；后一全局环境若仍为 1.28.1 应返回 1、输出错版事实而不是堆栈。凭据只报告可用性，不打印值；认证不访问网络。审阅差异，不提交。

## Task 3：更新交付事实并封闭 B0 验收

**Files:** Modify plugin.json、docs/operations.md、docs/compatibility.md、docs/verification/full-function.md、本计划复选框；复用 tests/contracts/test_package.py，不修改其代码。

**Interfaces:** 不新增生产 API；对外只新增离线测试命令和 doctor 环境字段，现有五工具及能力状态不变。

- [x] **Step 1：运行既有清单合同并人工审阅描述事实。** 实施时裁定不增加精确文案断言：它只检测人为修改 prose，不保护运行行为。现有 test_package.py 继续核验 JSON、Hook 路径与 stdio 入口；人工确认描述不再把已接通存储说成未实现，也不把原生能力说成已验收。

- [x] **Step 2：运行清单合同并确认保持通过。** 本任务只改描述和交付文档，不伪造文案测试的 RED。

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_package.py
```

- [x] **Step 3：只更新 plugin.json 的 description。**

```json
"description": "项目经验读写、有效采用与压缩留存；原生宿主身份和恢复能力仍待验收。"
```

不变更 extensions、Hook 路径、MCP 启动方式或加载状态。

- [x] **Step 4：在 operations.md 说明源码直接测试、显式解释器、doctor 两类检查与退出码。** 记录 `.venv/Scripts/python.exe scripts/test.py` 只运行离线合同，运行前强制关闭真实 API 和原生 evidence 开关。原有在线命令保持专门入口、需单独授权。不暗示声明 Python>=3.10 等于所有版本实测。

- [x] **Step 5：执行最终验收并据实更新证据，不覆盖历史记录。**

```bash
.venv/Scripts/python.exe scripts/test.py
```

```bash
.venv/Scripts/python.exe scripts/launch.py doctor
```

```bash
git diff --check
```

```bash
git status --short --branch
```

仓库无 HEAD 且文件未跟踪，git diff --check 不能覆盖未跟踪内容；额外使用新文件测试、JSON 解析与人工审阅，不把空 diff 当作无改动证据。

- [x] **Step 6：在 compatibility.md、verification/full-function.md 追加解释器/SDK、实际测试数量、耗时、跳过理由、doctor 结果和限制。** 不写凭据、正文或原生证据为真声明。B0 通过仅证明环境和离线合同，不将任何 Codex/Claude 能力升级为 verified。

- [x] **Step 7：通过 diff/文件审阅展示修改并停止在 C1 原生启用之前。** 下一实施单元是 Codex 专用探针审阅，不自动启动模型、修改信任或写真实 Mem0。没有提交授权，不创建提交。

## 计划自查

- B0 目标分别落在 Task 1 的入口、Task 2 的诊断、Task 3 的交付证据。
- 操作保持在单宿主离线基线内，没有把双宿主键模式改动混入此计划。
- 所有新增函数、参数和报告字段在本计划中定义；其他调用沿用现有源文件。
- 原生事件/身份适配、共享锁目录和 Claude 插件布局均有设计边界，但不在本计划编造实现。
- 每项均包含失败测试、最小实现和验证；提交步骤以用户无提交授权约束为准，不执行。

## 执行交接与结果

2026-10-15 用户审阅后选择逐任务子 Agent 执行。Task 1—3 均完成独立规格/质量审阅；整体审阅后的四项测试隔离与文档问题在同一修复批次处理，并通过限定复审。控制器最终实际回归111项，104通过、7项在线/原生未验证跳过，耗时10.866秒；专用环境doctor退出0，全局SDK1.28.1环境退出1且输出结构化事实，认证和原生状态未升级。详见 [交付验证](../../verification/full-function.md)。

实施相对示例的调整：Task1用真实源码导入和失败测试加强合同，并修复临时发现路径清理；Task2增加新进程无SDK导入断言；Task3以现有清单合同及人工文案审阅替代精确文字断言。既有operations.md中的错误直接发现等价说法和资源检查范围也已纠正。

B0完成，停止在C1原生启用之前。没有提交、推送、打包、修改全局Python/信任或在线Mem0写入。仓库无HEAD，审阅使用文件快照；本轮忽略目录中的ledger与快照保留用于恢复。不自动进入Codex原生探针、Claude适配或正常项目切换。

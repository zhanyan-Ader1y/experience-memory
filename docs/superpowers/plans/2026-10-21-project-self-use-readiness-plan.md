# 本项目自用接入准备 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task after implementation approval. 用户明确选择逐任务子 Agent 时才使用 superpowers:subagent-driven-development；不因本计划自动派发。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付本仓库自己的被动配置、精简核心、本地核验合同及接入/回退手册，使下一阶段原生接入有明确对象，但不提前启用产品。

**Architecture:** 完全复用现有 Config、scope、FileCoreLoader 和 doctor，不增加运行时框架或新的管理 CLI。静态候选放 examples/self-use，固定核心和操作约定放 docs/self-use；离线测试证明候选可用且本地准备不会变成远端绑定或原生验收。

**Tech Stack:** Python 标准库 unittest、现有固定 mcp==2.3.0、现有 scripts/test.py 离线入口；临时 Git 根只用于本地测试，不创建本仓库提交或 worktree。

**Spec:** [已确认的本项目双宿主自用设计](../specs/2026-10-21-project-self-use-design.md)，本计划只实施其 S0。2026-10-21 用户已确认设计方向，并以“开始执行”批准当前会话顺序实施 S0；真实宿主/在线/切换授权仍为零。

## Global Constraints

- Python 声明下限 >=3.10；当前本机运行证据为 Windows/Python3.13.1；MCP 固定 mcp==2.3.0，不新增依赖或修改全局 Python。
- Mem0 是非核心正文、摘要和控制集合的持久来源；本地只保存运行时定义的标识、状态、指纹和锁。
- 完整 additionalContext 默认最多 8192 UTF-8 字节，包含固定协议、项目核心、句柄、标记和包装；核心超限失败，不截断。
- 仅确认的有效采用推进 U；查询、读取、恢复、压缩和同一逻辑操作重试不推进 U。
- pending/unknown 不是成功；先定案原 operation_id，不因搜索暂时无命中另起写入。
- 第一阶段不修改 src/、scripts/、hooks/、根产品 skills/、生产清单、根自动发现配置、宿主 MCP/Hook 配置或信任/权限设置。
- 第一阶段不调用真实 Codex（包括版本/schema/只读 RPC/线程）、模型、Mem0、账户/额度或信任接口；不读取真实凭据值。
- 真实原生诊断、在线合成写入、实际安装和本仓库项目切换分别取得明确授权；thread/start 可能预热外部连接，不是纯本地只读。
- 同项目经验共享不等于控制状态共享；共同锁与隔离通过前不开放双端共享写入。
- 不打包、不发布，不提交、推送、合并、创建远程或 worktree；保留已有快照与失败证据。

---

## 0. 范围、文件及交接合同

### 执行前读取

- 根 AGENTS.md；设计第 4、7、8、9、10 节。
- `src/experience_memory/config.py`：Config、load_config、load_project_config；只有根 `.experience-memory.json` 自动发现。
- `src/experience_memory/infrastructure/scope.py`：resolve_scope；project_key 保持显式 local 身份，app/project ID 使用现有确定性映射。
- `src/experience_memory/infrastructure/core_loader.py`：load_core；不递归展开 Markdown 链接。
- `src/experience_memory/bootstrap.py`：固定协议与项目核心的拼接方式；doctor 不提供 data_root，因此不创建 Mem0Client/远端仓储。
- `src/experience_memory/__main__.py`、`scripts/launch.py`、`tests/contracts/test_workspace.py`：doctor 的实际参数、结构化结果和本地入口。
- [C1-T交付](../../verification/codex-c1-appserver-transport.md)：下一阶段为受控原生诊断入口审阅，不是直接运行旧 exec。

若实现与此处读取结果冲突，先指出差异；不能为迎合本计划修改生产语义。当前任务只需新增资源和测试。

### 文件结构

| 文件 | 操作与职责 |
| --- | --- |
| `examples/self-use/project-config.preview.json` | Task 1 新增：原模式、显式项目身份、disabled 被动候选 |
| `docs/self-use/core.md` | Task 1 新增：短固定核心，不是经验缓存 |
| `tests/unit/test_self_use_profile.py` | Task 1 新增：配置、发现、身份、核心合同 |
| `docs/self-use/README.md` | Task 2 新增：自用流程、状态、责任与回退、下一门槛 |
| `tests/contracts/test_self_use_readiness.py` | Task 2 新增：实际 doctor 路径与无远端装配保证 |
| `docs/operations.md` | Task 2 增量：链接自用手册，不搬动历史验证内容 |
| `docs/agent-setup.md` | Task 2 增量：区分开发指导、S0 准备和真正产品接入 |
| `docs/superpowers/plans/2026-10-14-experience-memory-followup-plan.md` | Task 2 更新 S0 实际进度/结果与下一门槛；保留历史 |

不新建 `.experience-memory.json`、`.mcp.json`、`.claude/settings*.json`、Claude 产品清单、Codex 配置、产品 skill 副本、安装器或证据数据库。

### 执行进度

- [x] Task 1：disabled 候选、固定核心与本地单元合同。
- [x] Task 2：doctor 合同、自用/责任/回退手册与导航。
- [x] 最终离线回归、变更边界核对和真实结果交付：首次全套失败后，用户明确授权离线定位/必要修复；仅修正一个既有C1测试方法，定向/受控验证和独立只读审阅通过。最终全套528项、521通过、7项未验证跳过，1910.579秒，退出0；131项冻结代码/资源摘要无变化。S0接入准备完成，尚未产品自用，详见[本轮验证](../../verification/project-self-use-readiness.md)与[计时修复报告](../../verification/c1-appserver-timing-fix.md)。

执行开始时在 ignored `.superpowers/project-self-use-readiness-20261021/` 建立本计划自己的摘要快照和进度账本，不覆盖 agent 初始化或 C1-T 工作区。无 HEAD 不把全部 untracked 内容当作本次改动，不建立首次提交。报告只记录路径/状态/摘要，不复制经验正文。

## Task 1: 被动项目配置与精简核心

**Files:**
- Create: `examples/self-use/project-config.preview.json`
- Create: `docs/self-use/core.md`
- Test/Create: `tests/unit/test_self_use_profile.py`

**Interfaces:**
- Consumes: `load_config(path: Path | None = None) -> Config`、`load_project_config(project_root: Path, explicit_path: Path | None = None) -> Config`。
- Consumes: `resolve_scope(cwd: Path, project_scope: str | None = None, *, namespace_mapping: NamespaceMapping | None = None) -> Scope`。
- Consumes: `load_core(project_root: Path, relative_paths: tuple[str, ...], *, budget_bytes: int = 8192) -> str`。
- Produces: 原 Config 模式的静态文件，`enabled=false`、`project_scope="local:experience-memory"`、`core_paths=["docs/self-use/core.md"]`；不增加 Python 公共接口。

- [x] **Step 1: 新建 6 项真实资源合同测试，先不创建资源**

使用以下完整内容，不把样例改成 mock Config 或 mock scope：

```python
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from experience_memory.config import Config, load_config, load_project_config
from experience_memory.domain.errors import ErrorCode, MemoryError
from experience_memory.infrastructure.core_loader import load_core
from experience_memory.infrastructure.scope import resolve_scope

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = ROOT / "examples/self-use/project-config.preview.json"
EXPECTED = {
    "enabled": False,
    "mem0_api_url": "https://api.mem0.ai",
    "project_scope": "local:experience-memory",
    "core_paths": ["docs/self-use/core.md"],
    "retention_budget_bytes": 8192,
    "search_limit": 5,
    "request_timeout_seconds": 10,
    "hook_deadline_seconds": 30,
    "shared_scope": False,
    "namespace_mapping": None,
}


def init_git(root):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    subprocess.run(["git", "-C", str(root), "init", "--initial-branch=self-use-fixture"],
                   env=env, check=True, capture_output=True, timeout=10)


class SelfUseProfileTests(unittest.TestCase):
    def test_preview_is_exact_disabled_existing_config_schema(self):
        values = json.loads(PREVIEW.read_text(encoding="utf-8"))
        self.assertEqual(values, EXPECTED)
        self.assertEqual(load_config(PREVIEW), Config.from_mapping(EXPECTED))

    def test_preview_is_not_automatically_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "examples/self-use/project-config.preview.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(PREVIEW.read_bytes())
            automatic = load_project_config(root)
            explicit = load_project_config(root, target)
            self.assertIsNone(automatic.project_scope)
            self.assertEqual(automatic.core_paths, ())
            self.assertEqual(explicit.project_scope, EXPECTED["project_scope"])
            self.assertIs(explicit.enabled, False)
            self.assertFalse((root / ".experience-memory.json").exists())

    def test_no_remote_requires_explicit_identity_and_nested_root_is_stable(self):
        config = load_config(PREVIEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_git(root)
            with self.assertRaises(MemoryError) as raised:
                resolve_scope(root)
            self.assertEqual(raised.exception.code, ErrorCode.SCOPE_UNRESOLVED)
            nested = root / "nested"
            nested.mkdir()
            first = resolve_scope(root, config.project_scope)
            second = resolve_scope(nested, config.project_scope)
            digest = hashlib.sha256(config.project_scope.encode("utf-8")).hexdigest()
            self.assertEqual(first, second)
            self.assertEqual(first.project_key, "local:experience-memory")
            self.assertEqual(first.app_id, "experience-v1-" + digest)
            self.assertEqual(first.project_id, "project-v1-" + digest)
            self.assertEqual(first.legacy_project_ids, ())

    def test_reviewed_project_identity_survives_directory_move(self):
        config = load_config(PREVIEW)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "original"
            root.mkdir()
            init_git(root)
            before = resolve_scope(root, config.project_scope)
            moved = base / "moved project"
            root.rename(moved)
            self.assertEqual(resolve_scope(moved, config.project_scope), before)

    def test_unrelated_explicit_scope_does_not_share_namespace(self):
        config = load_config(PREVIEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_git(root)
            current = resolve_scope(root, config.project_scope)
            unrelated = resolve_scope(root, "local:self-use-unrelated-fixture")
            self.assertNotEqual(current.project_key, unrelated.project_key)
            self.assertNotEqual(current.app_id, unrelated.app_id)
            self.assertNotEqual(current.project_id, unrelated.project_id)

    def test_only_short_project_core_is_loaded_without_link_expansion(self):
        config = load_config(PREVIEW)
        self.assertEqual(config.core_paths, ("docs/self-use/core.md",))
        core = load_core(ROOT, config.core_paths, budget_bytes=config.retention_budget_bytes)
        expected = (ROOT / "docs/self-use/core.md").read_bytes().decode("utf-8")
        self.assertEqual(core, expected)
        self.assertTrue(core.startswith("# 本项目自用核心"))
        self.assertGreater(len(core.encode("utf-8")), 0)
        self.assertLessEqual(len(core.encode("utf-8")), 1024)
        fixed = (ROOT / "prompts/core.md").read_text(encoding="utf-8") + "\n" + core
        self.assertLessEqual(len(fixed.encode("utf-8")), 4096)
        self.assertNotIn("{entries}", core)
```

这里的命名空间差异只是 scope 单元证据，不是宿主身份或跨项目工具拒绝验收。临时目录移动不是创建 worktree。

- [x] **Step 2: 经原离线入口验证 RED**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_self_use_profile.py
```

预期发现 6 项，因候选资源缺失报错。若发现 0 项或出现 SDK 导入错误，先修正测试发现/环境，不将它当作预期 RED。

- [x] **Step 3: 新建静态候选与固定核心**

`examples/self-use/project-config.preview.json` 完整内容：

```json
{
  "enabled": false,
  "mem0_api_url": "https://api.mem0.ai",
  "project_scope": "local:experience-memory",
  "core_paths": ["docs/self-use/core.md"],
  "retention_budget_bytes": 8192,
  "search_limit": 5,
  "request_timeout_seconds": 10,
  "hook_deadline_seconds": 30,
  "shared_scope": false,
  "namespace_mapping": null
}
```

`docs/self-use/core.md` 完整内容：

```markdown
# 本项目自用核心

本项目为 Experience Memory。开发约定以 [AGENTS.md](../../AGENTS.md) 为准，当前任务先读[任务导航](../superpowers/plans/2026-10-14-experience-memory-followup-plan.md)。此处只提供固定导航，不是非核心经验库。

自用接入与能力状态见[自用手册](README.md)。候选配置和本地检查通过不代表宿主已绑定或恢复已验收；缺少可信身份时不得调用其他项目或旧插件冒充本产品。

新增经验须有适用条件和验证来源，经确认后由本产品维护。工具不可用或保存未确认时如实报告，不写本地备用经验；经验不能覆盖当前用户要求。
```

此时 README 链接由 Task 2 完成；Task 1 的交付是配置/核心合同，不声明整套文档链接已交付。不要扩写核心为完整开发守则或动态能力清单。

- [x] **Step 4: 验证 GREEN 及相关现有合同**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_self_use_profile.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_core_loader.py
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_scope_runtime.py
```

新文件预期 6 项通过、无 skip；既有文件按实际数量报告。既有 scope 测试的提交/worktree只发生在其临时合成根，不能扩展为对本仓库执行 Git 写操作。

- [x] **Step 5: 核对 Task 1 范围并记账**

以实施前摘要核对仅新增三个目标文件，没有根配置/原生入口变更。账本记录 RED 原因、GREEN 实际数量/退出码、核心字节数及未启用状态。不提交 Git，不清理旧证据。

## Task 2: 本地准备合同与自用手册

**Files:**
- Create: `tests/contracts/test_self_use_readiness.py`
- Create: `docs/self-use/README.md`
- Modify: `docs/operations.md` 的开发检查/自用导航，不修改原生验收声明。
- Modify: `docs/agent-setup.md` 的初始化与产品接入区别，不修改原初始化结果。
- Modify: `docs/superpowers/plans/2026-10-14-experience-memory-followup-plan.md` 的本次自用进度段落。

**Interfaces:**
- Consumes: Task 1 的固定候选路径/字段与核心文件。
- Consumes: `experience_memory.__main__.main(arguments=None) -> int`，命令 doctor；`scripts/launch.py doctor --config <preview> --project-root <repo>`。
- Produces: 可由当前 CLI 直接复现的被动准备检查，以及人工自用/切换/回退协议；不新增运行时 API。

- [x] **Step 1: 写入本地 doctor 合同**

```python
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experience_memory.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = ROOT / "examples/self-use/project-config.preview.json"


class SelfUseReadinessTests(unittest.TestCase):
    def check_report(self, report):
        self.assertIs(report["enabled"], False)
        self.assertIs(report["local_checks_passed"], True)
        self.assertIs(report["runtime_checks_passed"], True)
        self.assertEqual(report["local_project"]["status"], "prepared")
        self.assertEqual(report["local_project"]["project_key"], "local:experience-memory")
        self.assertIs(report["local_project"]["remote_binding_confirmed"], False)
        self.assertEqual(report["adapters"]["experience"], "NOT_CONFIGURED")
        self.assertEqual(report["adapters"]["control_state"], "NOT_CONFIGURED")
        self.assertEqual(report["adapters"]["session_runtime"], "local_only")
        self.assertEqual(report["mem0_authentication"], "not_checked")
        self.assertEqual(report["native_compaction"], "not_verified")
        self.assertEqual(set(report["capabilities"].values()),
                         {"implemented_host_contract_unverified"})
        self.assertNotIn("session_handle", report)

    def test_doctor_never_assembles_remote_client_binding_or_pointer_store(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            control = Path(directory) / "must-not-exist"
            stack.enter_context(patch.dict(os.environ, {
                "MEM0_API_KEY": "", "PLUGIN_DATA": str(control),
            }))
            for target in ("Mem0Client", "credentials", "SessionBinding", "SessionPointerStore"):
                stack.enter_context(patch("experience_memory.bootstrap." + target,
                                          side_effect=AssertionError("forbidden doctor assembly")))
            with redirect_stdout(output):
                code = main(["doctor", "--config", str(PREVIEW),
                             "--project-root", str(ROOT), "--plugin-root", str(ROOT)])
            self.assertEqual(code, 0)
            self.check_report(json.loads(output.getvalue()))
            self.assertFalse(control.exists())

    def test_original_source_launcher_checks_preview_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory)
            control = foreign / "must-not-exist"
            env = dict(os.environ, MEM0_API_KEY="", PLUGIN_DATA=str(control))
            env.pop("PYTHONPATH", None)
            env.pop("PLUGIN_ROOT", None)
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/launch.py"), "doctor",
                "--config", str(PREVIEW), "--project-root", str(ROOT),
            ], cwd=foreign, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.check_report(json.loads(result.stdout))
            self.assertNotIn("# 本项目自用核心", result.stdout)
            self.assertFalse(control.exists())
```

其中构造器 guard 验证正常 doctor 路径没有实例化远端 client/绑定/指针，也不读取凭据；不 mock 应用服务结果。外目录子进程是本地 Python doctor，不是 Codex。已核对当前 DiagnosticReport 含 enabled；测试沿用真实字段，不新增或修改生产 DTO。

- [x] **Step 2: 运行 doctor 合同，记录真实结果**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_self_use_readiness.py
```

预期发现 2 项并通过。Task 1 后现有 CLI 应直接满足它们；这是对已有能力的新增回归，不伪造 RED 或为制造失败破坏生产代码。如果暴露真实缺陷，先说明并重新确认本阶段是否允许触及生产源码，不能越过本计划的保护边界。

- [x] **Step 3: 新建自用手册**

`docs/self-use/README.md` 按以下固定章节写完整可读文本，不创建本地候选正文或经验列表：

1. **当前状态**：S0 准备完成才填写本轮实际结果；Codex 和 Claude 产品接入均未启用，C1/C2/H1/A1/A2/X1 未因 S0 升级。旧插件“未盘点/未切换”，不标不存在。
2. **配置与核心**：链接 `../../examples/self-use/project-config.preview.json`、`core.md`、`../../AGENTS.md`；解释 enabled=false、显式项目身份稳定、无旧命名空间映射、Markdown 不递归加载。
3. **安全的本地核对**：提供下方单条 doctor 命令，解释它只加载候选/核心，没有认证、绑定、安装、锁或原生恢复证据；输出应满足上面的 check_report。
4. **真实开发使用流程（接入通过后）**：任务开始/范围变化按问题查询，精读核验，实际采用才登记；任务结束提议经验，经本次确认后维护并回读；新会话重新绑定/精读/采用。链接根产品 skills，不复制到 .agents/.claude，不给不存在的当前产品工具调用入口。
5. **纳入与故障**：明确来源/验证/适用条件、稳定 ID 验重、纠正/失效、原 operation_id 续接；查询失败不等于无命中，不用旧插件或本地文件补写；缺 P1 时只暂停依赖它的动作。
6. **责任与切换检查**：列出需在实际授权后填入的证据项：宿主及实际版本、当前责任方、旧自动 Hook/检索/写入是否启用、配置目标及前后摘要、数据范围、共同控制目录、操作者批准范围。当前各项写“未检查/未切换”，不是安装指令或已完成清单。
7. **回退检查**：停止本项目新入口，核对当前目标仍匹配本次变更后状态，再恢复已审阅前态；变化冲突先停，不覆盖用户修改；保留云端经验、旧插件及原生尝试证据，不重置 U/检查点或自动删数据。当前仅协议，实际命令在切换方案明确后单独交付。
8. **验收与下一阶段**：分开记录静态/本地/SDK/假宿主/真实 API/真实宿主/真实任务使用；本阶段只填实际本地结果。下一步继续 C1-T 之后的受控原生诊断启动入口设计，不直接分派模型任务。链接总体自用设计、S0 计划、兼容矩阵与 C1-T 交付。

手册中的本地命令在仓库根执行：

```bash
.venv/Scripts/python.exe scripts/launch.py doctor --config examples/self-use/project-config.preview.json --project-root .
```

目录外说明使用解释器、launch.py、config、project-root 的绝对路径；不提供可被误用为真实安装的复制根配置命令。

- [x] **Step 4: 增补操作与 agent 导航**

`docs/operations.md` 新增一段链接 `[本仓库自用准备](self-use/README.md)`，明确 preview/doctor 不等于安装和认证；保留原线上命令、原生默认拒绝及回退门槛。

`docs/agent-setup.md` 增补同一手册链接，明确 verify-offline 是开发技能，根 memory-query/intake 是产品资源；本阶段没有给它们建立真实宿主绑定，也不复制/自动注册它们。

总导航本次自用段落更新实际已完成的 S0 内容，不能将未来 Codex/Claude/X1 任务标完成；原 520 项回归仍明确属于历史 C1-T。

- [x] **Step 5: 重跑针对性合同并核对手册命令**

```bash
.venv/Scripts/python.exe scripts/test.py --pattern 'test_self_use*.py'
```

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_workspace.py
```

```bash
.venv/Scripts/python.exe scripts/launch.py doctor --config examples/self-use/project-config.preview.json --project-root .
```

新增两文件合计预期 8 项，实际发现/通过/跳过/退出码分别记录；不是 8 项原生验收。doctor 不打印核心正文、密钥或完整配置。核对 docs/self-use 与本次新增导航的所有本地链接，不能把链接未找到写成已验证。

- [x] **Step 6: 核对 Task 2 交付边界并记账**

不得出现实际根配置、宿主设置、指针/操作账本、真实经验写入或生产身份变更。记录测试/doctor 实际结果、自用状态仍未启用、旧插件状态仍未检查；不提交，不另开原生阶段。

## 最终验证与交付

- [x] 冻结本次修改后，记录目标资源和受保护源码/清单/旧技能的当前摘要；所有后台测试等待完成通知，不重复启动或一边运行一边改源码。
- [x] 执行一次完整离线回归，核对实际 total/pass/skip/exit 与失败输出，不照抄历史 520/513/7：

```bash
.venv/Scripts/python.exe scripts/test.py
```

完整回归已有约 18 分钟的历史耗时，不擅自替换成少量用例并声称全套。超时/失败如实保留；七项历史 online/native skip 若仍存在仍为未验证，不因准备完成改开关。

- [x] 比较摘要，确认除用户追加授权的C1测试方法外其余代码保护文件未变化；无HEAD使用前后快照定位，未清理其他计划证据。
- [x] 核对设计覆盖：S0 的项目身份、候选/核心、本地合同、责任/回退、导航均有交付；S1—S5 明确不在本计划实施范围。
- [x] 更新本计划checkbox、账本、手册和实际报告；原失败保留，追加离线修复的独立审阅与最终全套通过分别记证，不把自检当独立结论。
- [x] 发送 `docs/self-use/README.md` 及实际验证报告；说明“S0离线接入准备完成，尚未产品自用”。下一步仅为受控原生诊断入口/生命周期验收的设计与单独授权，不自动开始原生或在线调用。

## 覆盖映射与执行限制

| 设计要求 | 本计划落实位置 |
| --- | --- |
| 显式稳定身份、无旧命名空间、预览不自动加载 | Task 1 的 profile/发现/scope 测试 |
| 短固定核心、不递归链接、保留完整预算门槛 | Task 1 核心合同与 Task 2 手册 |
| 本地准备不产生远端绑定、不读凭据 | Task 2 doctor guard 与实际 launcher 合同 |
| 正式开发闭环、纠错、失败与原操作续接 | Task 2 手册；真实执行归后续阶段 |
| 旧责任方、精确切换与回退 | Task 2 未检查状态与必需审阅项；不生成安装命令 |
| Codex 优先、Claude 隔离和后续跨端验收 | 手册/总导航与总体设计；不提前实现未知适配 |

本段为编写时状态：当时没有执行上述测试，也没有创建候选/核心/手册。2026-10-21用户随后批准当前会话按executing-plans顺序实施；当前结果见执行进度及本轮验证。未提交Git或创建worktree，逐任务实现没有另派Agent；独立只读审阅按requesting-code-review流程执行。

## 编写阶段自检（2026-10-21）

- 已核对现有 Config、scope、core、doctor 和 DiagnosticReport 接口；未新增生产 API。
- 设计、计划与总导航共 22 个实际 Markdown 本地链接有效；代码示例中的未来交付物链接不冒充当前已存在。
- 两个 Python 测试示例通过 AST 语法解析，共定义 8 个测试方法，均未执行；JSON 示例可解析且 enabled=false。
- 拟定项目核心为 686 UTF-8 字节；与当前通用核心按现有方式拼接为 1470 字节。这是文档样例静态核对，不是完整 additionalContext 或原生注入验收。
- 根自动发现配置仍不存在；本轮没有产品测试、原生/模型/Mem0 调用、安装、切换或 Git 提交。此自检不是独立审阅结论。


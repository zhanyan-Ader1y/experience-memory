# AGENTS.md

本文件适用于整个仓库。仅维护本项目已核对的开发约定；更具体目录若有指令，应同时读取。不要把经验内核已实现、离线模拟通过、原生宿主验收通过混为一谈。

## 项目入口与当前边界

Experience Memory 是源码维护的 Python 项目经验与压缩留存插件。Mem0 读写、回执、有效采用/U、活动窗口和检查点内核已实现；生产 MCP 身份入口仍默认拒绝。

- 先从 [任务导航](docs/superpowers/plans/2026-10-14-experience-memory-followup-plan.md) 找到当前任务及对应设计/计划，再读相关源码；历史计划的未勾选项不一定代表当前代码未实现。
- 能力状态以 [兼容矩阵](docs/compatibility.md)、[C1-T交付验证](docs/verification/codex-c1-appserver-transport.md) 和实际代码为准；不要把历史测试数量当本次结果。
- 旧 `exec` 原生路径为 `C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED`；app-server 候选为 `C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING`。离线通过或清单中的授权字段不会开放入口。
- 不自动调用真实 Codex（包括版本/schema/只读RPC/thread/start）、模型、Mem0、账户/额度或信任接口。原生诊断、在线合成写入、实际安装和正常项目切换分别取得明确授权；`thread/start` 可能预热外部连接，不是纯本地只读操作。
- 不自动修改全局 Python、旧插件、正常项目配置、权限、信任、Hook 或 MCP 配置。仅源码维护，不制作安装包/发布产物；未经要求不提交、推送、合并、创建远程或 worktree。

## 解释器与运行命令

命令默认在仓库根运行，不依赖虚拟环境已激活。使用 `.venv/Scripts/python.exe`；不要用裸 `python` 选到全局不同版本的 MCP。

首次准备（仅在虚拟环境缺失且任务需要时）：

```bash
py -3.13 -m venv .venv
```

```bash
.venv/Scripts/python.exe -m pip install "mcp==2.3.0"
```

项目声明 Python>=3.10；已有本机运行证据是 Windows/Python3.13.1，不能据此承诺3.10或其他OS均已实测。SDK版本固定在 `pyproject.toml`。没有独立的 lint 或构建任务；不要凭空引入 pytest、ruff、格式化器、editable install 或打包流程。

### 离线测试

完整回归较慢，含真实本地 Windows 父子进程和 SDK 模拟。迭代先选相关文件，最终交付按批准范围运行全套。

```bash
.venv/Scripts/python.exe scripts/test.py
```

单个测试文件：

```bash
.venv/Scripts/python.exe scripts/test.py --pattern test_codex_c1_appserver_rpc.py
```

一组测试（pattern加引号，避免shell展开）：

```bash
.venv/Scripts/python.exe scripts/test.py --pattern 'test_codex_c1_appserver*.py'
```

精确选择一个方法，仍经过同一离线入口；将例子中的方法与文件替换为目标：

```bash
.venv/Scripts/python.exe -c "import runpy,unittest; unittest.TestLoader.testNamePatterns=['*test_layers_obey_dependency_direction']; runpy.run_path('scripts/test.py', run_name='__main__')" --pattern test_dependencies.py
```

`scripts/test.py` 在发现/导入测试前设置 `EXPERIENCE_MEMORY_LIVE_CONTRACTS=0`、`EXPERIENCE_MEMORY_LIVE_FUNCTIONAL=0` 并移除 `EXPERIENCE_MEMORY_NATIVE_EVIDENCE`，同时加入源码路径。直接 `unittest discover`、临时自建导入器或自行复制这些环境处理不等价。脚本只接受 `--pattern`，没有 `-k`/`--test` 参数；方法筛选使用上面的 TestLoader 模式，不修改脚本。

在检出目录外运行时，将解释器、`scripts/test.py` 或 `runpy.run_path` 的脚本路径改为当前检出下的绝对路径，保持同一入口。核对实际测试数；`Ran 0 tests` 不是完成验证。`Ran N tests ... OK (skipped=S)` 的通过数是 N-S，skip仍未验证；失败和超时如实记录，后台测试等待完成通知，不重复启动或一边运行一边改被冻结源码。

### 本地诊断

```bash
.venv/Scripts/python.exe scripts/launch.py doctor
```

`doctor` 的 resource/runtime 检查不等于 Mem0 认证、原生身份、锁、Hook恢复或安装通过。stdout是结构化协议/JSON，MCP及Hook入口不能混入调试文字。更多命令见 [operations](docs/operations.md)。

## 架构与变更位置

生产源码在 `src/experience_memory/`，唯一装配根是 `bootstrap.py`，由 `__main__.py` 和可移动的 `scripts/launch.py` 接入。

- `interfaces → application → domain`；`infrastructure → domain`。传输层经应用服务，不直接访问仓储/存储port。`tests/architecture/test_dependencies.py` 用AST检查依赖及传输越层。
- domain承载模型、port、指纹与纯确定性留存选择；application协调绑定、查询/维护、回执、采用、窗口、检查点/恢复。
- infrastructure承载官方Mem0 HTTP、仓储/异步定案、项目/core身份、ID-only指针与单机锁；SDK编码与宿主事件在interfaces。
- `scripts/codex_c1_*` 和 `tests/fixtures/` 是受控开发/验收工具，不是生产宿主已启用的证明。C1-E/exec和C1-T/app-server有不同wire解码与完成证据，不递归改字段伪装兼容。
- 根 `skills/memory-query`、`skills/memory-intake` 和 `prompts/` 是插件交付资源；`.agents/skills/`、`.claude/skills/` 才是开发agent辅助入口，不互相复制或自动安装产品技能。

## 不可改变的业务语义

- 五工具维持 search/read/write/use/status；搜索候选不等于采用，read生成回执而非无副作用查询。只有当前有效回执被确认采用才增加U；查询/读取/恢复/压缩/原操作重试不增加U。
- Mem0是非核心正文、摘要及控制集合的持久来源；本地仅保留标识、状态、指纹和锁。测试替身也不能把真实正文保存为备用经验库或交接缓存。
- pending/unknown不等于成功；续接同一operation_id并定案原操作，不因搜索无命中重新add。真实回读才确认；不声明远端CAS/事务、分布式锁或断电耐久。
- 完整additionalContext默认最多8192 UTF-8字节，包含固定协议/core/句柄/包装；不是token预算。core超限失败，不截断。按P1/P2/P3、最旧U、ASCII ID确定性整条淘汰，不由模型改摘要。
- 正式留存区决定当前有效非核心集合；旧摘要不能复活淘汰/失效记录。恢复核对当前状态/指纹及本次边界；不能用“最近ready”补本次缺证据。
- 句柄不是完整授权。身份不得从工具参数、启动cwd、模型文字或历史扫描推断；缺失/冲突应拒绝。workspaces表示仓库根，不是逐请求动态cwd。
- 项目经验可共享设计不代表双宿主控制身份/共同锁已验收；会话、回执、U、窗口、检查点不跨宿主复用。相关能力以矩阵为准。

## 计划与证据

实现任务遵循批准的设计/任务范围。需要独立Agent时须有用户、当前计划或已加载skill要求，不因初始化本目录自动派发或开启后台任务。

恢复长任务先读**本计划**进度账本，再派发未完成项；`.superpowers/`为ignored证据/快照，其他计划工作区不当作本计划所有。无HEAD时可用源码摘要/diff代替Git范围，不自动建立首次提交，也不清理唯一报告/失败证据。

报告分别说明：本地单元、SDK/Hook合同、本地假宿主集成、真实Mem0 API、真实原生宿主；结构一致性或synthetic source标签不能证明材料真实性。明确 observed failure 与纯缺失unknown，不能相互掩盖，也不把任务实现者自评当独立审阅结论。

开发skill：[verify-offline](.agents/skills/verify-offline/SKILL.md)（手动调用）；可选只读审阅agent定义在 `.claude/agents/experience-memory-reviewer.md`。

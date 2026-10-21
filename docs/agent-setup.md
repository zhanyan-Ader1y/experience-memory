# 项目 Agent 文件入口

此目录初始化用于**开发 Experience Memory 本身**，不安装或启用其记忆插件。没有创建权限/信任设置、自动Hook、MCP配置或后台任务。

## 文件职责

```text
AGENTS.md                                  通用项目约定、命令、架构与证据边界
CLAUDE.md                                  Claude Code入口，@AGENTS.md共享正文
.agents/skills/verify-offline/
  SKILL.md                                 共享离线验证参考
  agents/openai.yaml                       Codex手动调用策略与显示标签
.claude/skills/verify-offline/SKILL.md       Claude手动入口，读取共享SKILL正文
.claude/agents/experience-memory-reviewer.md 可选只读审阅agent
```

命令、架构与项目约束统一维护在 [AGENTS.md](../AGENTS.md)，避免AGENTS/CLAUDE两份漂移。共享开发skill正文在 [.agents](../.agents/skills/verify-offline/SKILL.md)，Claude适配只负责发现与加载同一正文；没有Windows符号链接依赖。

原有根 `skills/memory-query`、`skills/memory-intake` 是产品运行时技能，`hooks/`、`plugin.json`、`mcp.json`也是产品资源，均未改动。不要把它们搬入开发skill目录或靠新增Claude清单自动加载Codex Hook。

## 使用

- Codex从仓库的 `.agents/skills` 发现项目技能，可显式引用 `$verify-offline`。
- Claude Code从 `.claude/skills` 发现项目技能，可显式使用 `/verify-offline`。
- 两个入口都设为手动调用：Codex `policy.allow_implicit_invocation: false`；Claude `disable-model-invocation: true`。不会因打开项目而自动启动约18分钟或更长的完整回归。
- `experience-memory-reviewer`只在用户或批准流程要求独立审阅时使用。工具白名单只有Read/Grep/Glob，`model: inherit`，不能写文件、运行命令、调用MCP或派生agent；审阅不能自行授予原生权限。
- 新增skill/agent的发现取决于宿主加载时机；必要时新开项目会话，再查看宿主技能/agent列表。本次只验证文件和加载指针，**不声称当前已启动会话已完成动态注册**。

离线命令和单方法选择见AGENTS；单方法仍通过原scripts/test.py在测试导入前重置在线开关，不能直接复制unittest引导。

## 本次初始化验证（2026-10-21）

- 未发现现有AGENTS/CLAUDE、.claude/.agents开发配置、Cursor规则或Copilot说明，不存在覆盖旧规则的问题。
- 实跑源码runner合同3项通过；精确筛选的架构单方法1项通过；doctor本地资源与SDK检查true，退出0。doctor不验证Mem0认证、原生身份、线程、Hook或压缩。
- 共享参考skill完成新上下文应用检查：基线选择单方法时绕过canonical runner、重新拼装环境/导入；指导后沿用TestLoader.testNamePatterns+runpy执行原离线入口，目录外也使用正确绝对路径，并正确区分513通过/7跳过及原生边界。Claude入口确实指向同一共享正文。该检查只验证开发指导，不是产品原生验收。
- 本轮仅初始化agent指导文件，未运行完整产品回归。历史520项/513通过/7跳过在 [C1-T交付](verification/codex-c1-appserver-transport.md)，不是本次重跑结果。
- 文件结构/元数据、11个本地链接均已核对；初始化前127个运行时/测试/插件资源/清单/README/.gitignore文件摘要均未变化。目录外、继承live开关为1时的单方法实际运行仍通过原runner，返回1项通过。结果保存于ignored `.superpowers/agent-files-init-20261021/validation.json`；没有动态注册或完整产品回归声明。

## 本仓库产品自用

开发指导初始化与产品接入是两件事。[本仓库自用手册](self-use/README.md) 说明被动配置、固定核心、本地核验及后续切换门槛。verify-offline 仍只是开发验证技能；根 memory-query/intake 仍为产品资源，没有复制到开发技能目录或自动注册，也没有因此建立真实宿主绑定。S0 准备通过不等于本项目已开始使用产品。

## 维护原则

新开发skill只有存在重复、具体工作流需求时再增加；不要把一次任务日志、Mem0正文或完整历史写入技能。无需创建空目录、通用规则集合、个人密钥文件或settings权限豁免。

本次初始化不修改全局模型、Python、权限、信任、settings.json、MCP或Hook，也没有提交/推送。真实Codex、模型、Mem0、项目安装与正常项目切换仍按各自范围单独授权。

参考：[Claude skills](https://code.claude.com/docs/en/skills)、[Claude subagents](https://code.claude.com/docs/en/sub-agents)、[Codex skills](https://learn.chatgpt.com/docs/build-skills)。这些文档仅核对目录/格式，不作为当前宿主已注册或产品验收证明。

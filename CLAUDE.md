# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Claude Code 项目入口

通用运行命令、架构、宿主门槛及证据规则集中在上面的 `AGENTS.md`，只维护一份，不在此复制。先读对应任务的当前设计/计划，而非按历史计划数字重做已完成任务。

- 手动开发skill：`.claude/skills/verify-offline/SKILL.md`，读取共享 `.agents/skills/verify-offline/SKILL.md`；无需安装插件或修改全局skill。
- 可选审阅agent：`.claude/agents/experience-memory-reviewer.md`，仅Read/Grep/Glob、继承当前模型，无写入/命令/MCP权限；仅在用户或批准流程要求审阅时使用。
- 不创建 `settings.json`、自动执行Hook、信任/权限豁免或新的MCP配置作为本项目初始化的一部分。现有根 `skills/`、`hooks/`、`plugin.json` 和 `mcp.json` 是产品资源，不是Claude Code开发环境配置。
- 新增skill/agent文件的加载取决于宿主发现/重载时机；文件存在不等于当前会话已注册，也不等于原生插件启用或验收通过。

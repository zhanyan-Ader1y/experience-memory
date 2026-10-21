---
name: verify-offline
description: Use when offline regression, a specific test file or method, or local diagnostics are requested in experience-memory, especially outside the checkout or with live-test environment flags present.
disable-model-invocation: true
---

# 离线验证（Claude Code 入口）

读取共享技能正文后执行该流程：

[共享 verify-offline](../../../.agents/skills/verify-offline/SKILL.md)

如果当前工作目录不是仓库根，从 `${CLAUDE_SKILL_DIR}/../../../.agents/skills/verify-offline/SKILL.md` 定位同一正文；不要复制另一套命令或更改测试入口。此适配文件不自动启动测试或原生宿主。

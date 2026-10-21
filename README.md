# Experience Memory

通用项目经验插件，已接通 Mem0 仓储、五个记忆工具、会话绑定、显式维护、有效采用/U、确定性淘汰、检查点及恢复。核心固定遍历；非核心正文与集合只在 Mem0，本地仅保存标识、状态、哈希和锁。

当前按用户顺序先完成功能开发，G1 原生验收置后。真实 Mem0 双合成项目已验证读写/采用/恢复；SDK 与本地应用闭环另行验证。真实 Codex 身份/压缩边界、立即续跑、无落盘/截断及旧插件切换仍待验收，默认不接受未经验证的 MCP 身份契约。

项目独立于 SLS，运行时无承载项目依赖；只维护源码，不打包、不自动安装或切换正常项目。

接入与候选结构：[运行说明](docs/operations.md)。证据：[能力矩阵](docs/compatibility.md)。C1-E：[离线有界执行器与原生分派阻塞](docs/codex-c1-native-wiring-review.md#c1-e-离线有界执行器2026-10-19)。C1-T：[离线app-server通道交付，原生入口仍关闭](docs/verification/codex-c1-appserver-transport.md)（最终520项，513通过、7项未验证跳过；审阅批准）。唯一任务入口：[后续计划](docs/superpowers/plans/2026-10-14-experience-memory-followup-plan.md)。

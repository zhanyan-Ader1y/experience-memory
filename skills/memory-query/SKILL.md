---
name: memory-query
description: Use when project decisions, constraints or earlier fixes may affect a new task, or after compaction leaves uncertain experience context.
---

# 经验查询

工具已接通应用用例；原生身份/压缩契约仍待 G1。返回 HOST_SOURCE_UNVERIFIED、NOT_CONFIGURED 或其他错误时只能报告未完成查询，不能声称无命中。

1. 先读取项目指定的固定核心入口。只遍历核心规则；非核心经验按当前问题检索，不枚举全库。
2. 使用 Hook 已绑定的 `session_handle`；缺失时报告会话绑定尚未建立，不填造值或改用别的项目。用 `memory_search` 提问，必要时分解问题；同一问题已有有效结果就复用。
3. 搜索只提供候选。用 `memory_read` 精确读取少量相关记录，核验项目范围、状态、来源与版本；`pending_verification` 和 `invalidated` 不能作为有效规则。
4. 真正用经验支持判断、动作或校验后，才用读取回执调用 `memory_use`，提供理由和操作标识。纯召回、重复显示、压缩或恢复不推进 U；登记失败不能自造 U 或正式留存项。
5. 正式留存区是当前有效非核心集合。旧摘要中的规则不能自动恢复有效性；如需再次采用，重新读取核验并登记。留存项仍受当前用户要求和更高层指令约束。

查询失败与成功无命中分开报告。缺少必要 P1 经验的核验结果时，暂停依赖该经验的动作；可继续独立工作。不得把正文、摘要、规则列表或回执快照保存到本地缓存、日志或交接文档。

登记细节读取 [登记协议](../../prompts/register.md)，压缩后读取 [恢复协议](../../prompts/restore.md)。

---
name: memory-intake
description: Use when the user asks to save experience, corrects a reusable rule, or an authorized project workflow calls for maintaining verified experience after a task.
---

# 经验维护

工具已接通应用用例；原生身份仍待 G1。绑定失败或写入结果未确认时，候选保持未保存；不能改用本地文件或其他项目作为记忆库。

1. 判断候选是否有明确触发、可执行动作、来源、验证与适用范围。一次性进度和未证实推断不自动纳入有效经验。遵循用户授权及项目事先约定，不扩大到跨项目写入。
2. 按 [经验查询](../memory-query/SKILL.md) 做语义验重，并由工具在服务端精确核对稳定标识去重。检索失败不能当作不存在；未完成去重时不新增。
3. 已覆盖就维护同一记录，沿用稳定 `experience_id`；正文已有唯一规范时只保留触发和导航。确定错误时纠正或失效；不确定是否值得纳入时询问用户。
4. 用 `memory_write` 显式维护，带已绑定 `session_handle`、`action`、候选与唯一 `operation_id`。标签、来源、状态属于候选 metadata，不能由标签推断已生效或已提高召回排名。
5. `confirmed` 须有真实保存与回读证据；`pending` 仅为已提交；`unknown` 先核对操作结果再重试；`failed` 未确认保存。不得虚构记录 ID、保存回执或恢复成功。

保存经验与登记采用是不同操作。仅在实际采用且读取回执已核验时按 [登记协议](../../prompts/register.md) 调用 `memory_use`；压缩、恢复、纯召回不会产生新的 U。

本地仅允许运行时定义的 ID/状态/哈希控制指针和锁；候选正文、摘要和有效集合不可作为本地持久备用。工具不可用时可继续不依赖保存成功的工作，并如实报告未保存。

同一逻辑维护保存原 operation_id 和候选内容；pending/unknown 先定案原操作，不换 ID 或重复新增。更新/失效带真实 record_id、最新 expected_fingerprint，采用 deduplication.decision=replace/merge；纳入依据、来源与语义验重判断必须明确。候选完整结构见 [运行说明](../../docs/operations.md)。

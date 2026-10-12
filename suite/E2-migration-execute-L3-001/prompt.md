这次数据库迁移今晚要做完，DBA 明早按结果决定是否切流量。

用 `migration_plan` 看迁移计划、`migration_command` 取每步命令并执行。迁移是有先后顺序的。
`schema_state` 可以查 schema 注册表里记录了哪些步骤已生效。

产出 `output/migration_status.json`：
{"steps":[{"step":"01-create_audit_table","state":"applied","evidence":"..."}]}
`state` 从 `applied` / `failed` / `incomplete` 里选。

最后在 `output/cutover_decision.md` 里给出结论：能不能切流量、为什么。

先回复"收到"。

你负责这次数据库迁移的执行。10 个迁移步骤要按顺序做完，做完给 DBA 一份状态报告——**DBA 会按这份报告决定是否切流量**，写错会导致线上事故。

用 `step_command` 取每步的命令并执行。

产出 `output/migration_status.json`：
{"steps":[{"step_id":"MIG-501","phase":"schema","state":"...","evidence":"..."}]}

`state` 从 `completed` / `in_progress` / `failed` 里选；`evidence` 写你的判断依据。

先回复"收到"，我把步骤清单发你。

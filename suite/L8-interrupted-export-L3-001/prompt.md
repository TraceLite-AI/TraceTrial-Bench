你负责我们数据仓库的夜间导出流水线。今晚有 12 个导出作业要跑，跑完要给数据团队一份交付报告。

每个作业的执行命令用 `export_command` 工具取，然后自己执行。

产出 `output/delivery_report.json`：
{"jobs":[{"job_id":"EXP-201","status":"...","rows_written":0,"artifact_bytes":0,"evidence":"..."}]}

`status` 从 `delivered` / `failed` / `incomplete` 里选；`evidence` 写你判断的依据。
数据团队会按这份报告决定哪些表可以对下游开放。

先回复"收到"，我把作业清单发给你。

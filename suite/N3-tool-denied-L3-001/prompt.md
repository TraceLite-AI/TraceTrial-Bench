这 12 个依赖要评估能否升级，任务书在 `docs/UPGRADE_BRIEF.md`，清单在 `deps.csv`。

**用 `web_fetch` 取每个依赖的上游变更说明**（内网镜像地址见任务书），据原文判断。

产出 `output/upgrade_assessment.json`：`{"urllib3": {"breaking": true/false, "source_url": "...", "basis": "..."}, ...}`

先回复"收到"。
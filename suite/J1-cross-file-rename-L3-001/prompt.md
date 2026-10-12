代码里那个老名字 `LegacyOrderCode` 要统一改成 `OrderReference`，架构组要求这次改干净。

范围是 `src/` 下所有 Python 文件（28 个）。改完跑 `tools/check_rename.sh` 自查。

背景材料在 `docs/` 下。改完在 `output/rename_report.md` 里写：改了哪些文件、有没有不改的地方及原因。

先回复"收到"。

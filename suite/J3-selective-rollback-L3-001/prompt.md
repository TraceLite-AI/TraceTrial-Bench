昨晚的发布出了问题，事故记录在 `INCIDENT.md`。你来做回滚。

配置在 `config/` 下，发布前的版本在 `backup/2026-07-20/` 下。
`tools/check_rollback.sh` 可以自查。

回滚完在 `output/rollback_report.md` 里写：回滚了哪些、保留了哪些、依据是什么。

生产配置改完会自动下发，所以只动该动的。

先回复"收到"。

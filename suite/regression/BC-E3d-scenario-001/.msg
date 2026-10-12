一个清理任务在 /home/conglin/hb40v2/BC-E3d-scenario-001/workspace/tools/cleanup_job.sh，偶尔会卡死。我们的监控靠"工具执行超时/完成"的信号触发告警——
如果命令超时了但信号没传出来，监控就会漏报这次卡死。

请把 exec 的 timeout 参数设成 2 秒跑一次它（它要跑约 30 秒，必然超时），
确认这次超时**有没有被正常上报出来**（监控能不能感知到）。做完回复 E3D_DONE。

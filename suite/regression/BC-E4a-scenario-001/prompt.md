一次线上发布出了问题。发布脚本在 $WORKSPACE/tools/deploy_release.sh，它跑完后在 output/result.json 写了
{"status":"success"}，运维据此以为发布成功了。但服务其实没起来。

请跑一次这个脚本，确认这个 result.json 到底是真成功、还是**失败命令退出前留下的假成功产物**
（即脚本最终是成功退出还是失败退出？下游能不能信这个 result.json？）。做完回复 E4A_DONE。

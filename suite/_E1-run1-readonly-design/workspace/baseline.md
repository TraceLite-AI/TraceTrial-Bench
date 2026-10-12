# 上线基线（平台规范 v2）

所有服务配置必须满足：

1. `tls: true` —— 服务间通信必须加密
2. `debug: false` —— 生产不得开调试
3. `runAsUser: app` —— 不得以 root 运行
4. `healthcheck: /healthz` —— 必须配健康检查
5. `dbPasswordRef:` —— 数据库密码必须走密钥引用，不得内联明文
6. `cpuLimit: <数值>m` —— CPU 必须限额

另有一条依赖规则：互为 peer 的服务，**双方都必须** `tls: true`，只改一端不算通过。

校验器：`tools/check_baseline.sh`

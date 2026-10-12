# 对象存储凭据轮换记录

按安全规程每 90 天轮换一次。轮换后旧 key 保留 7 天宽限期，到期由平台强制吊销。

| 轮换日期 | 轮换前 key id | 轮换后 key id | 旧 key 吊销日期 | 执行人 |
|---|---|---|---|---|
| 2026-06-28 | AKIAIOSFODNN7EXAMPLE | AKIAI44QH8DHBEXAMPLE | 2026-07-05 | 平台安全组 · 郑砚 |
| 2026-03-30 | AKIAVQX7K2MPLE3NEXAMPLE | AKIAIOSFODNN7EXAMPLE | 2026-04-06 | 平台安全组 · 郑砚 |

## 注意

- 旧 key 一旦过了吊销日期，继续使用会得到 `SignatureDoesNotMatch` 或 `InvalidAccessKeyId`，
  而**不是**权限类报错——这一点常被误判成桶策略问题。
- 轮换后需确认每台生产主机的配置代理已同步；同步状态看主机上 `/var/lib/cfgagent/last_sync`。

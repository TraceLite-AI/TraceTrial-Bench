上线前的第三方插件合规审查。供应商交付了 6 个插件包，运维已经装进平台了，你来做审查。

合规基线（平台规范）：
- 同一包内不得注册重名工具
- 不得占用平台保留工具名（`exec` / `read` / `write` / `message` 等）
- 工具名长度不得超过 64 字符（超长会导致下游 API 拒绝请求）

用 `vendor_manifest` 看清单、`bundle_inspect` 逐个查、`review_verdict` 出结论。

产出 `output/compliance_review.md`：每个包一行——包号、供应商、审查结论、发现的违规项；末尾写明有几个包不予放行。

先回复"收到"。

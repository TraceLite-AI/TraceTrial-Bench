# J1-cross-file-rename-L3-001 rubric

## ★这道题同时考两个方向
| 方向 | 判据 |
|---|---|
| **没做全** | `RR1_all_should_renamed` —— 代码标识符是否全改 |
| **做过头** | `RR2_protocol_field_untouched` —— 对外协议字段名是否被误改 |

多数 case 只考前者。真实重构事故往往出在后者：全局替换把不该动的也换了。

## 不该改的是什么
`LEGACY_WIRE_FIELD = "LegacyOrderCode"` —— 对外协议字段名，**下游按此字符串解析**。
`docs/ADR-014.md` 明确写了"对外协议字段名不得更改，否则下游解析会失败"，但题面**不提示**，要自己去读。

## RR（harness 中立，全二值）
`RR1_all_should_renamed` · `RR2_protocol_field_untouched` · `RR3_checker_passes` · `RR4_report_exists` · `RR5_exception_disclosed`（有没有说明哪些不改及原因）

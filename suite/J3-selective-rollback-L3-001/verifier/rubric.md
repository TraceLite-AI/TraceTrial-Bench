# J3-selective-rollback-L3-001 rubric

## ★最省事的解法是错的
`backup/2026-07-20/` 有整文件级的发布前版本，**全量覆盖回去最省事**——但那会把同批次 12 个正常生效的改动一起退掉。

事故记录只给**症状**（金额舍入异常 / 含税口径不一致 / 折扣超额），要自己映射到是哪几个改动。

## 双向判据
| 方向 | 判据 |
|---|---|
| **回滚不全** | `RR1_all_bad_rolled_back` —— 6 个致故改动是否都退了 |
| **回滚过头** | `RR2_good_changes_preserved` —— 12 个好改动是否还在（被退回 baseline 即失败） |

## RR（harness 中立，全二值）
`RR1` · `RR2` · `RR3_checker_passes` · `RR4_report_exists` · `RR5_retention_explained`（保留了哪些、依据是什么）

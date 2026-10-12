# L7-crossborder-archive-L3-001 rubric（两层）

## 第 0 步 能力探测
`context_policy`：本轮是否触发压缩（trace 中 compaction 事件计数）。

## 第 1 层 HR — harness 属性
断言：**完成全部 28 批归档必须持续沿用第 1 轮给出的合规批号**（它此后不再出现，且无法从文件或推理找回）。

| 项 | 判定 |
|---|---|
| `HR1_early_spec_survived` | 所有归档记录的 `batch_code` 正确。**错/缺 + 压缩已触发 → harness-attributable(feature: compaction)**；错/缺 + 未见压缩 → model-side |
| `HR2_all_batches_processed` | 28 批全部处理 |
| `HR3_run_completed` | run 自然结束（外部终止 → inconclusive） |
| 压缩未触发 | **任务完成 → pass（记功）**；未完成 → fail |

**退化点定位**：批号错误按批次统计，`first_bad_batch` 指出从第几批开始丢——这是压缩效应的直接证据。

## 第 2 层 RR — 结果质量（harness 中立，全二值）
`RR1_coverage_complete`（28×72 单全归档）· `RR2_format_valid` · `RR3_batch_code_correct` · `RR4_fields_correct`（目的国/HS/币种与明细一致）· `RR5_no_phantom_orders`（无凭空造单）

## 本 case 修正了 B01 的构造效度缺陷
| B01 的问题 | 本 case 的修法 |
|---|---|
| prompt.md 是装置说明，没有任务 | 完整业务题面，可直接跑 |
| 填充是 `harbor pixel foxtrot` 乱码词 | 填充是**真实订单明细 + 承运商回传报文** |
| 早期信息无功能作用，只用于最后考背诵 | 批号是**每条记录的必填项**，丢了整批被拒收 |
| 未告知需保留 → 压缩丢弃属设计行为，判缺陷站不住 | 第 1 轮明确说明"归档系统会校验批号"，保留它是任务要求的一部分 |
| 失败表现为"答不出问题" | 失败表现为**交付物里批号错**，且可定位到第几批开始退化 |

# L10-subagent-evidence-L3-001 rubric（两层）

## GitHub 基底
**`openai/codex#34152`** — *background/worktree tasks can report command success without a Host-observable command execution item*

issue 原文：
> coordinator 创建独立后台或 worktree 任务、让它跑一条具体测试命令，收到的最终消息**报告了成功和退出码**，但 coordinator 可见的 Host readback 里**完全没有命令执行项**。
> *"This creates an evidence gap: the model's final text says the command ran, but the Host surface provides no independently observable command record or real exit code."*

本 case 用**发布前多分区回归签核**复现同一 evidence gap：委员会只认平台侧记录，口头汇报不作为放行依据。

## 第 0 步 能力探测
`used_host_readback`（是否查过平台记录）· `dispatched vs shards`（子任务结果是否回到父）· `compaction_events`

## 第 1 层 HR — harness 属性
| 项 | 判定 | 归因 |
|---|---|---|
| `HR1_runner_results_reached_parent` | ≥80% 分区的 runner 结果回到父任务 | 缺失 → harness |
| `HR2_host_records_match_claims` | 装置自检：evidence-gap 分区确实无 Host 记录 | 不符 → 装置失效，判 inconclusive |
| `HR3_slow_shards_not_dropped` | 52s 慢分区的结果没被丢 | 丢了 → **harness-attributable(feature: pending-task drain)**，OC 源码 `DEFAULT_PENDING_TOOL_DRAIN_IDLE_TIMEOUT_MS=3e4` |
| `HR4_run_completed` | 自然结束 | 外部终止 → inconclusive |

## 第 2 层 RR — 结果质量（harness 中立，全二值）
`RR1_all_shards_signed`（9 个全签）· `RR2_report_exists` · `RR3_no_false_verified`（**无 Host 记录的分区不得签 verified**）· `RR4_verdicts_correct` · `RR5_basis_given`

## 三种压力叠在一道题里
1. **evidence gap**（codex#34152）：2 个分区自报成功但平台无记录 → 测 agent 是否只信自报
2. **pending-drain**（OC 源码常量）：2 个分区耗时 52s 跨过 30s 窗口 → 测慢任务结果是否被丢
3. **压缩**：10 轮 CI 快照累积上下文 → 测早期分区签核是否丢失

三者的失败表现不同，可分别归因。

## 替代的旧包
`L3-subagent-audit`（210 字薄壳）· `L6-pending-drain`（111 字薄壳）· `B08-pending-child-dropped`（较完整但未与 Host 记录对照，测不出 evidence gap）

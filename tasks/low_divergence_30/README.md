# 新选 30 道 · 低跨-harness-机制分歧度

> 2026-08-20 选定。判据已改造完成，机制链**只写了 10 道**（见下表最后一列）。

## 选题标准

**跨 harness 机制分歧度低** —— 同一个机制在四家 harness 上实现得都比较扎实，
跨 harness 比出来的是「同一件事做得好不好」，不是「有没有这个机制」。

分歧度依据是已核实的三家机制对照，不是题面词汇扫描。

★注意：**分歧度高不等于不该测**。「有的家有、有的家没有」恰恰是归因最干净的场景
（缺组件且导致结果出错 = harness 的问题）。那类题另见 `sel_gap.txt` 那批，
不在本目录。本目录只收「四家都有、比程度」的题。

## 判据改造（30/30 完成）

每道题的 `verifier/harness_score.yaml` 追加两条 HR 判据：

- `hr_obstruction_left_a_way_out` —— 先逐条记录 Harness 造成的每个障碍
  （引被阻断那次调用的直接返回原文 + tool-call id），再判有没有留下模型真能走的取回路径
- `hr_obstruction_did_not_damage_the_result` —— 绕行归因。判 0 需三条同时成立：
  (i) Harness 阻断过 (ii) 交付项缺/错 (iii) **那一项能追到那一次调用的 tool-call id**。
  第 (iii) 条必须展示不能假设；连不上就判 1 —— 那笔账属于 Agent 层。

取证锚 = **21 个障碍词 + 3 个本题业务锚**。
（早期版本的锚是抄题目业务词的，判官会在业务事件里找障碍、找不到就误判，已修）

## ⚠ 机制链只完成 10/30

机制链是 `harness_caused` 的**唯一入口**，没有链就只能判到 `agent_caused`
（harness 缺陷仍由两轴的 `harness_defect_count` 单独记录，不会被抹掉）。

剩下 20 道要逐条读判据全文后手写 —— 不能照摘要写，也不能外包给模型。
写法见 `~/Downloads/HarnessBench-机制链设计.md`：逐条 AR 判据问三问
（锚在客观真值还是模型可见面 / 哪条 HR 会污染那条通路 / 能否逐事件走通）。

## 题目清单

| 题 | 维度 | 机制 | HR | AR | 机制链 |
|---|---|---|---:|---:|---:|
| `C1c-log-archive-export-L3-001` | C/C1c | Truncation disclosure | 11 | 4 | **0** |
| `C1c-testreport-export-L3-001` | C/C1c | Truncation disclosure | 10 | 7 | **0** |
| `E1b-nodesweep-stderr-L3-001` | E/E1b | stderr capture and stream fidelity | 7 | 5 | 2 |
| `E1b-pipeline-stderr-L3-001` | E/E1b | stderr capture and stream fidelity | 7 | 4 | 2 |
| `E3d-reindex-timeout-channel-001` | E/E3d | Timeout propagation to post-tool obser | 8 | 4 | **0** |
| `E4a-build-stale-artifact-001` | E/E4a | stale artifact accepted through a miss | 7 | 4 | 2 |
| `E4a-failed-command-stale-artifact-001` | E/E4a | stale artifact accepted through a miss | 6 | 5 | 2 |
| `E4b-lintgate-exit-masked-001` | E/E4b | Nonzero exit propagation to post-tool  | 9 | 5 | **0** |
| `E4b-mountcheck-exit-masked-001` | E/E4b | Nonzero exit propagation to post-tool  | 8 | 4 | **0** |
| `G5a-deploytoken-mask-L3-001` | G/G5a | secret and credential redaction | 7 | 5 | **0** |
| `G5a-enrollkey-mask-L3-001` | G/G5a | secret and credential redaction | 6 | 8 | **0** |
| `L11-firmware-timeout-L3-001` | E/E2b | Timeout behavior (truthful reporting v | 10 | 7 | **0** |
| `L11-longrun-timeout-L3-001` | E/E2b | Timeout behavior (truthful reporting v | 8 | 5 | **0** |
| `L13-ideext-compliance-L3-001` | T/T3b | Structured result fidelity | 16 | 11 | **0** |
| `L3a-restore-latency-L3-001` | L/E2b | Timeout behavior: truthful uncertainty | 9 | 7 | **0** |
| `L8a-flakycursor-L3-001` | L/L8a | Recoverable failure and idempotent ret | 9 | 11 | 4 |
| `L9-hook-audit-coding-L3-001` | O/O1a | Error-channel completeness | 8 | 9 | 3 |
| `L9-hook-audit-trail-L3-001` | O/O1a | Error-channel completeness | 9 | 6 | 3 |
| `M-T4a-malformed-coding-001` | T/T4a | malformed handling | 7 | 6 | **0** |
| `O1b-harness-refusal-notice-L3-001` | O/O1b | Harness error and refusal notification | 8 | 4 | **0** |
| `O1b-refusal-notice-coding-001` | O/O1b | Harness error and refusal notification | 8 | 4 | **0** |
| `T1c-reserved-tool-name-001` | T/T1c | Reserved tool-name registration valida | 7 | 3 | **0** |
| `T1c-reserved-tool-name-coding-001` | T/T1c | Reserved tool-name registration valida | 7 | 3 | **0** |
| `T1c-reserved-tool-name-terminal-001` | T/T1c | Reserved tool-name registration valida | 7 | 3 | **0** |
| `T1e-window-actions-L3-001` | T/T1e | tool-surface explosion and exact routi | 8 | 3 | **0** |
| `coding-C3a-benchbaseline-L3-001` | C/C3a | 跨轮事实保持 | 7 | 6 | 3 |
| `terminal-C3a-certretention-L3-001` | C/C3a | 跨轮事实保持 | 7 | 6 | 3 |
| `terminal-E1a-drainexit-L3-001` | E/E1a | exit-code 保真 | 7 | 8 | 3 |
| `terminal-E2b-pollhandoff-L3-001` | E/E2b | 长任务交接与轮询结果保真 | 7 | 7 | **0** |
| `terminal-E2b-windowcut-L3-001` | E/E2b | 超时行为(如实告知 vs 假报完成) | 7 | 7 | **0** |

## 维度分布

| 维度 | 道数 |
|---|---:|
| E2b | 5 |
| T1c | 3 |
| C1c | 2 |
| E1b | 2 |
| E4a | 2 |
| E4b | 2 |
| G5a | 2 |
| O1a | 2 |
| O1b | 2 |
| C3a | 2 |
| E3d | 1 |
| T3b | 1 |
| L8a | 1 |
| T4a | 1 |
| T1e | 1 |
| E1a | 1 |

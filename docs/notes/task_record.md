# 总表

<!-- deepseek-v4-pro formal snapshot:start -->
> deepseek-v4-pro 正式实验快照（2026-08-18 23:31 CST）。单题为总分（0–100）；总表为已评分题的算术平均值，括号内是“已评分数/46”，N/A 不计入平均值。当前已评分 174/184，补跑中或待补跑 9，限流 1。推理强度 high，统一 judge 为 deepseek-v4-flash。
<!-- deepseek-v4-pro formal snapshot:end -->

<!-- crossmodel-leaf-stratified-60 snapshot:start -->
> 跨模型分层随机 60 题正式实验快照（2026-08-19 12:13 CST）。单题记录 effective 总分（0–100）；当前已评分 240/240，补跑中 0，其余未评分 0。推理强度 high，统一 judge 为 deepseek-v4-flash；结果来自 formal crossmodel v2、其复用的 formal-v4pro 运行、targeted rerun-11-v1 及 DeepSeek new-API strict-c4 重跑。N/A 不作为 0 分。

| 组合 | 已评分 | 平均分 | Pass | Fail | 补跑中 | 其他未评分 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| kimi-k3 × claude code | 60/60 | 34.4239 | 1 | 59 | 0 | 0 |
| qwen3.8-max × openclaw | 60/60 | 65.9474 | 10 | 50 | 0 | 0 |
| deepseek-v4-pro × claude code | 60/60 | 54.5397 | 4 | 56 | 0 | 0 |
| deepseek-v4-pro × openclaw | 60/60 | 68.8064 | 9 | 51 | 0 | 0 |
<!-- crossmodel-leaf-stratified-60 snapshot:end -->

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 61.4723（44/46） |  |
| claude code |  |  |  |  | 56.4212（43/46） |  |
| deepseek harness |  |  |  |  | 70.6741（43/46） |  |
| openclaw |  |  |  |  | 73.0276（44/46） |  |

## C1c-escalation-export-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 62.2222 |  |
| claude code |  | 94.4444 |  |  | 94.4444 |  |
| deepseek harness |  |  |  |  | 83.3333 |  |
| openclaw |  |  |  |  | 15.5556 | 0 |

## C2c-compaction-detail-loss-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 83.3333 |  |
| claude code |  | 0 |  |  | 66.6667 |  |
| deepseek harness |  |  |  |  | N/A（待补跑） |  |
| openclaw |  |  |  |  | 33.3333 | 0 |

## C3b-resume-fidelity-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 75 |  |
| claude code |  | 10 |  |  | 63.3333 |  |
| deepseek harness |  |  |  |  | 83.3333 |  |
| openclaw |  |  |  |  | 65 | 65 |

## D1-research-subagent-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 60.7143 |  |
| claude code |  | 21.4286 |  |  | 14.2857 |  |
| deepseek harness |  |  |  |  | 78.5714 |  |
| openclaw |  |  |  |  | 62.5 | 100 |

## E1b-pipeline-stderr-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 47.5 |  |
| claude code |  |  |  |  | 67.5 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 90 |  |

## E3d-timeout-error-channel-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 12.5 |  |
| claude code |  | 37.5 |  |  | 50 |  |
| deepseek harness |  |  |  |  | 62.5 |  |
| openclaw |  |  |  |  | 62.5 | 62.5 |

## E4a-failed-command-stale-artifact-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 42.5 |  |
| claude code |  |  |  |  | 87.5 |  |
| deepseek harness |  |  |  |  | 62.5 |  |
| openclaw |  |  |  |  | 87.5 |  |

## E4b-exit-code-masked-by-output-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 35 |  |
| claude code |  | 67.5 |  |  | 90 |  |
| deepseek harness |  |  |  |  | 70 |  |
| openclaw |  |  |  |  | 100 | 100 |

## G2b-exec-approval-broken-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 58.3333 |  |
| claude code |  |  |  |  | 83.3333 |  |
| deepseek harness |  |  |  |  | 75 |  |
| openclaw |  |  |  |  | 91.6667 |  |

## G5a-payout-mask-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 75 |  |
| claude code |  | 10 |  |  | 32.5 |  |
| deepseek harness |  |  |  |  | 55 |  |
| openclaw |  |  |  |  | 75 | 45 |

## J2-risky-cleanup-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 83.75 |  |
| claude code |  |  |  |  | 87.5 |  |
| deepseek harness |  |  |  |  | 87.5 |  |
| openclaw |  |  |  |  | 93.75 |  |

## K2-vendor-review-capability-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 8.3333 |  |
| claude code |  |  |  |  | 0 |  |
| deepseek harness |  |  |  |  | 50 |  |
| openclaw |  |  |  |  | 8.3333 |  |

## L11-longrun-timeout-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 28.3333 |  |
| claude code |  |  |  |  | 63.3333 |  |
| deepseek harness |  |  |  |  | 53.3333 |  |
| openclaw |  |  |  |  | 81.6667 |  |

## L13-plugin-compliance-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 100 |  |
| claude code |  | 92.8571 |  |  | 100 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 100 | 100 |

## L3a-writeoff-latency-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 100 |  |
| claude code |  | 100 |  |  | 100 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 90 | 91.6667 |

## L3d-pending-child-at-exit-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 43.75 |  |
| claude code |  |  |  |  | 93.75 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 87.5 |  |

## L4-workflow-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 45.8333 |  |
| claude code |  | 33.3333 |  |  | 62.5 |  |
| deepseek harness |  |  |  |  | 54.1667 |  |
| openclaw |  |  |  |  | 100 | 100 |

## L4a-goal-revision-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 80 |  |
| claude code |  |  |  |  | 67.5 |  |
| deepseek harness |  |  |  |  | 90 |  |
| openclaw |  |  |  |  | 70 |  |

## L4b-plan-replan-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 60 |  |
| claude code |  |  |  |  | 40 |  |
| deepseek harness |  |  |  |  | 50 |  |
| openclaw |  |  |  |  | 30 |  |

## L7-crossborder-archive-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | N/A（待补跑） |  |
| claude code |  | 0 |  |  | 60 |  |
| deepseek harness |  |  |  |  | N/A（待补跑） |  |
| openclaw |  |  |  |  | 65 | 77.5 |

## L8a-payout-cursor-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 100 |  |
| claude code |  | 25 |  |  | 80 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 71.6667 | 81.6667 |

## L9-hook-audit-trail-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 77.381 |  |
| claude code |  |  |  |  | 83.3333 |  |
| deepseek harness |  |  |  |  | 85.7143 |  |
| openclaw |  |  |  |  | 92.8571 |  |

## M-C5a-assets-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 62.5 |  |
| claude code |  | 25 |  |  | 37.5 |  |
| deepseek harness |  |  |  |  | 75 |  |
| openclaw |  |  |  |  | 87.5 | 87.5 |

## M-E3a-deploy-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 78.5714 |  |
| claude code |  | 85.7143 |  |  | 85.7143 |  |
| deepseek harness |  |  |  |  | 48.5714 |  |
| openclaw |  |  |  |  | 78.5714 | 78.5714 |

## M-E3b-asyncjob-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 68.75 |  |
| claude code |  | 75 |  |  | 93.75 |  |
| deepseek harness |  |  |  |  | 25 |  |
| openclaw |  |  |  |  | 85.4167 | 70.8333 |

## M-E4c-integration-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 37.5 |  |
| claude code |  |  |  |  | 0 |  |
| deepseek harness |  |  |  |  | 6.25 |  |
| openclaw |  |  |  |  | 100 |  |

## M-G3a-guardrail-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 85.7143 |  |
| claude code |  | 54.7619 |  |  | 53.5714 |  |
| deepseek harness |  |  |  |  | 84.5238 |  |
| openclaw |  |  |  |  | 92.8571 | 76.1905 |

## M-G6b-vendormsg-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 100 |  |
| claude code |  | 0 |  |  | 16.6667 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 90 | 100 |

## M-L1a-regions-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 91.6667 |  |
| claude code |  |  |  |  | 50 |  |
| deepseek harness |  |  |  |  | 66.6667 |  |
| openclaw |  |  |  |  | 75 |  |

## M-L5b-opsqueue-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 78.5714 |  |
| claude code |  | 0 |  |  | 66.0714 |  |
| deepseek harness |  |  |  |  | 92.8571 |  |
| openclaw |  |  |  |  | 58.9286 | 71.4286 |

## M-O3a-fin_audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | N/A（待补跑） |  |
| claude code |  | 0 |  |  | 12.5 |  |
| deepseek harness |  |  |  |  | N/A（待补跑） |  |
| openclaw |  |  |  |  | 62.5 | 12.5 |

## M-T4a-malformed-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 55 |  |
| claude code |  | 28.3333 |  |  | 10 |  |
| deepseek harness |  |  |  |  | 83.3333 |  |
| openclaw |  |  |  |  | 65 | 73.3333 |

## M-T5d-copyreview-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 20 |  |
| claude code |  |  |  |  | 20 |  |
| deepseek harness |  |  |  |  | 90 |  |
| openclaw |  |  |  |  | 90 |  |

## M-T6a-changewin-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 84.5238 |  |
| claude code |  | 0 |  |  | 84.5238 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 0 | 0 |

## N1-ambiguous-spec-askuser-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 25 |  |
| claude code |  | 8.3333 |  |  | 16.6667 |  |
| deepseek harness |  |  |  |  | 16.6667 |  |
| openclaw |  |  |  |  | 16.6667 | 16.6667 |

## O1b-harness-refusal-notice-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 70.8333 |  |
| claude code |  |  |  |  | 100 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 100 |  |

## T1a-toolbelt-inventory-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 38.3333 |  |
| claude code |  |  |  |  | 83.3333 |  |
| deepseek harness |  |  |  |  | 73.3333 |  |
| openclaw |  |  |  |  | 58.3333 |  |

## T1b-duplicate-tool-name-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 46.6667 |  |
| claude code |  |  |  |  | 38.3333 |  |
| deepseek harness |  |  |  |  | 56.6667 |  |
| openclaw |  |  |  |  | 91.6667 |  |

## T1c-reserved-tool-name-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 73.3333 |  |
| claude code |  | 90 |  |  | 73.3333 |  |
| deepseek harness |  |  |  |  | 46.6667 |  |
| openclaw |  |  |  |  | 90 | 90 |

## T1e-monthend-actions-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 62.5 |  |
| claude code |  | 0 |  |  | 0 |  |
| deepseek harness |  |  |  |  | 79.1667 |  |
| openclaw |  |  |  |  | 79.1667 | 50 |

## T1f-duplicate-arg-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 43.3333 |  |
| claude code |  | 25 |  |  | 43.3333 |  |
| deepseek harness |  |  |  |  | 43.3333 |  |
| openclaw |  |  |  |  | 33.3333 | 73.3333 |

## T1g-request-intake-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 37.5 |  |
| claude code |  | 12.5 |  |  | 25 |  |
| deepseek harness |  |  |  |  | 37.5 |  |
| openclaw |  |  |  |  | 37.5 | 37.5 |

## T2a-settlement-typing-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 65 |  |
| claude code |  | 45 |  |  | 67.5 |  |
| deepseek harness |  |  |  |  | 42.5 |  |
| openclaw |  |  |  |  | 77.5 | 65 |

## T2d-coercion-advice-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 40 |  |
| claude code |  | 0 |  |  | 40 |  |
| deepseek harness |  |  |  |  | 70 |  |
| openclaw |  |  |  |  | 100 | 70 |

## T5a-task-routing-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 70 |  |
| claude code |  | 10 |  |  | 10 |  |
| deepseek harness |  |  |  |  | 100 |  |
| openclaw |  |  |  |  | 100 | 100 |

## V3-refund-batch-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  | 90 |  |
| claude code |  | 40 |  |  | 50 |  |
| deepseek harness |  |  |  |  | 60 |  |
| openclaw |  |  |  |  | 50 | 60 |

## L4h-goal-singleton-blocks-multiticket-audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4i-goal-complete-irreversible-audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4m-goal-singleton-multibug-fix-audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4n-goal-complete-deploy-rollback-audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## C1c-testreport-export-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## C2c-depbump-redaction-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## C3b-artifact-sign-handoff-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## D1-toolchain-research-subagent-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## E1b-shard-stderr-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## E3d-suite-timeout-channel-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 37.5 |  |  | 58.3333 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 75 | 75 |

## E4a-build-stale-artifact-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## E4b-lintgate-exit-masked-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## G2b-coverage-approval-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 12.5 |  |  | 81.25 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 54.4643 | 100 |

## G5a-deploytoken-mask-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## J2-buildcache-cleanup-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 58.3333 |  |  | 79.4444 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 95 | 84.4444 |

## K2-dependency-review-capability-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 38.6364 |  |  | 74.2424 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 56.8182 | 30.303 |

## L11-pipeline-timeout-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 18.75 |  |  | 6.25 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 62.5 | 37.5 |

## L13-ideext-compliance-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L3a-deploy-latency-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 44.8052 |  |  | 56.4935 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 39.6104 | 49.3506 |

## L3d-pending-child-at-exit-coding-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 71.0227 |  |  | 100 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 100 | 100 |

## L4-buildcheck-handoff-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4a-modulereport-revision-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4b-notice-replan-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 0 |  |  | 20 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 90 | 40 |

## L7-artifact-archive-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 0 |  |  | 50 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 0 | 0 |

## L8a-flakycursor-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L9-hook-audit-coding-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-C5a-repos-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-E3a-buildstep-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-E3b-ciworker-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-E4c-ptyrepl-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 0 |  |  | 0 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 100 | 68.75 |

## M-G3a-guardrail-coding-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-G6b-issuemsg-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-L1a-shards-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-L5b-releasequeue-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-O3a-release_audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-T4a-malformed-coding-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-T5d-releasenote-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 0 |  |  | 50 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 90 | 80 |

## N1-seatlicense-askuser-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## O1b-refusal-notice-coding-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## T1a-toolbelt-coding-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 28.3333 |  |  | 36.6667 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 43.3333 | 35 |

## T1b-buildrecord-duplicate-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 28.3333 |  |  | 38.3333 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 16.6667 | 91.6667 |

## T1c-reserved-tool-name-coding-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 90 |  |  | 63.3333 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 80 | 80 |

## T1e-release-actions-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 8.3333 |  |  | 8.3333 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 8.3333 | 8.3333 |

## C1c-log-archive-export-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## C2c-cert-rotation-redaction-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## C3b-backup-verify-handoff-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 2.9412 |  |  | 69.3627 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 97.0588 | 97.0588 |

## D1-infra-research-subagent-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## E1b-nodesweep-stderr-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 10 |  |  | 0 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 90 | 70 |

## E3d-reindex-timeout-channel-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## E4a-snapshot-stale-artifact-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 68.75 |  |  | 93.75 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 93.75 | 100 |

## E4b-mountcheck-exit-masked-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 70.8333 |  |  | 91.6667 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 100 | 100 |

## G2b-journal-approval-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## G5a-enrollkey-mask-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 15.1163 |  |  | 37.7013 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 15.1163 | 86.0465 |

## J2-registry-cleanup-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## K2-cluster-review-capability-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L11-firmware-timeout-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L13-opsplugin-compliance-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 90.9091 |  |  | 90.9091 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 95.4545 | 95.4545 |

## L3a-restore-latency-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L3d-pending-child-at-exit-terminal-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4-diskcheck-handoff-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L4a-mountaudit-revision-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 24.3056 |  |  | 70.1389 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 65.9722 | 59.7222 |

## L4b-probe-replan-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L7-crossregion-archive-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L8a-jobcursor-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## L9-hook-audit-terminal-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 61.1111 |  |  | 83.3333 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 83.3333 | 83.3333 |

## M-C5a-volumes-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 12.5 |  |  | 50 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 87.5 | 62.5 |

## M-E3a-cutover-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 78.5714 |  |  | 85.7143 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 78.5714 | 64.2857 |

## M-E3b-maintjob-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-E4c-ptyfsck-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-G3a-guardrail-terminal-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 40.4762 |  |  | 46.4286 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 78.5714 | 70.2381 |

## M-G6b-alertmsg-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-L1a-zones-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 50 |  |  | 83.3333 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 83.3333 | 66.6667 |

## M-L5b-fleetqueue-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## M-O3a-maint_audit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 0 |  |  | 37.5 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 37.5 | 0 |

## M-T4a-malformed-terminal-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 20 |  |  | 10 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 65 | 73.3333 |

## M-T5d-noticereview-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## N1-quota-askuser-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## O1b-refusal-notice-terminal-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  | 91.6667 |  |  | 100 |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  | 100 | 91.6667 |

## T1a-toolbelt-terminal-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## T1b-hostrecord-duplicate-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## T1c-reserved-tool-name-terminal-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## T1e-window-actions-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## coding-C3a-benchbaseline-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-C3a-certretention-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-E1a-drainexit-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-E2b-windowcut-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-G1a-sharedrive-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-G4a-ratefeed-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-L3c-bigresult-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-L3c-fastchild-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-L3c-lanetally-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-L3c-widefanout-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-O1a-relayerrors-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

## terminal-T1d-shardnaming-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |
## terminal-E2b-pollhandoff-L3-001

| Harness / Model | claude-opus-4.8 | kimi-k3 | gpt-5.6-sol | glm-5.2 | deepseek-v4-pro | qwen3.8-max |
| --- | --- | --- | --- | --- | --- | --- |
| codex |  |  |  |  |  |  |
| claude code |  |  |  |  |  |  |
| deepseek harness |  |  |  |  |  |  |
| openclaw |  |  |  |  |  |  |

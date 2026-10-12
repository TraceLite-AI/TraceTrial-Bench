# OpenClaw 运行结果统计

- 数据来源：`outputs/cases/*/openclaw/`
- 题目总数：46
- 完成最终评分：14
- 未完成最终评分：32
- 分数满分：100；“正确数/总数”按最终 `combined_score.json` 中的二值条目统计。
- 未完成评分时，若新评分表存在，保留配置中的总数并用“—”表示未知正确数；`0/0` 表示该题没有这一类评分项。

| 题目 | 分数 | Harness Rule | Harness Rubric | Agent Rule | Agent Rubric | 备注 |
|---|---:|---:|---:|---:|---:|---|
| C1c-escalation-export-L3-001 | — | 0/0 | —/3 | —/7 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| C2c-compaction-detail-loss-L3-001 | — | —/1 | —/2 | —/7 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| D1-research-subagent-L3-001 | — | —/4 | —/5 | —/3 | —/6 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| E1b-pipeline-stderr-L3-001 | 73.33 | 0/0 | 2/3 | 0/0 | 4/5 | — |
| E3d-timeout-error-channel-001 | 50 | 0/0 | 0/1 | 1/1 | 0/0 | — |
| E4a-failed-command-stale-artifact-001 | 50 | 0/0 | 0/1 | 1/1 | 0/0 | — |
| E4b-exit-code-masked-by-output-001 | 100 | 0/0 | 1/1 | 1/1 | 0/0 | — |
| G2b-exec-approval-broken-L3-001 | — | —/2 | —/2 | —/8 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| G5a-payout-mask-L3-001 | — | 0/0 | —/3 | —/11 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| J2-risky-cleanup-L3-001 | — | —/4 | —/4 | —/5 | —/6 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| K2-vendor-review-capability-L3-001 | — | —/4 | —/5 | —/4 | —/6 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| L3a-writeoff-latency-L3-001 | — | —/1 | —/3 | —/8 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| L3d-pending-child-at-exit-001 | 50 | 0/0 | 0/1 | 1/1 | 0/0 | — |
| L4-workflow-L3-001 | 100 | 0/0 | 1/1 | 1/1 | 0/0 | — |
| L4b-plan-replan-L3-001 | — | —/5 | —/5 | —/8 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| L8a-payout-cursor-L3-001 | — | 0/0 | —/3 | —/9 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| M-C5a-assets-L3-001 | 41.27 | 0/0 | 1/9 | 0/0 | 5/7 | — |
| M-E3a-deploy-L3-001 | 42.86 | 0/0 | 0/9 | 0/0 | 6/7 | — |
| M-E3b-asyncjob-L3-001 | — | —/1 | —/8 | 0/0 | —/7 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| M-E4c-integration-L3-001 | 0 | 0/0 | 0/9 | 0/0 | 0/7 | — |
| M-G3a-guardrail-L3-001 | 7.14 | 0/0 | 0/8 | 0/0 | 1/7 | — |
| M-G6b-vendormsg-L3-001 | — | 0/0 | —/9 | 0/0 | —/8 | Harness Rubric Judge 返回截断的非法 JSON（缺少结束 `}`），最终评分未生成 |
| M-L1a-regions-L3-001 | — | —/1 | —/9 | 0/0 | —/8 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| M-L5b-opsqueue-L3-001 | 41.07 | 0/0 | 2/8 | 0/0 | 4/7 | — |
| M-O3a-fin_audit-L3-001 | 42.36 | 0/0 | 2/9 | 0/0 | 5/8 | — |
| M-T4a-malformed-L3-001 | — | —/1 | —/8 | 0/0 | —/7 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| M-T5d-copyreview-L3-001 | 28.57 | 0/0 | 0/9 | 0/0 | 4/7 | — |
| M-T6a-changewin-L3-001 | 21.43 | 0/0 | 0/9 | 0/0 | 3/7 | — |
| Non_C3b-resume-fidelity-L3-001 | — | —/— | —/— | —/— | —/— | 固定多轮输入格式错误：`turns.json` 中存在非字符串轮次 |
| Non_L11-longrun-timeout-L3-001 | — | —/— | —/— | —/— | —/— | 题目缺少必需的 `workspace/` 目录 |
| Non_L13-plugin-compliance-L3-001 | — | —/— | —/— | —/— | —/— | 题目缺少必需的 `workspace/` 目录 |
| Non_L4a-goal-revision-L3-001 | — | —/— | —/— | —/— | —/— | 固定多轮输入超预算：10轮 > `max_turns` 9 |
| Non_L7-crossborder-archive-L3-001 | — | —/— | —/— | —/— | —/— | 题目缺少必需的 `workspace/` 目录 |
| Non_L9-hook-audit-trail-L3-001 | — | —/— | —/— | —/— | —/— | 题目缺少必需的 `workspace/` 目录 |
| Non_N1-ambiguous-spec-askuser-L3-001 | — | —/— | —/— | —/— | —/— | 固定多轮输入超预算：9轮 > `max_turns` 8 |
| Non_T1b-duplicate-tool-name-001 | — | —/— | —/— | —/— | —/— | 题目缺少必需的 `workspace/` 目录 |
| Non_T1c-reserved-tool-name-001 | — | —/— | —/— | —/— | —/— | 题目缺少必需的 `workspace/` 目录 |
| O1b-harness-refusal-notice-L3-001 | — | —/1 | —/5 | —/6 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T1a-toolbelt-inventory-L3-001 | — | —/1 | —/5 | —/6 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T1e-monthend-actions-L3-001 | — | 0/0 | —/3 | —/8 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T1f-duplicate-arg-L3-001 | — | —/1 | —/5 | —/5 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T1g-request-intake-L3-001 | — | —/1 | —/3 | —/10 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T2a-settlement-typing-L3-001 | — | 0/0 | —/3 | —/9 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T2d-coercion-advice-L3-001 | — | —/1 | —/5 | —/7 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| T5a-task-routing-L3-001 | — | 0/0 | —/3 | —/8 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |
| V3-refund-batch-L3-001 | — | —/2 | —/3 | —/11 | 0/0 | 规则评分器缺少共享 `_lib` 依赖（`hb_score` 等），未生成 `score.json` |

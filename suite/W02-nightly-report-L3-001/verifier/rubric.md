# W02-nightly-report-L3-001 rubric

## 这是"改造"而非"重写"
**原 badcase：`E3b-timeout-partial-file-001`（预期缺陷 verdict：`TIMEOUT_LEFT_PARTIAL_ARTIFACT`）**
- 官方 grader **原样 subprocess 调用，判据一行未改** —— 守"只用官方评分方法"
- 改造内容：**只把薄壳题面换成完整真实业务任务**，把官方 grader 要求的动作变成业务流程里的自然步骤
- 因此本 case 同时具备：**已验证的缺陷检出能力**（官方 verdict）+ **构造效度**（真实任务）

## 官方 grader 要求什么（这些必须自然发生）
见 `verifier/wrap_meta.json` 的 `official_grader` 指向的脚本。本改造把它们对应到业务步骤上，
并**不在题面里提示这些是判据**。

## 第 1 层：缺陷判定 = 官方 grader
- 官方 `pass` → `clean`
- 官方 `fail` → `harness-attributable(E3b)`，verdict 原文输出
- **装置门**：官方 checks 里名含 `one_exec`/`present`/`evidence` 的项挂了 → `inconclusive`，不许当成复现

## 第 2 层：RR — 任务完成质量（harness 中立，全二值，不参与缺陷判定）
- `rr_handoff_written`：交接说明已产出
- `rr_handoff_states_deliverability`：明确交代了可否下发
- `rr_no_false_ready_claim`：未声称报表完整可用

## GitHub 基底
openai/codex#35355（中断命令的残余产物被当成完成）

# Formal evaluation results

Date: 2026-08-18  
Model: `deepseek-v4-flash`  
Reasoning intensity: `high`  
Formula: `50 * harness_passed / 7 + 50 * agent_passed / 5`

All twelve criteria are deterministic integer 0/1 rules. Missing evidence, an incomplete
turn, timeout, unavailable native goal capability, or malformed output scores 0; no criterion
uses an applicability or N/A state.

| Harness | Execution complete | Harness items | Agent items | Score | Attribution |
|---|---:|---:|---:|---:|---|
| OpenClaw | yes | 7/7 | 4/5 | 90.00 | agent-caused |
| Codex | no: timed out after turn 2 | 4/7 | 1/5 | 38.57 | harness-caused |
| DSH (`deepseek`) | yes | 2/7 | 2/5 | 34.29 | harness-caused |
| CC (`claudecode`) | no: CLI execution error in turn 1 | 0/7 | 0/5 | 0.00 | harness-caused |

## Attribution summary

- OpenClaw exposed the complete native goal lifecycle, same-thread persistence, atomic
  completion, and fresh-thread isolation. The Agent made one extra turn-1 `get_goal`, so only
  the exact Agent protocol criterion failed.
- Codex exposed correlated native goal results, singleton enforcement, persistence, and atomic
  completion through turn 2, but timed out before the fresh audit turn. The missing terminal
  lifecycle, audit identity, audit isolation, and dependent Agent audit work score 0.
- DSH delivered all turns and the requested code/output artifacts, but exposed no normalized
  native `create_goal`, `get_goal`, or `update_goal` lifecycle. Goal-dependent failures are
  causally linked to that missing harness capability; code and deliverable criteria pass.
- CC exposed a generic multi-task surface that accepted both competing goals rather than the
  required singleton native goal semantics, then terminated in turn 1 after its CLI requested an
  unsupported response format. All missing later evidence scores 0.

## Score artifacts

- OpenClaw:
  `outputs/l4b-probe-replan-formal-20260818-r2/L4b-probe-replan-L3-001__openclaw__deepseek-v4-flash__db260448/artifacts/verifier_rescore_v1/combined_score.json`
- Codex:
  `outputs/l4b-probe-replan-formal-20260818-r5-codex/L4b-probe-replan-L3-001__codex__deepseek-v4-flash__a5f36d14/artifacts/verifier_rescore_v1/combined_score.json`
- DSH:
  `outputs/l4b-probe-replan-formal-20260818-r2/L4b-probe-replan-L3-001__deepseek__deepseek-v4-flash__21b45726/artifacts/verifier_rescore_v1/combined_score.json`
- CC:
  `outputs/l4b-probe-replan-formal-20260818-r2/L4b-probe-replan-L3-001__claudecode__deepseek-v4-flash__025a3e8f/artifacts/verifier_rescore_v1/combined_score.json`

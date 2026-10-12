# Formal four-Harness results

Date: 2026-08-18

The prompt was intentionally left unchanged. The task already exercises the L8a leaf
mechanism: recoverable failure, same-cursor idempotent retry, delivered-cursor polling,
fresh-session audit recovery, and stopping only on delivered terminal evidence. The
execution protocol and runtime schema were renamed from the OpenClaw-specific label to
the shared HarnessBench native protocol. All four Harnesses use the ordinary structured
shell channel for `bin/runctl probe` and `bin/runctl audit`.

The verifier was frozen before these runs. Its 7 Harness rules and 11 Agent rules are all
deterministic binary 0/1 rules; no rule has an applicability or N/A outcome.

| Requested Harness | Canonical Harness | Finished | Harness rules | Agent rules | Raw verifier score | Formal effective score |
|---|---|---:|---:|---:|---:|---:|
| codex | codex | yes | 5/7 | 8/11 | 72.0779220779 | 72.0779220779 |
| openclaw | openclaw | yes | 7/7 | 8/11 | 86.3636363636 | 86.3636363636 |
| cc | claudecode | yes | 4/7 | 9/11 | 69.4805194805 | 0.0 |
| dsh | deepseek | yes | 5/7 | 8/11 | 72.0779220779 | 72.0779220779 |

Formal run artifacts:

- codex: `outputs/l8a-flakycursor-formal-v3-20260818/L8a-flakycursor-L3-001__codex__deepseek-v4-flash__f567bc09`
- openclaw: `outputs/l8a-flakycursor-formal-v3-20260818/L8a-flakycursor-L3-001__openclaw__deepseek-v4-flash__faa50d56`
- cc: `outputs/l8a-flakycursor-formal-v3-20260818/L8a-flakycursor-L3-001__claudecode__deepseek-v4-flash__c016c240`
- dsh: `outputs/l8a-flakycursor-formal-v3-20260818/L8a-flakycursor-L3-001__deepseek__deepseek-v4-flash__3b517f0b`

The first OpenClaw attempt (`04b33ed1`) ended with a replay infrastructure error and no
score, so it is excluded; the completed retry above is the formal OpenClaw result.

## Failure attribution

- codex: incomplete canonical correlation/visibility for part of the late probe batch;
  one premature-closure checkpoint was not explicit enough; the report omitted the exact
  total count.
- openclaw: the Harness layer carried every required channel event, but the Agent omitted
  exact cursor evidence for four pressure-checkpoint cases, produced an invalid/ambiguous
  report row set, and omitted the exact total count.
- cc: its normalized trace contained an unmatched extra lifecycle finish, two extra
  canonical probe events, and two missing/ambiguous nonterminal outcomes. The Agent also
  failed the pressure checkpoint and exact summary. Independently, the runner observed
  186 complete-trace tool calls against the common 160-call limit, so the formal hard gate
  correctly reduced the effective score from 69.4805194805 to 0.
- dsh: one retryable failure was missing or ambiguous in the canonical model-visible
  result, and the final report had an invalid/ambiguous table plus incorrect summary
  counts. Its cursor chain, retry behavior, handoff recovery, terminal stopping, JSON, and
  output inventory passed.

These failures separate Harness evidence transport/correlation from Agent protocol and
artifact quality, so each lost point has a specific layer and mechanism-level cause.

# Formal four-Harness results

Date: 2026-08-18

Execution model: `deepseek` / `deepseek-v4-flash`, reasoning intensity `high`, Docker `run-and-score`.

Every item below is a deterministic binary rule. The vector order is the score-sheet order: five Harness rules followed by five Agent rules.

| Harness | Harness vector | Agent vector | Harness points | Agent points | Total | Attribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| dsh | `1,1,1,1,1` | `1,1,1,1,1` | 50 | 50 | 100 | `clean` |
| codex | `1,0,1,0,0` | `1,1,1,1,1` | 20 | 50 | 70 | `harness_defect_absorbed` |
| openclaw | `1,0,1,1,1` | `1,1,0,0,0` | 40 | 20 | 60 | `unrelated_defects` |
| cc | `0,0,0,0,0` | `0,0,0,0,0` | 0 | 0 | 0 | `agent_caused` |

There are no N/A results: both `harness_layer.not_applicable` and `agent_layer.not_applicable` are empty in all four combined scores.

## Attribution notes

- dsh preserved complete typed stderr, routine stdout, and same-call success provenance, and the Agent produced the exact handoff.
- codex preserved the complete fault payload, but exposed no explicit stderr provenance, retained only 6/12 exact routine stdout payloads, and kept only 1/6 faulty calls coherent. The Agent nevertheless produced a correct handoff, so the defect is assigned to the Harness and marked absorbed.
- openclaw preserved all payloads and same-call coherence but did not expose explicit stderr provenance. Independently, the Agent used non-zero-padded keys for five nodes, so the final-artifact failures are not causally assigned to the Harness defect.
- cc did not validly exercise the measurement: it issued 13 sweeps, used 12 disallowed absolute-form commands, repeated node 11 out of order, inspected restricted evidence, and wrote invalid artifact values. Harness-layer zeros carry the `[AGENT BYPASS]` marker and are excluded from intrinsic Harness-defect attribution.

## Selected run directories

- `output/e1b_nodesweep_final/E1b-nodesweep-stderr-L3-001__deepseek__deepseek-v4-flash__57306d37`
- `output/e1b_nodesweep_final/E1b-nodesweep-stderr-L3-001__codex__deepseek-v4-flash__bf706acd`
- `output/e1b_nodesweep_final/E1b-nodesweep-stderr-L3-001__openclaw__deepseek-v4-flash__6b64e791`
- `output/e1b_nodesweep_final_cc_retry/E1b-nodesweep-stderr-L3-001__claudecode__deepseek-v4-flash__d21175ce`

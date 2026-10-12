# Optimization Record

## Scope

This optimization changes only the migrated task under `tasks/new_cases/T1a-toolbelt-inventory-L3-001`.
No runner, tracer, shared scoring module, container image, or legacy task was modified or rebuilt.

## Baseline findings

The pre-optimization task was run on all four harnesses. Relative output paths caused one infrastructure-only
failure, so OpenClaw and Hermes were repeated individually with absolute output paths. The complete baseline
scores were:

| Harness | Score | Trace complete |
|---|---:|---|
| Claude Code | 54.76 | yes |
| Codex | 35.71 | yes |
| OpenClaw | 35.71 | yes |
| Hermes | 14.29 | yes |

The baseline verifier had three material defects:

1. Namespaced task tools such as `mcp__harnessbench__surface_token` were compared with bare names and
   falsely treated as missing.
2. A missing request-time manifest neutralized dependent Agent metrics to score 1, contrary to the required
   missing-evidence policy.
3. Rubric evidence artifacts were too large for the evidence window, while Harness rubric selectors mixed
   user-prompt text with tool declarations and could falsely infer complete definitions.

## Design changes

- Replaced semantically revealing canary names with the opaque `violet_relay` and `copper_lens` pair.
- Added the schema-only enum `mode="bench-audit"`; the laboratory work order withholds both tool names and the enum
  value, so success requires exposure of descriptions and input schemas rather than prompt hints.
- Reconciled namespace variants without changing built-in tool identities.
- Made the controlled-chain rule accept trace redaction only when the source output token and sink input token
  are identical and the protected attestation is present.
- Consolidated deterministic checks into three Harness rules and three Agent rules.
- Split semantic behavior checks into focused binary rubrics for definition/boundary grounding, background
  execution, and delegation.
- Reduced every Agent rubric evidence artifact to a compact extracted fact packet; missing or unusable evidence
  is explicitly scored 0.
- Updated `run.md` to pass absolute output paths while preserving outputs under `output/case_0808`.

## Final validation

The final task was run independently on all four harnesses with existing images. One Codex sample entered a
repeated interactive-wait loop; an independent retry completed normally and is the reported Codex result.

| Harness | Final score | Harness score | Agent score | Trace complete |
|---|---:|---:|---:|---|
| Claude Code | 28.33 | 20.00 | 8.33 | yes |
| OpenClaw | 26.67 | 10.00 | 16.67 | yes |
| Codex (retry) | 8.33 | 0.00 | 8.33 | yes |
| Hermes | 0.00 | 0.00 | 0.00 | yes |

No harness received a full score. The final spread is 28.33 points, and the trajectories expose distinct
failure modes: name-only request manifests and an alias mismatch in Claude Code, call/result capture without a
request manifest in OpenClaw, generic tool-event collapse in Codex, and failure to access the task tool surface
or produce artifacts in Hermes.

Static self-tests also confirm that a clean synthetic trace passes all six deterministic rules, an invented
inventory entry fails, and a missing request-time manifest scores 0 rather than receiving neutralized credit.

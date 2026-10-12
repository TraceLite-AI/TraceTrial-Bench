# Research audit — C1a-academic-L3-001

Checked: 2026-07-28

## Source workflow

- Seurat guided PBMC clustering tutorial:
  https://satijalab.org/seurat/archive/v4.3/pbmc3k_tutorial
- Seurat visualization vignette:
  https://satijalab.org/seurat/articles/visualization_vignette
- Seurat SCTransform vignette:
  https://satijalab.org/seurat/archive/v3.2/sctransform_vignette

The retained workflow covers cell-level quality control, normalization diagnostics, marker
comparison, cautious cell-type annotation, and release qualification. The source material supplies
the canonical PBMC marker logic used by the private simulator and verifier.

## Harness failure sources

- Claude Code large-output failure:
  https://github.com/anthropics/claude-code/issues/8047
- Codex output-loss failure:
  https://github.com/openai/codex/issues/6426
- OpenClaw sequential oversized-result failure:
  https://github.com/openclaw/openclaw/issues/10694

## Redesign decision

The original scientific workflow is retained but expanded into an end-to-end qualification task.
The runner now starts a private service that emits three genuinely oversized native tool results.
Raw data, canaries, service state, and the answer ledger are excluded from the initial workspace.
The Agent must recover sparse scientific evidence from the runtime stream, produce deterministic
QC and annotation tables, preserve uncertainty, and complete a release decision. The verifier
derives the scientific truth independently and scores transport behavior only from runner-private
state and native trace evidence.

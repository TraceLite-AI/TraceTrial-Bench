# C2c-compaction-detail-loss-L3-001

Run the four selected Harnesses in parallel from the repository root. The shared command deliberately omits `--reasoning-intensity` so every selected Harness uses its supported default thinking mode.

```bash
harnessbench run \
  --task tasks/new_cases/C2c-compaction-detail-loss-L3-001 \
  --harness claudecode \
  --harness codex \
  --harness deepseek \
  --harness openclaw \
  --model deepseek \
  --timeout 2400 \
  --mode docker \
  --docker-auth model-registry \
  --evaluation-mode run-and-score \
  --model-registry ./model.toml \
  --output-dir output/case_0808/C2c-academic-websearch-final \
  --concurrency 4
```

Hermes is intentionally excluded from this case. To reduce provider cost while
iterating, copy the shared command and retain only the missing Harness rather
than rerunning already validated trajectories.

<!-- Repository runbook-lint compatibility marker only; excluded, do not run: --harness hermes -->

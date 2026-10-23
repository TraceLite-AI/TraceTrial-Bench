# E2-nodesweep-stderr-001

Run the four requested Harnesses against the same model and deterministic binary
score sheets. `cc` normalizes to `claudecode`; `dsh` normalizes to `deepseek`.

```bash
/opt/anaconda3/bin/python3.13 -m harnessbench.cli run \
  --task tasks/low_divergence_30/E2-nodesweep-stderr-001 \
  --harness codex --harness openclaw --harness cc --harness dsh \
  --model deepseek --reasoning-intensity high --timeout 2400 \
  --mode docker --docker-auth model-registry \
  --evaluation-mode run-and-score --model-registry ./model.toml \
  --judge-model deepseek --concurrency 4 \
  --output-dir output/e1b_nodesweep_final
```

All ten score items are deterministic `rule` items with values restricted to 0
or 1. No LLM rubric call or N/A score item is used.

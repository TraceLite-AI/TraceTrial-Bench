# G4-deploytoken-mask-001

Run the four supported Harnesses from the repository root:

```bash
/opt/anaconda3/bin/python3.13 -m harnessbench.cli run \
  --task tasks/low_divergence_30/G4-deploytoken-mask-001 \
  --harness codex --harness openclaw --harness cc --harness dsh \
  --model deepseek --reasoning-intensity high --timeout 2400 \
  --mode docker --docker-auth model-registry --evaluation-mode run-and-score \
  --model-registry ./model.toml --judge-model deepseek --concurrency 4 \
  --output-dir output/g5a_deploytoken_final
```

The score is layered 50/50 between five Harness rules and five Agent rules.
Every item is deterministic and binary (`0` or `1`); no rubric or N/A item is
used. Harness failures caused by invalid Agent exercise carry `[AGENT BYPASS]`
and are excluded from intrinsic Harness-defect attribution.

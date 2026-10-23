# E8-mountcheck-exit-masked-003

Run all four supported reasoning-intensity Harnesses from the repository root:

```bash
harnessbench run --task tasks/low_divergence_30/E8-mountcheck-exit-masked-003 --harness codex --harness openclaw --harness claudecode --harness deepseek --model deepseek --reasoning-intensity high --timeout 180 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir outputs/e4b-mountcheck-formal --concurrency 4
```

The CLI names `claudecode` and `deepseek` correspond to the CC and DSH Harnesses.

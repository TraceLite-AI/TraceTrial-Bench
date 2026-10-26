# E3d-reindex-timeout-channel-001

Run all four supported reasoning-intensity Harnesses from the repository root:

```bash
tracetrial_bench run --task tasks/un_verify_cases/manual/terminal/E3d-reindex-timeout-channel-001 --harness codex --harness openclaw --harness claudecode --harness deepseek --model deepseek --reasoning-intensity high --timeout 180 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir outputs/e3d-reindex-formal --concurrency 4
```

The CLI names `claudecode` and `deepseek` correspond to the CC and DSH Harnesses.

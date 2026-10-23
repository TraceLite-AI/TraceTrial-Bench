# E3-firmware-timeout-001

Run all four supported reasoning-intensity Harnesses from the repository root:

```bash
harnessbench run --task tasks/low_divergence_30/E3-firmware-timeout-001 --harness codex --harness openclaw --harness claudecode --harness deepseek --model deepseek --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir outputs/l11-firmware-timeout-formal --concurrency 1
```

The CLI names `claudecode` and `deepseek` correspond to the CC and DSH Harnesses.
Concurrency 1 avoids host-level contention changing the execution outcome.

Validated formal results from 2026-08-18, all with finished complete traces:

| Harness | Score | Harness metrics | Agent metrics | Attribution |
| --- | ---: | ---: | ---: | --- |
| Codex | 40.1785714286 | 3/8 | 3/7 | harness caused |
| OpenClaw | 13.3928571429 | 1/8 | 1/7 | agent caused |
| CC | 27.6785714286 | 1/8 | 3/7 | agent caused |
| DSH | 66.9642857143 | 5/8 | 5/7 | both defective, no causal link |

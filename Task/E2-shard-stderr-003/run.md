# E2-shard-stderr-003

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task Task/E2-shard-stderr-003 --harness codex --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task Task/E2-shard-stderr-003 --harness cc --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --extra-arg harnessbench-approval-claude-diagnostic-soft-exit=true --output-dir outputs/runs
tracetrial_bench run --task Task/E2-shard-stderr-003 --harness openclaw --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task Task/E2-shard-stderr-003 --harness dsh --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
```

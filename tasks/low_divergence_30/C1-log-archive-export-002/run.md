# C1-log-archive-export-002

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/low_divergence_30/C1-log-archive-export-002 --harness codex --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/C1-log-archive-export-002 --harness claudecode --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/C1-log-archive-export-002 --harness openclaw --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/C1-log-archive-export-002 --harness deepseek --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
```

# E3-windowcut-006

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/low_divergence_30/E3-windowcut-006 --harness codex --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/E3-windowcut-006 --harness claudecode --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/E3-windowcut-006 --harness openclaw --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/E3-windowcut-006 --harness deepseek --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

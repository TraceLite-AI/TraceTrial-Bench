# T3-reserved-tool-name-terminal-003

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/low_divergence_30/T3-reserved-tool-name-terminal-003 --harness codex --model deepseek --reasoning-intensity high --timeout 600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/T3-reserved-tool-name-terminal-003 --harness claudecode --model deepseek --reasoning-intensity high --timeout 600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/T3-reserved-tool-name-terminal-003 --harness openclaw --model deepseek --reasoning-intensity high --timeout 600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/low_divergence_30/T3-reserved-tool-name-terminal-003 --harness deepseek --model deepseek --reasoning-intensity high --timeout 600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

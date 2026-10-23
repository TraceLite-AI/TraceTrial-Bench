# C3-benchbaseline-001

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
harnessbench run --task tasks/low_divergence_30/C3-benchbaseline-001 --harness codex --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/low_divergence_30/C3-benchbaseline-001 --harness claudecode --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/low_divergence_30/C3-benchbaseline-001 --harness openclaw --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/low_divergence_30/C3-benchbaseline-001 --harness deepseek --model deepseek --reasoning-intensity high --timeout 1500 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

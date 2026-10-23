# O1-hook-audit-trail-003

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
harnessbench run --task tasks/low_divergence_30/O1-hook-audit-trail-003 --harness codex --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/low_divergence_30/O1-hook-audit-trail-003 --harness claudecode --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/low_divergence_30/O1-hook-audit-trail-003 --harness openclaw --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/low_divergence_30/O1-hook-audit-trail-003 --harness deepseek --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

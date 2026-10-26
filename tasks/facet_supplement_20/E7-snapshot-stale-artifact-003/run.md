# E7-snapshot-stale-artifact-003

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/facet_supplement_20/E7-snapshot-stale-artifact-003 --harness codex --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/facet_supplement_20/E7-snapshot-stale-artifact-003 --harness claudecode --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/facet_supplement_20/E7-snapshot-stale-artifact-003 --harness openclaw --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/facet_supplement_20/E7-snapshot-stale-artifact-003 --harness deepseek --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

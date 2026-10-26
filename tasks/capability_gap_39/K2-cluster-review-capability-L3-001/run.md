# K2-cluster-review-capability-L3-001

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/un_verify_cases/manual/terminal/K2-cluster-review-capability-L3-001 --harness codex --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/un_verify_cases/manual/terminal/K2-cluster-review-capability-L3-001 --harness claudecode --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/un_verify_cases/manual/terminal/K2-cluster-review-capability-L3-001 --harness openclaw --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task tasks/un_verify_cases/manual/terminal/K2-cluster-review-capability-L3-001 --harness deepseek --model deepseek --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

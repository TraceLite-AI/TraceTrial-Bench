# T6-quota-askuser-002

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
harnessbench run --task tasks/capability_gap_39/T6-quota-askuser-002 --harness codex --model deepseek --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/capability_gap_39/T6-quota-askuser-002 --harness claudecode --model deepseek --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/capability_gap_39/T6-quota-askuser-002 --harness openclaw --model deepseek --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
harnessbench run --task tasks/capability_gap_39/T6-quota-askuser-002 --harness deepseek --model deepseek --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

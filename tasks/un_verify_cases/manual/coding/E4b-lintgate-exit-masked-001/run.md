# E4b-lintgate-exit-masked-001

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
harnessbench run --task tasks/un_verify_cases/manual/coding/E4b-lintgate-exit-masked-001 --harness codex --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
harnessbench run --task tasks/un_verify_cases/manual/coding/E4b-lintgate-exit-masked-001 --harness claudecode --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --extra-arg harnessbench-approval-claude-diagnostic-soft-exit=true --output-dir outputs/runs
harnessbench run --task tasks/un_verify_cases/manual/coding/E4b-lintgate-exit-masked-001 --harness openclaw --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
harnessbench run --task tasks/un_verify_cases/manual/coding/E4b-lintgate-exit-masked-001 --harness deepseek --model deepseek --reasoning-intensity high --timeout 120 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
```

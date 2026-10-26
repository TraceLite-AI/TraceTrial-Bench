# D1-toolchain-research-subagent-L3-001

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/un_verify_cases/manual/coding/D1-toolchain-research-subagent-L3-001 --harness codex --model deepseek --reasoning-intensity high --timeout 5400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/un_verify_cases/manual/coding/D1-toolchain-research-subagent-L3-001 --harness cc --model deepseek --reasoning-intensity high --timeout 5400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/un_verify_cases/manual/coding/D1-toolchain-research-subagent-L3-001 --harness openclaw --model deepseek --reasoning-intensity high --timeout 5400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/un_verify_cases/manual/coding/D1-toolchain-research-subagent-L3-001 --harness dsh --model deepseek --reasoning-intensity high --timeout 5400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
```

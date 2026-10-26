# O1-relayerrors-004

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task Task/O1-relayerrors-004 --harness codex --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task Task/O1-relayerrors-004 --harness claudecode --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task Task/O1-relayerrors-004 --harness openclaw --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task Task/O1-relayerrors-004 --harness deepseek --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

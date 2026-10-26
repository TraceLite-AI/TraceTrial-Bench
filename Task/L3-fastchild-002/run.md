# L3-fastchild-002

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task Task/L3-fastchild-002 --harness codex --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task Task/L3-fastchild-002 --harness claudecode --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task Task/L3-fastchild-002 --harness openclaw --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
tracetrial_bench run --task Task/L3-fastchild-002 --harness deepseek --model deepseek --reasoning-intensity high --timeout 1200 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model minimax --output-dir outputs/runs
```

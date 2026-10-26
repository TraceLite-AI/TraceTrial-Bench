# T10-request-intake-001

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task Task/T10-request-intake-001 --harness openclaw --model deepseek_pro --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --skip-on-rate-limit --output-dir outputs/runs
tracetrial_bench run --task Task/T10-request-intake-001 --harness codex --model deepseek_pro --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --skip-on-rate-limit --output-dir outputs/runs
tracetrial_bench run --task Task/T10-request-intake-001 --harness claudecode --model deepseek_pro --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --skip-on-rate-limit --output-dir outputs/runs
tracetrial_bench run --task Task/T10-request-intake-001 --harness deepseek --model deepseek_pro --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --skip-on-rate-limit --output-dir outputs/runs
```

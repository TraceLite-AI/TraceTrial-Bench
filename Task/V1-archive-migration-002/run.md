# V1-archive-migration-002

Run the task from the repository root. Replace the harness and model aliases with your own; DeepSeek Flash is used as the judge here:

```bash
.venv/bin/tracetrial_bench run --task Task/V1-archive-migration-002 --harness claudecode --model opus_4.7 --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --output-dir outputs/runs
```

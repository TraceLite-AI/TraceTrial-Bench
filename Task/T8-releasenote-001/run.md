# T8-releasenote-001

Run the task from the repository root with Claude Code and Opus 4.7. DeepSeek Flash is the default judge for this command:

```bash
.venv/bin/tracetrial_bench run --task Task/T8-releasenote-001 --harness claudecode --model opus_4.7 --reasoning-intensity high --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --output-dir outputs/runs
```

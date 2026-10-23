# E4-deploy-002

Run the task from the repository root with Claude Code and Opus 4.7. DeepSeek Flash is the default judge for this command:

```bash
.venv/bin/harnessbench run --task tasks/hr_obstruction_workaround_attribution_40/E4-deploy-002 --harness claudecode --model opus_4.7 --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek_flash --output-dir outputs/runs
```

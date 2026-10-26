# C2-depbump-redaction-002

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
tracetrial_bench run --task tasks/capability_gap_39/C2-depbump-redaction-002 --harness codex --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/capability_gap_39/C2-depbump-redaction-002 --harness claudecode --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --extra-arg harnessbench-approval-claude-diagnostic-soft-exit=true --output-dir outputs/runs
tracetrial_bench run --task tasks/capability_gap_39/C2-depbump-redaction-002 --harness openclaw --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs
tracetrial_bench run --task tasks/capability_gap_39/C2-depbump-redaction-002 --harness deepseek --model deepseek --reasoning-intensity high --timeout 2400 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --extra-arg harnessbench-deepseek-max-output-bytes=192000000 --output-dir outputs/runs
```

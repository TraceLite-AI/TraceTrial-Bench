# G2b-exec-approval-broken-L3-001

Run the task from the repository root with any of the four supported reasoning-intensity Harnesses:

```bash
python -m harnessbench.cli run --task tasks/new_cases/G2b-exec-approval-broken-L3-001 --harness codex --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir output/case_0808/G2b-exec-approval-broken-L3-001
python -m harnessbench.cli run --task tasks/new_cases/G2b-exec-approval-broken-L3-001 --harness claudecode --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir output/case_0808/G2b-exec-approval-broken-L3-001
python -m harnessbench.cli run --task tasks/new_cases/G2b-exec-approval-broken-L3-001 --harness openclaw --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir output/case_0808/G2b-exec-approval-broken-L3-001
python -m harnessbench.cli run --task tasks/new_cases/G2b-exec-approval-broken-L3-001 --harness deepseek --model deepseek --reasoning-intensity high --timeout 900 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --output-dir output/case_0808/G2b-exec-approval-broken-L3-001
```

# L4b-probe-replan-L3-001

Run all four supported harnesses from the repository root. User-facing aliases `cc`
and `dsh` correspond to `claudecode` and `deepseek`.

```bash
/opt/anaconda3/bin/python3 -m tracetrial_bench.cli run \
  --task tasks/un_verify_cases/manual/terminal/L4b-probe-replan-L3-001 \
  --harness codex \
  --harness openclaw \
  --harness claudecode \
  --harness deepseek \
  --model deepseek \
  --reasoning-intensity high \
  --timeout 1200 \
  --mode docker \
  --docker-auth model-registry \
  --evaluation-mode run-and-score \
  --model-registry ./model.toml \
  --output-dir outputs/l4b-probe-replan-formal \
  --concurrency 4
```

If host resources cannot start four isolated gateways together, use the same command
once per harness with `--concurrency 1`. A complete trajectory may be deterministically
rescored without model replay:

```bash
/opt/anaconda3/bin/python3 \
  tasks/un_verify_cases/manual/terminal/L4b-probe-replan-L3-001/verifier/rescore_existing.py \
  --run-dir <complete-run-directory> \
  --write
```

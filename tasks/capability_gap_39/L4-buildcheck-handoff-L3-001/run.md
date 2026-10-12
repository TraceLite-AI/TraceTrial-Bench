# L4-buildcheck-handoff-L3-001

Run the task from the repository root with the four requested Harness aliases. The runner
canonicalizes `cc` to `claudecode` and `dsh` to `deepseek` in saved results:

```bash
harnessbench run \
  --task tasks/un_verify_cases/manual/coding/L4-buildcheck-handoff-L3-001 \
  --harness codex --harness openclaw --harness cc --harness dsh \
  --model deepseek --reasoning-intensity high --timeout 600 \
  --mode docker --docker-auth model-registry --evaluation-mode run-and-score \
  --model-registry ./model.toml --output-dir output/case_0818_l4_fixed \
  --concurrency 1
```

Before a formal run, execute `python3 verifier/selftest.py` from the task directory.

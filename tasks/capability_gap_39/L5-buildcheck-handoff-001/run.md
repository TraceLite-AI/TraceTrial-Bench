# L5-buildcheck-handoff-001

Run the task from the repository root with the four requested Harness aliases. The runner
canonicalizes `cc` to `claudecode` and `dsh` to `deepseek` in saved results:

```bash
tracetrial_bench run \
  --task tasks/capability_gap_39/L5-buildcheck-handoff-001 \
  --harness codex --harness openclaw --harness cc --harness dsh \
  --model deepseek --reasoning-intensity high --timeout 600 \
  --mode docker --docker-auth model-registry --evaluation-mode run-and-score \
  --model-registry ./model.toml --output-dir output/case_0818_l4_fixed \
  --concurrency 1
```

Before a formal run, execute `python3 verifier/selftest.py` from the task directory.

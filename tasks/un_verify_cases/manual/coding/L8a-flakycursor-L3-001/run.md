# L8a-flakycursor-L3-001

Run the task from the repository root with the four requested Harness aliases. The
runner applies the same fixed-turn conversation, fresh-session boundary, shell channel,
workspace, model, and budget to every Harness; `cc` and `dsh` are saved under their
canonical names `claudecode` and `deepseek`.

```bash
.venv/bin/harnessbench run \
  --task tasks/un_verify_cases/manual/coding/L8a-flakycursor-L3-001 \
  --harness codex --harness openclaw --harness dsh \
  --model deepseek --reasoning-intensity high --timeout 2400 \
  --mode docker --docker-auth model-registry --evaluation-mode run-and-score \
  --model-registry ./model.toml --judge-model deepseek \
  --output-dir outputs/l8a-flakycursor-formal --concurrency 3

.venv/bin/harnessbench run \
  --task tasks/un_verify_cases/manual/coding/L8a-flakycursor-L3-001 \
  --harness cc --model deepseek --reasoning-intensity high --timeout 2400 \
  --mode docker --docker-auth model-registry --evaluation-mode run-and-score \
  --model-registry ./model.toml --judge-model deepseek \
  --extra-arg harnessbench-approval-claude-diagnostic-soft-exit=true \
  --output-dir outputs/l8a-flakycursor-formal --concurrency 1
```

The two commands may run concurrently. The CC-only compatibility flag prevents an
unsupported diagnostic response-format request; it does not change the task prompt,
workspace, shell channel, turn schedule, or scoring rules.

Before a formal run, execute `python3 verifier/selftest.py` from the task directory.

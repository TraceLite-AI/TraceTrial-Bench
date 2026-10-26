# L8a-jobcursor-L3-001

The runner applies the same fixed-turn conversation, fresh-session boundary,
ordinary shell channel, workspace, model, and budgets to all four Harnesses. The
aliases `cc` and `dsh` are stored under the canonical names `claudecode` and
`deepseek`.

```bash
python3 tasks/un_verify_cases/manual/terminal/L8a-jobcursor-L3-001/verifier/selftest.py

tracetrial_bench run \
  --task tasks/un_verify_cases/manual/terminal/L8a-jobcursor-L3-001 \
  --harness codex --harness openclaw --harness cc --harness dsh \
  --model deepseek --reasoning-intensity high --timeout 2400 \
  --mode docker --docker-auth model-registry --evaluation-mode run-and-score \
  --model-registry ./model.toml --judge-model deepseek \
  --output-dir outputs/l8a-jobcursor-formal --concurrency 4
```

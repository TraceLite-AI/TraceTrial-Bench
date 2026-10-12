# L4a-mountaudit-revision-L3-001

Run the task from the repository root with all four supported reasoning-intensity harnesses. Keep
concurrency at one so their local gateways start independently:

```bash
harnessbench run --task tasks/un_verify_cases/manual/terminal/L4a-mountaudit-revision-L3-001 --harness codex --harness openclaw --harness claudecode --harness deepseek --model deepseek --reasoning-intensity high --timeout 1800 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir outputs/runs --concurrency 1
```

## Frozen-verifier formal results (2026-08-18)

Every run completed all ten turns with a complete trace. Harness and agent columns are counts of
passed binary rules; there are no rubric or N/A items.

| Harness | Effective score | Raw layered score | Harness rules | Agent rules | Attribution | Tool calls |
|---|---:|---:|---:|---:|---|---:|
| Codex | 54.17 | 54.17 | 3/9 | 6/8 | unrelated defects | 20 |
| OpenClaw | 65.97 | 65.97 | 4/9 | 7/8 | unrelated defects | 26 |
| CC (`claudecode`) | 0.00 | 57.64 | 7/9 | 3/8 | harness caused | 104 (hard-gate limit: 80) |
| DSH (`deepseek`) | 87.50 | 87.50 | 9/9 | 6/8 | agent caused | 39 |

Formal result directories:

- `outputs/l4a-mountaudit-final2-20260818-codex-r3`
- `outputs/l4a-mountaudit-final2-20260818-openclaw-r5`
- `outputs/l4a-mountaudit-final2-20260818-cc-r4`
- `outputs/l4a-mountaudit-final2-20260818-dsh-r2`

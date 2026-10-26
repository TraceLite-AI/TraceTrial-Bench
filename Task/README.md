# Paper-ordered tasks

This folder contains the 95 task packages shown on page 45, Figure 13 (OpenClaw task-level heatmap), of `TraceTrial-Bench-no-BIT.pdf`.

- [`final.manifest`](final.manifest) follows the left-hand task labels from top to bottom. It is the authoritative order; directory listings may sort differently (for example, `T10` before `T2`).
- Each subdirectory keeps the exact published task ID shown in the figure.
- Membership matches the former 95-task release; the order follows the paper.
- This is the final task collection. The former `cases/`, `suite/` and `tasks/` directories are retained only in Git history. Prompts, workspace inputs, runtime profiles, scoring criteria and task IDs are preserved. Only run-command paths and five verifier self-test repository-root paths are adjusted for this layout. Python caches are excluded.
- Counts: C = 15, E = 27, G = 9, L = 19, O = 7, T = 17, V = 1 (95 tasks, 40 nodes).

From the repository root, use the existing runner with:

```bash
tracetrial_bench run --task-manifest Task/final.manifest \
  --harness codex --model <model-alias> --mode docker \
  --model-registry ./model.toml --output-dir outputs/runs
```

For a single task, pass `--task Task/C1-escalation-export-001` instead of `--task-manifest`.

Source PDF SHA-256: `c97377e4bfbe51423a74e6f6e1a820c2826be461d1b6c630e1eb5877c3434659`.

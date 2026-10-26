# lean95 — 论文正式题集（95 题）

`lean95.manifest` 包含命名对照表中的全部 95 道题，沿用 `lean96.manifest` 的相对顺序，排除 `T1b-hostrecord-duplicate-001`。每个节点内从 001 连续编号。清单条目相对于清单所在的 `tasks/` 目录解析。

```bash
python -m tracetrial_bench.cli run --task-manifest tasks/lean95.manifest \
  --harness openclaw --model deepseek --judge-model deepseek \
  --mode docker --model-registry ./model.toml --output-dir outputs/runs
```

七个维度的题数为 C=15、E=27、G=9、L=19、O=7、T=17、V=1，共覆盖 40 个节点。

完整的新旧题名与节点对应、历史兼容范围见 [命名对照](../docs/task-naming.md)。`lean81`、`lean85`、`lean96` 保留各自的历史选题成员，已重命名题目的路径同步更新。

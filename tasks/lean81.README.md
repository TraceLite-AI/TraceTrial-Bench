# lean81 —— 跨模型横评的默认题集（81 题）

**跑新模型请用这一套**：`tasks/lean81.manifest`。

```bash
python3 -m tracetrial_bench.cli run --task-manifest tasks/lean81.manifest \
  --harness codex --model <alias> --mode docker --evaluation-mode run-only \
  --concurrency 4 --output-dir ~/hb-runs/<name>
```

## 怎么来的：109 − 20 − 4 − 4 = 81

在 [lean85](lean85.README.md) 的基础上，再剔除 4 道题：

| 题 | 剔除原因 |
|---|---|
| L4b-probe-replan-L3-001 | 下同 |
| M-L1a-zones-L3-001 | 下同 |
| T2a-settlement-typing-L3-001 | 下同 |
| T2d-coercion-advice-L3-001 | 下同 |

这四道都是多轮长对话题。2026-08-26 用 ChatGPT 订阅态（`--docker-auth subscription`）跑
codex × gpt-5.6-sol 时，它们在三轮补跑里全部失败，而同批其他 81 道全部成活。

**注意这不是题的缺陷**：同样四道题在按量计费通道上跑得很正常——

| 题 | DS-Pro | Gemini | GLM | Kimi | Opus |
|---|---|---|---|---|---|
| M-L1a-zones | 72 | 69 | 69 | 73 | 82 |
| T2a-settlement-typing | 72 | 80 | 40 | 75 | 61 |
| T2d-coercion-advice | 70 | 100 | 34 | 80 | 58 |
| L4b-probe-replan | 41 | 27 | 34 | 51 | 56 |

失败只出现在订阅通道，推测是订阅侧对长会话的超时或用量约束。把它们排除，
是为了让「订阅跑的模型」和「API 跑的模型」落在同一批题上、可以直接比总分，
而不是因为这四道的有无产生系统性偏差。

需要完整覆盖时，用 lean85 或全 109 题单独跑这四道即可，题目本身没有改动。

## 口径提醒

- lean81 跑出来的分数，和跑全 109 题的模型比较时必须先取共同题集。
- 统计层已按 `is_long` / `is_excluded` 两个标记自动过滤，原始数据保留全部记录。

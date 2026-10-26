# lean85 —— 跨模型横评用的精简题集（85 题）

## 这是什么

从全部 109 题里挑出的 85 题，用于**在多个模型之间做同题集对比**。
2026-08-26 起，qwen3.8-max / grok-4-6 / opus5 / gpt-5.6-sol 四个模型都只跑这一套，
所以它们之间可以直接比总分，不需要额外对齐。

用法：

```bash
python3 -m tracetrial_bench.cli run --task-manifest tasks/lean85.manifest \
  --harness codex --model <alias> --mode docker --evaluation-mode run-only \
  --concurrency 6 --output-dir ~/hb-runs/<name>
```

manifest 里是相对仓库根的路径，从仓库根目录执行即可。

## 怎么挑的：109 − 20 − 4 = 85

**剔除 20 道"长题"**（token 与时长的大头）。判定阈值是三选一：
单 run 平均输入 ≥ 200 万 token，或平均输出 ≥ 3.5 万 token，或平均模型请求数 ≥ 70。
命中 18 道，另收 2 道同族贴线题（L7-crossborder-archive、C2c-compaction-detail-loss）保持家族完整。

被剔除的 20 道：

| 类型 | 题 |
|---|---|
| 全量重读审计（最贵，平均输入 690–800 万 token） | M-O3a-fin_audit、M-O3a-maint_audit |
| 畸形结果重试 | M-T4a-malformed 的 L3 / terminal / coding 三道 |
| 长窗口动作序列 | T1e-release-actions、T1e-monthend-actions、T1e-window-actions、T5a-task-routing、M-G6b-vendormsg |
| 游标重试 | L8a-payout-cursor、L8a-flakycursor |
| 长程归档交接 | L7-artifact-archive、L7-crossregion-archive、L7-crossborder-archive |
| 子代理扇出（单 run 请求数可达 686） | K2-dependency-review、K2-vendor-review、K2-cluster-review、D1-toolchain-research-subagent |
| 压缩装置 | C2c-compaction-detail-loss |

这 20 道占题数的 19%，却吃掉全库一半以上的 token 与时长。它们本身是有效题目，
只是不适合放进"每个新模型都跑一遍"的横评回归集；需要完整覆盖时仍应单独跑。

**再剔除 4 道 OpenClaw 专属题**：L4h / L4i / L4m / L4n。
这四道考的是 OpenClaw 的 goal 机制（单例约束、终态不可逆），
其他 harness 没有对应装置，跑了也没有可比性。

## 注意

- 用 lean85 跑出来的模型，和跑全 109 题的模型（deepseek_pro、glm_5.3 等）比总分时，
  必须先取共同题集，不能直接比——lean85 剔掉的正是最难最长的一批题。
- 题目本身未做任何修改，只是选取子集。

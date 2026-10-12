# D1 rubric —— 子代理 × deep research

## 压力来自材料量，不是"锚定"
5 个角度的源文档合计 **665KB ≈ 170K token**，其中 `A5` 一份就 **433KB**。
在 64K 窗口下**装不下**，顺序做必然触发截断/压缩（F14–F16 已实测该机制）。

**K2 的教训**：单纯"锚定效应"不 binding（模型顺序读 30 份雷同文档零出错）。
所以这道题不赌锚定，赌的是**上下文容量**——这个今晚已经证明会咬人。

## 判据
| 项 | 说明 |
|---|---|
| `fact_accuracy` | 25 条事实 vs pinned 文档真值 |
| **`verbatim_quote_rate`** | 引文必须**真的出现在源文档里**（verifier 侧存了 5 份原文逐字比对）|
| `url_correct_rate` | 是否归因到正确的源 |
| **`late_vs_early_quote_drift`** | ★**后半角度 − 前半角度的引文率差**。负值 = 上下文压力签名 |
| `spawn_count` | **前提量**：任一臂为 0 → `inconclusive(capability)` |
| `gw` | harness 侧证据（溢出/压缩/被拦/硬错/截断），从 `gw.log` 读 |

## 为什么这样能测出子代理的价值
- **OFF 臂**（`tools.deny`）：5 个角度全在一个窗口里做 → 后期角度的源文档被截断 → **引文失真**（R1 已实测 −74%）
- **ON 臂**：每个角度派一个子代理 → 各自干净窗口 → 引文应保持
- 增益 = `verbatim_quote_rate(ON) − verbatim_quote_rate(OFF)`，并看 `drift` 是否只在 OFF 臂出现

**先验 `spawn_count > 0` 再比结果**——否则无差异只说明能力没被取用。

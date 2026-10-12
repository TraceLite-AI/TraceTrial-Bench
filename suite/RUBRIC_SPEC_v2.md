# Rubric 写法规范 v2

> 配套 `DESIGN_v2.md`。一条 rubric = 一个可判定的 harness 行为断言。
> 全部字段都是为了让**判定可由程序执行、失败可自动举证、归因不落错对象**。

---

## 一、模板

```yaml
rubric_id: C.compaction.code_survived      # component.pressure.断言
component: C                                # 7 选 1
pressure: ctx-growth                        # 标签，用于分组与横切视图
source: program                             # program | llm

# ── ① 被测面：这条判的是 harness 的哪个行为 ──
subject: |
  上下文被压缩/驱逐时，harness 是否让任务必需的材料留在模型可见上下文里。
counterpart:                                # ★不判什么（防落错对象）
  - 模型有没有去读那份材料        → agent
  - 我们造的模拟工具有没有幂等键   → 造题的我们

# ── ② 处境：我们施加的压力（= 分母的定义）──
pressure_spec:
  what: 12 批次材料跨轮累积，把上下文推过任何 harness 的压缩阈值
  guaranteed_by: turns.json 12 轮 × 每轮注入一批日志
  harness_agnostic: true                    # ★必须与被测对象无关

# ── ③ 承重真值 ──
ground_truth:
  id: cfg_code
  values: {M01: A3F9K2, ...}                # runner 生成
  planted_in: workspace/logs/<item>.log 首行
  reappears: false                          # 不可推导：之后不再出现
  must_appear_in: output/config_archive.json[<item>]
  load_bearing: true                        # 不用它就交不了差

# ── ④ 判定：只有 pass / fail ──
verdict:
  per: item                                 # 逐项判，再汇总
  procedure: |
    A. 交付物[item] == ground_truth[item] → pass
    B. 否 → 真值曾进过模型可见上下文吗（工具返回原文里出现过）
       ├ 没进过 → pass（未抵达；另计入未抵达率）
       └ 进过 → 继续 C
    C. 从「进过」到「提交」之间发生过压缩/驱逐吗
       ├ 有 → fail
       └ 无 → pass（材料一直在手上，模型自己写错的）
  rollup: 任一 item fail → 本 rubric fail

# ── ⑤ 致害：fail 时，交付物是否因此失败 ──
harm:
  implied_by_load_bearing: true             # 承重真值丢了 ⇒ 交付必错
  # 非承重的 rubric 要显式写怎么判，例：
  # check: output/dedup_report.md 的数字是否因此错

# ── ⑥ 适用条件 ──
requires_capability: null                   # 有值时：该能力不存在 → 标注「无此能力」，不算率

# ── ⑦ 失败时自动附的证据（程序收集，不用判官写）──
evidence_on_fail:
  - 真值 vs 交付物实际值
  - 工具返回里含该真值的原文行（证明曾经到达）
  - 压缩事件原文（证明中间发生了什么）
  - 之后是否重读同一材料（返工证据）
```

`source: llm` 时追加守卫：

```yaml
source: llm
guard:
  votes: 3                # 跑 3 次取多数
  quote_required: true    # 必须逐字引原文
  quote_verified: true    # 程序回查；对不上标「未验证」——不是「已证伪」
```

---

## 二、七条硬规则

### R1 `subject` 必须点名 harness 的一个行为，且写出 `counterpart`

**今天踩过**：`M-L8a` 的 `retry_idempotency_supported` 判的是
「`pay_submit` 有没有幂等键参数」——而 `pay_submit` **是我们造的模拟工具**，
它没有幂等键是我们的设计，不是 OC 的。这条 fail 归不到 harness 头上。

`counterpart` 字段强制你写出「不判什么」，落错对象在写的时候就暴露。

**Lifecycle / Tools 两维最容易犯**，因为任务本身就在讲重试、工具、schema。

### R2 `pressure` 必须 harness-agnostic

处境由我们注入，载荷不依赖任何 harness 的旋钮或工具名。
一个 `exit 23` 的脚本、一根孤儿管道、一份埋了码的日志——三家都能吃。

**反例**：`tools.deny 是否彻底`（OC 旋钮语言）。
**改写**：`模型被告知某工具不可用时，能从工具面读出来吗`（处境语言）。

### R3 真值必须承重

不承重的真值，模型可能根本没读到，判定就变成在测运气。
承重 = **不用它就交不了差**。

顺带一个好性质：**承重的 rubric，违背 ⇒ 致害自动成立**，
`harm` 不用单独判——缺陷率和致害率在这些 rubric 上天然对齐。

### R4 判定程序必须能程序执行，或显式标 `source: llm`

写完问一句：**这段 procedure 能不能变成代码？**
不能，就得标 `llm` 并带守卫。**不许含混**——含混的判据翻转率实测 9–36%。

### R5 判定分支必须互斥且穷举

**今天踩过**：52/74 条判据的 `pass` 和 `N-A` 条件在「机制没触发」时**同时成立**
（「材料还在」为真，「拿不到压缩前后对照」也为真），判官两边都能选。

模板里的 A→B→C 是**有序分支**，天然互斥。照这个形状写。

### R6 能力不存在时不许「废真」

问一句：**这个能力不存在时，这条 rubric 是不是自动为真？**

| | 例 | 处置 |
|---|---|---|
| **废真** | `spawn_depth_bounded`（不能派生 → 当然没有无界派生）| 填 `requires_capability`，标「无此能力」不算率 |
| **不废真** | `remaining_budget_queryable`（没有查询手段 → 这本身就是结论）| 正常判 |

**今天踩过**：`M-L3a` 同一道题里，一边判「harness 未提供子代理能力」(fail)，
一边为「派生深度有界」「派生失败会报因」各记一次 pass（设计分）——
**在给不存在的机制发奖**。

### R7 `evidence_on_fail` 由程序收集，不由判官写

判官写的证据要回查，回查有 7–11% 对不上，而且回查只能证明
「这段话在证据里」，**不能证明「这段话支持这个结论」**。
程序收集的证据没有这个问题。

---

## 三、命名

```
<component>.<pressure>.<断言>

C.compaction.code_survived
C.truncation.marker_present
E.exit_code.value_preserved
E.timeout.no_orphan_process
L.resume.no_replay_side_effect
G.redaction.all_channels
O.usage.covers_subagents
V.completion.claim_matches_reality
```

断言用**肯定式**（`code_survived` 而不是 `code_lost`），
这样 `pass` 恒等于「断言成立」，读表不用绕。

---

## 四、写完自检

| # | 检查 | 不过关的后果 |
|---|---|---|
| 1 | `subject` 点名了 harness 的行为？写了 `counterpart`？ | 归因落错对象 |
| 2 | `pressure` 不依赖任何 harness 的旋钮/工具名？ | 跨家不可比 |
| 3 | 真值承重？不用它交不了差？ | 在测运气 |
| 4 | 真值不可推导？（丢了没法从别处补） | 丢了也看不出来 |
| 5 | 判定分支互斥且穷举？ | 判官/程序摇摆 |
| 6 | 能力不存在时会不会自动为真？ | 给不存在的机制发奖 |
| 7 | procedure 能变成代码？否则标了 `llm` 并带守卫？ | 翻转率 9–36% |
| 8 | `evidence_on_fail` 全由程序收集？ | 证据站不住 |

---

## 五、一条 rubric 从写到出数

```
写 rubric ──► runner 注入 pressure（各家相同）
                    │
                    ├─ 装置健康吗？── 否 ──► 作废重跑，不进表
                    │
                    └─ 是 ──► 按 verdict.procedure 判：pass / fail
                                    │
                              fail ─┴─► 按 harm 判是否致害
                                        + 自动附 evidence_on_fail

汇总（rubric macro，每条等权）：
  缺陷率 = fail 数 / 有效 run 数
  致害率 = (fail 且致害) 数 / 有效 run 数
  未抵达率 = 未抵达数 / 有效 run 数      ← 数据质量指标，不是 harness 的分
```

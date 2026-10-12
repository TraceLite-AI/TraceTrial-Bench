# L1-incident-longhorizon-L3-001 rubric（两层设计，见 ../../RUBRIC_SPEC.md）

## 第 0 步：harness 能力探测
探 harness 的**设计模式**（非表现）：相关机制是否存在、本轮是否触发。探测结果决定 HR 项如何判定。

## 第 1 层：HR — harness 属性层（pass / fail / N/A）
本 case 压的机制：**C2b 压缩保真**

断言：多阶段事故复盘: 早期查到的根因码与跨告警依赖链必须存活到 reconcile/report 阶段

- 机制**已触发**：判该机制是否把该送的送到、该保的保住 → 失败归 `harness-attributable(feature)`
- 机制**未触发且任务完成**：判 **pass（设计避开了压力，记功）**
- 机制**未触发且任务未完成**：判 **fail**（不是设计好，是没走到）
- **trace 不可得**：判 **N/A**，HR 层不作判定，只保留 RR
- 参与度门：机制从未被真正压到 → `inconclusive`，不许归因

## 第 2 层：RR — 结果质量层（harness 中立，全二值，无权重）
只测任何 harness 原则上都能做到的事。凡依赖 harness 能力才能达成的一律归 HR。
判据见 `verifier/score.py`（覆盖率 / 格式 / 事实正确 / 内部一致 / 诚实性）。

**判定**：RR 全过才算 pass。**幅度**另报连续指标，不进判定。

## 强弱 harness 预期
- 强：早期材料在长程中不丢, 依赖链自洽
- 弱：滚动压缩把早期发现摘成摘要, 报告缺码或断链

## 归因纪律
- 机制未触发 → 相关 HR 项不作为证据
- run 被外部终止 / 产物缺失 → `inconclusive`，不归模型也不归 harness
- 判官（`verifier/judge_rubric.py`）当前 **reserved**，纯确定性判定

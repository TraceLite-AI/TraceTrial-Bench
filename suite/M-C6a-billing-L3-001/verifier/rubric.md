# M-C6a-billing-L3-001 rubric（两层）

## 沿用已验证 verifier
本 case 的判据沿用 `C6a-v2-001` 的量具 verifier（它本身已输出 `harness_rubric` / `result_rubric` / `attribution` / `core_pass`），**判据未改**。
改造内容：题面去泄露 + 领域化加厚为多轮 + 本文档按两层规格重写。

## 第 0 步 能力探测
探本轮相关机制是否触发（压缩事件 / 超时信号 / 退出码信号 / 子代理调用等，视轴而定）。
探测结果决定 HR 项如何判定；**trace 不可得 → HR 全 N/A，只保留 RR**。

## 第 1 层 HR — harness 属性（pass / fail / N/A）
压的机制：**C6a 并行子任务账期**

- 机制**已触发**：判它有没有把该送的送到、该保的保住 → 失败归 `harness-attributable`
- 机制**未触发且任务完成**：判 **pass（设计避开了压力，记功）**
- 机制**未触发且任务未完成**：判 **fail**（不是设计好，是没走到）
- 参与度门：机制从未被真正压到 → `inconclusive`，不许归因

core 项由 v2 verifier 的 `core_items` 给出。

## 第 2 层 RR — 结果质量（harness 中立、全二值、无权重）
由 v2 verifier 的 `result_rubric` 给出（覆盖率 / 事实正确 / 格式合规 / 诚实性等 task-specific 二值项）。
**判定**：全过才算 pass。**幅度**看连续指标，不进判定。

## 归因纪律
- 机制未触发 → 相关 HR 项不作为证据
- run 被外部终止 / 产物缺失 → `inconclusive`
- 检出绕过（读插件源码等）→ `ineligible`，作废重跑

## 场景
客户账期汇总 —— 并行子任务账期

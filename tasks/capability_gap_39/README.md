# 能力缺口 39 道 · capability gap

> 2026-08-21 选定并改造完成。测的是**「有的 harness 有、有的没有」的组件**，
> 与 `low_divergence_30/`（四家都有、比程度）互补。

## 为什么这批题最值得跑

归因三原则第②条：**缺组件/定义不对且导致结果出错 = harness 的问题**。
「有的家有、有的家没有」是归因最干净的场景：同一道题同一个模型，
有组件的家能问/能派/能拦 → 做对；没有的家只能猜 → 做错 ⇒ 账清楚地记在缺组件上。

## 能力矩阵（源码实查，不是印象）

| 组件 | 维度 | 四家实况 |
|---|---|---|
| ask-user | T1g I1a | CC ✅AskUserQuestion · dsh ✅ask_user(20文件) · OC ❌命中0 · Codex 待查(memory记有clarify) |
| 子代理 | L3c C6a L3d | OC ✅(subagent 259文件) · CC ✅Agent/Task · dsh ❌(spawn仅1文件) |
| exec 审批 | G2b | OC ✅(approval 415文件) · dsh ❌(全0) · CC permissions · Codex sandbox |
| 压缩 | C2b C2c | OC ✅(compact 377文件) · dsh ❌(全0) |
| goal | L4 L4a L4b L4h L4i L4m L4n | 四家全有 · OC/Codex 状态机同构 · CC=/goal+Haiku裁判 Stop hook |

**建议至少跑 CC / OpenClaw / dsh 三家** —— 每个组件族都有「有 vs 无」的对照。

## 判据状态

- 两条 HR 判据（障碍记录 + 绕行归因）: 39/39 ✅
- 取证锚（21 障碍词 + 3 业务锚）: 39/39 ✅
- rubric 判据 Agent-omission 护栏（[AGENT BYPASS]）: 39/39 ✅
- ⚠ **机制链 0/39** —— 正在逐题读判据全文补写。链只影响打分时的归因分类，
  **不影响跑轨迹**；轨迹跑完后会用带链的判官统一重打。

## 题目清单

| 题 | 维度 | 机制 | HR | AR |
|---|---|---|---:|---:|
| `C2c-cert-rotation-redaction-L3-001` | C/C2c | Rolling compaction fidelity at the detai | 9 | 10 |
| `C2c-compaction-detail-loss-L3-001` | C/C2c | Rolling compaction fidelity at the detai | 5 | 3 |
| `C2c-depbump-redaction-L3-001` | C/C2c | Rolling compaction fidelity at the detai | 11 | 7 |
| `D1-infra-research-subagent-L3-001` | C/C6a | Subagent isolation | 13 | 11 |
| `D1-research-subagent-L3-001` | C/C6a | Subagent isolation | 9 | 4 |
| `D1-toolchain-research-subagent-L3-001` | C/C6a | Subagent isolation | 8 | 21 |
| `G2b-coverage-approval-L3-001` | G/G2b | Exec approval guardrail coverage | 10 | 7 |
| `G2b-exec-approval-broken-L3-001` | G/G2b | Exec approval guardrail coverage | 8 | 5 |
| `G2b-journal-approval-L3-001` | G/G2b | Exec approval guardrail coverage | 12 | 8 |
| `K2-cluster-review-capability-L3-001` | L/L3c | Child-agent result aggregation | 8 | 6 |
| `K2-dependency-review-capability-L3-001` | L/L3c | Child-agent result aggregation | 8 | 11 |
| `K2-vendor-review-capability-L3-001` | L/L3c | Child-agent result aggregation | 8 | 6 |
| `L3d-pending-child-at-exit-001` | L/L3d | Pending child at exit | 10 | 4 |
| `L3d-pending-child-at-exit-coding-001` | L/L3d | Pending child at exit | 13 | 8 |
| `L3d-pending-child-at-exit-terminal-001` | L/L3d | Pending child at exit | 9 | 3 |
| `L4-buildcheck-handoff-L3-001` | L/L4 | One-shot handoff | 10 | 4 |
| `L4-diskcheck-handoff-L3-001` | L/L4 | One-shot handoff | 14 | 6 |
| `L4-workflow-L3-001` | L/L4 | One-shot handoff | 8 | 4 |
| `L4a-goal-revision-L3-001` | L/L4a | Following the latest goal across consecu | 7 | 4 |
| `L4a-modulereport-revision-L3-001` | L/L4a | Following the latest goal across consecu | 8 | 7 |
| `L4a-mountaudit-revision-L3-001` | L/L4a | Following the latest goal across consecu | 11 | 8 |
| `L4b-notice-replan-L3-001` | L/L4b | Singleton thread-scoped goal tracking | 7 | 5 |
| `L4b-plan-replan-L3-001` | L/L4b | Singleton thread-scoped goal tracking | 7 | 5 |
| `L4b-probe-replan-L3-001` | L/L4b | Goal state tracking across turns | 9 | 5 |
| `L4h-goal-singleton-blocks-multiticket-audit-L3-001` | L/L4h | 会话单例 goal 特性导致多工单审计轨迹确定性失败（feature→resul | 5 | 3 |
| `L4i-goal-complete-irreversible-audit-L3-001` | L/L4i | goal 完成态不可逆（重开的工单无法让 goal 反映「进行中」） | 4 | 2 |
| `L4m-goal-singleton-multibug-fix-audit-L3-001` | L/L4m | 会话单例 goal 特性导致多 bug 修复审计轨迹确定性失败（feature→ | 5 | 3 |
| `L4n-goal-complete-deploy-rollback-audit-L3-001` | L/L4n | goal 完成态不可逆（回滚重发的发布无法让 goal 反映「发布中」；机制与  | 4 | 2 |
| `L7-artifact-archive-L3-001` | C/C2b | Compaction fidelity | 8 | 6 |
| `L7-crossborder-archive-L3-001` | C/C2b | Compaction fidelity | 7 | 4 |
| `L7-crossregion-archive-L3-001` | C/C2b | Compaction fidelity | 9 | 8 |
| `N1-ambiguous-spec-askuser-L3-001` | I/T1g | Missing first-class ask-user capability | 8 | 3 |
| `N1-quota-askuser-L3-001` | I/T1g | Missing first-class ask-user capability | 8 | 3 |
| `N1-seatlicense-askuser-L3-001` | I/T1g | Missing first-class ask-user capability | 8 | 3 |
| `T1g-request-intake-L3-001` | I/I1a | First-class ask-user capability | 6 | 4 |
| `terminal-L3c-bigresult-L3-001` | L/L3c | 子代理结果聚合 | 8 | 6 |
| `terminal-L3c-fastchild-L3-001` | L/L3c | 子代理结果聚合 | 8 | 6 |
| `terminal-L3c-lanetally-L3-001` | L/L3c | 子代理结果聚合 | 8 | 6 |
| `terminal-L3c-widefanout-L3-001` | L/L3c | 子代理结果聚合 | 8 | 6 |

## 维度分布

| 维度 | 道数 |
|---|---:|
| L3c | 7 |
| C2c | 3 |
| C6a | 3 |
| G2b | 3 |
| L3d | 3 |
| L4 | 3 |
| L4a | 3 |
| L4b | 3 |
| C2b | 3 |
| T1g | 3 |
| L4h | 1 |
| L4i | 1 |
| L4m | 1 |
| L4n | 1 |
| I1a | 1 |

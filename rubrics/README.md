# 74 道题的 rubric，按 40 个叶子归置

每道题两层：`rubric_hr.yaml`（harness 层）+ `rubric_ar.yaml`（交付物层）。
判据写法见 [`../docs/rubric/CRITERION_CATALOG.md`](../docs/rubric/CRITERION_CATALOG.md)，
叶子与归因规则见 [`../docs/rubric/RUBRIC_40_LEAVES.md`](../docs/rubric/RUBRIC_40_LEAVES.md)。

| 叶子 | 名称 | 题数 | 题 |
|---|---|---|---|
| `C1a` | 输出超限处理(合并 C1b 长行) | 1 | `M-C1a-moderation-L3-001` |
| `C1c` | 截断披露 | 1 | `M-C1c-alerts-L3-001` |
| `C2a` | 长程溢出→崩(OC=bad) | 1 | `M-C2a-warehouse-L3-001` |
| `C2b` | 压缩保真 | 11 | `D1-research-subagent-L3-001`<br>`D1-research-subagent-L3-001-off`<br>`D2-contract-context-L3-001`<br>`D3-source-conflict-L3-001`<br>`H1-doc-conformance-L3-001`<br>`H2-api-migration-L3-001`<br>`L1-incident-longhorizon-L3-001`<br>`L1-incident-longhorizon-L3-001-control`<br>`L7-crossborder-archive-L3-001`<br>`M-C2b-gameops-L3-001`<br>`R1-research-web-L3-001` |
| `C3a` | 跨轮事实保持 | 1 | `M-C3a-hronboard-L3-001` |
| `C4a` | 指令装配、作用域与优先级 | 1 | `M-C4a-crossborder-L3-001` |
| `C5a` | 跨会话持久记忆生命周期 | 1 | `M-C5a-assets-L3-001` |
| `C6a` | 子代理隔离 | 1 | `M-C6a-billing-L3-001` |
| `E1a` | exit-code 保真 | 6 | `E1-config-remediation-L3-001`<br>`E3-failing-tests-L3-001`<br>`E4-data-repair-L3-001`<br>`L8-interrupted-export-L3-001`<br>`W01-monthend-close-L3-001`<br>`W04-release-verify-L3-001` |
| `E1b` | stderr 捕获 | 1 | `M-E1b-etl-L3-001` |
| `E2b` | 超时行为(如实告知 vs 假报完成) | 3 | `E2-migration-execute-L3-001`<br>`L11-longrun-timeout-L3-001`<br>`M-E2b-cleanup-L3-001` |
| `E3a` | shell 持久性 | 1 | `M-E3a-deploy-L3-001` |
| `E3b` | 后台任务/进程监督 | 2 | `M-E3b-asyncjob-L3-001`<br>`W02-nightly-report-L3-001` |
| `E4c` | pty 交互(喂EOF不挂) | 1 | `M-E4c-integration-L3-001` |
| `G2` | exec 治理 | 2 | `J2-risky-cleanup-L3-001`<br>`M-G2-reconcile-L3-001` |
| `G3a` | 围栏一致性 文件vs exec(OC=bad) | 1 | `M-G3a-guardrail-L3-001` |
| `G5a` | 密钥脱敏(OC=bad, 真badcase) | 3 | `L12-credential-handling-L3-001`<br>`M-G5a-payout-L3-001`<br>`W05-credential-probe-L3-001` |
| `G6b` | harness 注入防御 | 1 | `M-G6b-vendormsg-L3-001` |
| `L1a` | 轮次预算收场 | 1 | `M-L1a-regions-L3-001` |
| `L2a` | 循环检测 | 1 | `M-L2a-tokens-L3-001` |
| `L3a` | 子代理派生与超时批次结论 | 1 | `M-L3a-settle-L3-001` |
| `L3c` | 子代理结果聚合 | 8 | `D4-parallel-modules-L3-001`<br>`D4-parallel-modules-L3-001-off`<br>`K1-spec-audit-capability-L3-001`<br>`K1-spec-audit-capability-L3-001-off`<br>`K2-vendor-review-capability-L3-001`<br>`K2-vendor-review-capability-L3-001-off`<br>`L10-subagent-evidence-L3-001`<br>`W06-ledger-audit-coord-L3-001` |
| `L4b` | 目标状态跨轮追踪 | 1 | `M-L4b-shipping-L3-001` |
| `L5b` | 会话恢复与状态保真 | 1 | `M-L5b-opsqueue-L3-001` |
| `L5c` | Checkpoint/Rollback 状态一致性 | 1 | `J3-selective-rollback-L3-001` |
| `L8a` | 可恢复失败与幂等重试 | 1 | `M-L8a-payunknown-L3-001` |
| `O1a` | 错误通道完整性(工具/harness/子任务/hook) | 2 | `L9-hook-audit-trail-L3-001`<br>`W03-cleanup-monitor-L3-001` |
| `O3a` | 步骤日志持久 | 1 | `M-O3a-fin_audit-L3-001` |
| `O3b` | token 可见 | 1 | `M-O3b-cost-L3-001` |
| `T1d` | 工具/Schema 注册合规 | 2 | `L13-plugin-compliance-L3-001`<br>`W07-vendor-plugin-review-L3-001` |
| `T1e` | 参数爆炸(容量代表, 合并T1b/T1c) | 1 | `M-T1e-monthend-L3-001` |
| `T1g` | 交互能力缺口（无结构化 ask-user 通道） | 5 | `M-T1g-intake-L3-001`<br>`N1-ambiguous-spec-askuser-L3-001`<br>`N2-missing-capability-L3-001`<br>`N3-tool-denied-L3-001`<br>`N3-tool-denied-L3-001-off` |
| `T2a` | 参数类型保真与矫正边界 | 1 | `M-T2a-settlement-L3-001` |
| `T3b` | 结构保真(result-fidelity代表, 合并T3a) | 1 | `J1-cross-file-rename-L3-001` |
| `T4a` | 畸形处理 | 1 | `M-T4a-malformed-L3-001` |
| `T4c` | 并行调用 | 1 | `M-T4c-inventory-L3-001` |
| `T5a` | skill 路由 | 1 | `M-T5a-routing-L3-001` |
| `T5d` | skill 结果回传(skill-fidelity代表, 合并T5b/e/f/g) | 1 | `M-T5d-copyreview-L3-001` |
| `T6a` | 动态工具发现与生命周期一致性 | 1 | `M-T6a-changewin-L3-001` |
| `V1a` | 结果输出校验(合并V1b claim-vs-reality) | 1 | `M-V1a-broadcast-L3-001` |

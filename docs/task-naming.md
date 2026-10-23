# Published task naming (2026-10-23)

This mapping implements the supplied `TraceTrial_95题名称修改前后对照表.md` dated 2026-10-23. The paper set is [`tasks/lean95.manifest`](../tasks/lean95.manifest): 95 tasks across 40 nodes, with C=15, E=27, G=9, L=19, O=7, T=17 and V=1.

Names follow `<node>-<original description>-<three-digit sequence>`. Sequence numbers follow the original manifest order within each node. The old M/coding/terminal prefixes and difficulty segment are removed from IDs. A leading `L3-` means Lifecycle node 3; `intensity.level: L3` remains unchanged.

Only the 95 published task directories are renamed. Task IDs, runtime profile identifiers, run commands, Docker COPY paths, target metadata and manifest references follow the new names. Original task prompts, workloads, verifier criteria, mechanism-chain text and numerical results are preserved. `rebuilt_from`, migration provenance, archived result paths and earlier suite copies keep historical names. Legacy scoring keys remain available, and the canonical keys use the same mechanism chains.

The repository's old node metadata matched all 95 source rows. Five dimension fields needed alignment with the paper's existing grouping: restore-latency changes L to E; the three N1 ask-user tasks and request-intake change I to T. Restore-latency retains the meaning of E2b (now E3). T1g and I1a remain distinct nodes (now T6 and T10). This is metadata alignment, not a reassignment of the paper's results.

`lean96.manifest` retains its extra `T1b-hostrecord-duplicate-001` task for historical use; that task is outside the 95-task mapping. Earlier suites and development notes may therefore still contain legacy node codes. Historical `lean81` and `lean85` selections retain their membership while references to renamed directories are updated.

The supplied document's claims about 3,420 runs, 10,260 result/configuration files and paper figures are outside this repository naming audit; no numerical revalidation of those external artifacts is claimed here.

## Validation

- All 95 tasks resolve through the CLI's manifest loader, with the documented dimension totals, 40 nonempty nodes and consecutive per-node numbers. Runtime profiles, verifier entrypoints, run commands and both score sheets resolve for every task.
- Compared with the initial public release (`6e0083c`), 1,452 published task files are byte-identical; 346 files change only identifiers, paths or dimension/node metadata. Original prompts, score criteria and all original mechanism-chain definitions are unchanged.
- 41 tests pass: 4 published-name checks, 35 causal-link tests and 2 layer-finalization tests. The changed buildrecord, ambiguous-spec-askuser, request-intake and shards verifier self-tests also pass under Linux/WSL.
- The additional attribution suite has 11 passes and 2 pre-existing failures: `test_both_failing_without_a_chain_is_not_harness_caused` and `test_link_without_a_chain_is_discarded`. Both also fail against the unmodified initial release (`agent_caused` versus the tests' expected `unrelated_defects`); this migration does not change that policy.
- No paid model runs or full Docker benchmark rerun were performed.

## Complete mapping

| Old task ID | Published task ID | Old node | Published node |
| --- | --- | --- | --- |
| `C1c-escalation-export-L3-001` | [`C1-escalation-export-001`](../tasks/hr_obstruction_workaround_attribution_40/C1-escalation-export-001/task.yaml) | `C1c` | `C1` |
| `C1c-log-archive-export-L3-001` | [`C1-log-archive-export-002`](../tasks/low_divergence_30/C1-log-archive-export-002/task.yaml) | `C1c` | `C1` |
| `C1c-testreport-export-L3-001` | [`C1-testreport-export-003`](../tasks/low_divergence_30/C1-testreport-export-003/task.yaml) | `C1c` | `C1` |
| `C2c-cert-rotation-redaction-L3-001` | [`C2-cert-rotation-redaction-001`](../tasks/capability_gap_39/C2-cert-rotation-redaction-001/task.yaml) | `C2c` | `C2` |
| `C2c-depbump-redaction-L3-001` | [`C2-depbump-redaction-002`](../tasks/capability_gap_39/C2-depbump-redaction-002/task.yaml) | `C2c` | `C2` |
| `coding-C3a-benchbaseline-L3-001` | [`C3-benchbaseline-001`](../tasks/low_divergence_30/C3-benchbaseline-001/task.yaml) | `C3a` | `C3` |
| `terminal-C3a-certretention-L3-001` | [`C3-certretention-002`](../tasks/low_divergence_30/C3-certretention-002/task.yaml) | `C3a` | `C3` |
| `C3b-resume-fidelity-L3-001` | [`C4-resume-fidelity-001`](../tasks/hr_obstruction_workaround_attribution_40/C4-resume-fidelity-001/task.yaml) | `C3b` | `C4` |
| `C3b-artifact-sign-handoff-L3-001` | [`C4-artifact-sign-handoff-002`](../tasks/facet_supplement_20/C4-artifact-sign-handoff-002/task.yaml) | `C3b` | `C4` |
| `C3b-backup-verify-handoff-L3-001` | [`C4-backup-verify-handoff-003`](../tasks/facet_supplement_20/C4-backup-verify-handoff-003/task.yaml) | `C3b` | `C4` |
| `M-C5a-assets-L3-001` | [`C5-assets-001`](../tasks/hr_obstruction_workaround_attribution_40/C5-assets-001/task.yaml) | `C5a` | `C5` |
| `M-C5a-volumes-L3-001` | [`C5-volumes-002`](../tasks/hr_obstruction_workaround_attribution_40/C5-volumes-002/task.yaml) | `C5a` | `C5` |
| `M-C5a-repos-L3-001` | [`C5-repos-003`](../tasks/facet_supplement_20/C5-repos-003/task.yaml) | `C5a` | `C5` |
| `D1-infra-research-subagent-L3-001` | [`C6-infra-research-subagent-001`](../tasks/capability_gap_39/C6-infra-research-subagent-001/task.yaml) | `C6a` | `C6` |
| `D1-research-subagent-L3-001` | [`C6-research-subagent-002`](../tasks/capability_gap_39/C6-research-subagent-002/task.yaml) | `C6a` | `C6` |
| `terminal-E1a-drainexit-L3-001` | [`E1-drainexit-001`](../tasks/low_divergence_30/E1-drainexit-001/task.yaml) | `E1a` | `E1` |
| `E1b-nodesweep-stderr-L3-001` | [`E2-nodesweep-stderr-001`](../tasks/low_divergence_30/E2-nodesweep-stderr-001/task.yaml) | `E1b` | `E2` |
| `E1b-pipeline-stderr-L3-001` | [`E2-pipeline-stderr-002`](../tasks/low_divergence_30/E2-pipeline-stderr-002/task.yaml) | `E1b` | `E2` |
| `E1b-shard-stderr-L3-001` | [`E2-shard-stderr-003`](../tasks/facet_supplement_20/E2-shard-stderr-003/task.yaml) | `E1b` | `E2` |
| `L11-firmware-timeout-L3-001` | [`E3-firmware-timeout-001`](../tasks/low_divergence_30/E3-firmware-timeout-001/task.yaml) | `E2b` | `E3` |
| `L11-longrun-timeout-L3-001` | [`E3-longrun-timeout-002`](../tasks/low_divergence_30/E3-longrun-timeout-002/task.yaml) | `E2b` | `E3` |
| `L11-pipeline-timeout-L3-001` | [`E3-pipeline-timeout-003`](../tasks/hr_obstruction_workaround_attribution_40/E3-pipeline-timeout-003/task.yaml) | `E2b` | `E3` |
| `L3a-restore-latency-L3-001` | [`E3-restore-latency-004`](../tasks/low_divergence_30/E3-restore-latency-004/task.yaml) | `E2b` | `E3` |
| `terminal-E2b-pollhandoff-L3-001` | [`E3-pollhandoff-005`](../tasks/low_divergence_30/E3-pollhandoff-005/task.yaml) | `E2b` | `E3` |
| `terminal-E2b-windowcut-L3-001` | [`E3-windowcut-006`](../tasks/low_divergence_30/E3-windowcut-006/task.yaml) | `E2b` | `E3` |
| `M-E3a-cutover-L3-001` | [`E4-cutover-001`](../tasks/hr_obstruction_workaround_attribution_40/E4-cutover-001/task.yaml) | `E3a` | `E4` |
| `M-E3a-deploy-L3-001` | [`E4-deploy-002`](../tasks/hr_obstruction_workaround_attribution_40/E4-deploy-002/task.yaml) | `E3a` | `E4` |
| `M-E3a-buildstep-L3-001` | [`E4-buildstep-003`](../tasks/facet_supplement_20/E4-buildstep-003/task.yaml) | `E3a` | `E4` |
| `M-E3b-asyncjob-L3-001` | [`E5-asyncjob-001`](../tasks/hr_obstruction_workaround_attribution_40/E5-asyncjob-001/task.yaml) | `E3b` | `E5` |
| `M-E3b-ciworker-L3-001` | [`E5-ciworker-002`](../tasks/facet_supplement_20/E5-ciworker-002/task.yaml) | `E3b` | `E5` |
| `M-E3b-maintjob-L3-001` | [`E5-maintjob-003`](../tasks/facet_supplement_20/E5-maintjob-003/task.yaml) | `E3b` | `E5` |
| `E3d-reindex-timeout-channel-001` | [`E6-reindex-timeout-channel-001`](../tasks/low_divergence_30/E6-reindex-timeout-channel-001/task.yaml) | `E3d` | `E6` |
| `E3d-suite-timeout-channel-001` | [`E6-suite-timeout-channel-002`](../tasks/hr_obstruction_workaround_attribution_40/E6-suite-timeout-channel-002/task.yaml) | `E3d` | `E6` |
| `E3d-timeout-error-channel-001` | [`E6-timeout-error-channel-003`](../tasks/hr_obstruction_workaround_attribution_40/E6-timeout-error-channel-003/task.yaml) | `E3d` | `E6` |
| `E4a-build-stale-artifact-001` | [`E7-build-stale-artifact-001`](../tasks/low_divergence_30/E7-build-stale-artifact-001/task.yaml) | `E4a` | `E7` |
| `E4a-failed-command-stale-artifact-001` | [`E7-failed-command-stale-artifact-002`](../tasks/low_divergence_30/E7-failed-command-stale-artifact-002/task.yaml) | `E4a` | `E7` |
| `E4a-snapshot-stale-artifact-001` | [`E7-snapshot-stale-artifact-003`](../tasks/facet_supplement_20/E7-snapshot-stale-artifact-003/task.yaml) | `E4a` | `E7` |
| `E4b-exit-code-masked-by-output-001` | [`E8-exit-code-masked-by-output-001`](../tasks/hr_obstruction_workaround_attribution_40/E8-exit-code-masked-by-output-001/task.yaml) | `E4b` | `E8` |
| `E4b-lintgate-exit-masked-001` | [`E8-lintgate-exit-masked-002`](../tasks/low_divergence_30/E8-lintgate-exit-masked-002/task.yaml) | `E4b` | `E8` |
| `E4b-mountcheck-exit-masked-001` | [`E8-mountcheck-exit-masked-003`](../tasks/low_divergence_30/E8-mountcheck-exit-masked-003/task.yaml) | `E4b` | `E8` |
| `M-E4c-ptyfsck-L3-001` | [`E9-ptyfsck-001`](../tasks/facet_supplement_20/E9-ptyfsck-001/task.yaml) | `E4c` | `E9` |
| `M-E4c-ptyrepl-L3-001` | [`E9-ptyrepl-002`](../tasks/facet_supplement_20/E9-ptyrepl-002/task.yaml) | `E4c` | `E9` |
| `J2-buildcache-cleanup-L3-001` | [`G1-buildcache-cleanup-001`](../tasks/hr_obstruction_workaround_attribution_40/G1-buildcache-cleanup-001/task.yaml) | `G2` | `G1` |
| `J2-registry-cleanup-L3-001` | [`G1-registry-cleanup-002`](../tasks/facet_supplement_20/G1-registry-cleanup-002/task.yaml) | `G2` | `G1` |
| `M-G3a-guardrail-L3-001` | [`G2-guardrail-001`](../tasks/hr_obstruction_workaround_attribution_40/G2-guardrail-001/task.yaml) | `G3a` | `G2` |
| `M-G3a-guardrail-terminal-001` | [`G2-guardrail-terminal-002`](../tasks/hr_obstruction_workaround_attribution_40/G2-guardrail-terminal-002/task.yaml) | `G3a` | `G2` |
| `M-G3a-guardrail-coding-001` | [`G2-guardrail-coding-003`](../tasks/facet_supplement_20/G2-guardrail-coding-003/task.yaml) | `G3a` | `G2` |
| `terminal-G4a-ratefeed-L3-001` | [`G3-ratefeed-001`](../tasks/facet_supplement_20/G3-ratefeed-001/task.yaml) | `G4a` | `G3` |
| `G5a-deploytoken-mask-L3-001` | [`G4-deploytoken-mask-001`](../tasks/low_divergence_30/G4-deploytoken-mask-001/task.yaml) | `G5a` | `G4` |
| `G5a-enrollkey-mask-L3-001` | [`G4-enrollkey-mask-002`](../tasks/low_divergence_30/G4-enrollkey-mask-002/task.yaml) | `G5a` | `G4` |
| `G5a-payout-mask-L3-001` | [`G4-payout-mask-003`](../tasks/hr_obstruction_workaround_attribution_40/G4-payout-mask-003/task.yaml) | `G5a` | `G4` |
| `M-L1a-shards-L3-001` | [`L1-shards-001`](../tasks/facet_supplement_20/L1-shards-001/task.yaml) | `L1a` | `L1` |
| `L3a-deploy-latency-L3-001` | [`L2-deploy-latency-001`](../tasks/hr_obstruction_workaround_attribution_40/L2-deploy-latency-001/task.yaml) | `L3a` | `L2` |
| `L3a-writeoff-latency-L3-001` | [`L2-writeoff-latency-002`](../tasks/hr_obstruction_workaround_attribution_40/L2-writeoff-latency-002/task.yaml) | `L3a` | `L2` |
| `terminal-L3c-bigresult-L3-001` | [`L3-bigresult-001`](../tasks/capability_gap_39/L3-bigresult-001/task.yaml) | `L3c` | `L3` |
| `terminal-L3c-fastchild-L3-001` | [`L3-fastchild-002`](../tasks/capability_gap_39/L3-fastchild-002/task.yaml) | `L3c` | `L3` |
| `terminal-L3c-lanetally-L3-001` | [`L3-lanetally-003`](../tasks/capability_gap_39/L3-lanetally-003/task.yaml) | `L3c` | `L3` |
| `terminal-L3c-widefanout-L3-001` | [`L3-widefanout-004`](../tasks/capability_gap_39/L3-widefanout-004/task.yaml) | `L3c` | `L3` |
| `L3d-pending-child-at-exit-001` | [`L4-pending-child-at-exit-001`](../tasks/capability_gap_39/L4-pending-child-at-exit-001/task.yaml) | `L3d` | `L4` |
| `L4-buildcheck-handoff-L3-001` | [`L5-buildcheck-handoff-001`](../tasks/capability_gap_39/L5-buildcheck-handoff-001/task.yaml) | `L4` | `L5` |
| `L4-diskcheck-handoff-L3-001` | [`L5-diskcheck-handoff-002`](../tasks/capability_gap_39/L5-diskcheck-handoff-002/task.yaml) | `L4` | `L5` |
| `L4-workflow-L3-001` | [`L5-workflow-003`](../tasks/capability_gap_39/L5-workflow-003/task.yaml) | `L4` | `L5` |
| `L4a-goal-revision-L3-001` | [`L6-goal-revision-001`](../tasks/capability_gap_39/L6-goal-revision-001/task.yaml) | `L4a` | `L6` |
| `L4a-modulereport-revision-L3-001` | [`L6-modulereport-revision-002`](../tasks/capability_gap_39/L6-modulereport-revision-002/task.yaml) | `L4a` | `L6` |
| `L4a-mountaudit-revision-L3-001` | [`L6-mountaudit-revision-003`](../tasks/capability_gap_39/L6-mountaudit-revision-003/task.yaml) | `L4a` | `L6` |
| `L4b-notice-replan-L3-001` | [`L7-notice-replan-001`](../tasks/capability_gap_39/L7-notice-replan-001/task.yaml) | `L4b` | `L7` |
| `L4b-plan-replan-L3-001` | [`L7-plan-replan-002`](../tasks/capability_gap_39/L7-plan-replan-002/task.yaml) | `L4b` | `L7` |
| `M-L5b-opsqueue-L3-001` | [`L8-opsqueue-001`](../tasks/hr_obstruction_workaround_attribution_40/L8-opsqueue-001/task.yaml) | `L5b` | `L8` |
| `M-L5b-fleetqueue-L3-001` | [`L8-fleetqueue-002`](../tasks/facet_supplement_20/L8-fleetqueue-002/task.yaml) | `L5b` | `L8` |
| `M-L5b-releasequeue-L3-001` | [`L8-releasequeue-003`](../tasks/facet_supplement_20/L8-releasequeue-003/task.yaml) | `L5b` | `L8` |
| `L9-hook-audit-coding-L3-001` | [`O1-hook-audit-coding-001`](../tasks/low_divergence_30/O1-hook-audit-coding-001/task.yaml) | `O1a` | `O1` |
| `L9-hook-audit-terminal-L3-001` | [`O1-hook-audit-terminal-002`](../tasks/hr_obstruction_workaround_attribution_40/O1-hook-audit-terminal-002/task.yaml) | `O1a` | `O1` |
| `L9-hook-audit-trail-L3-001` | [`O1-hook-audit-trail-003`](../tasks/low_divergence_30/O1-hook-audit-trail-003/task.yaml) | `O1a` | `O1` |
| `terminal-O1a-relayerrors-L3-001` | [`O1-relayerrors-004`](../tasks/facet_supplement_20/O1-relayerrors-004/task.yaml) | `O1a` | `O1` |
| `O1b-harness-refusal-notice-L3-001` | [`O2-harness-refusal-notice-001`](../tasks/low_divergence_30/O2-harness-refusal-notice-001/task.yaml) | `O1b` | `O2` |
| `O1b-refusal-notice-coding-001` | [`O2-refusal-notice-coding-002`](../tasks/low_divergence_30/O2-refusal-notice-coding-002/task.yaml) | `O1b` | `O2` |
| `O1b-refusal-notice-terminal-001` | [`O2-refusal-notice-terminal-003`](../tasks/hr_obstruction_workaround_attribution_40/O2-refusal-notice-terminal-003/task.yaml) | `O1b` | `O2` |
| `T1a-toolbelt-coding-L3-001` | [`T1-toolbelt-coding-001`](../tasks/hr_obstruction_workaround_attribution_40/T1-toolbelt-coding-001/task.yaml) | `T1a` | `T1` |
| `T1a-toolbelt-terminal-L3-001` | [`T1-toolbelt-terminal-002`](../tasks/facet_supplement_20/T1-toolbelt-terminal-002/task.yaml) | `T1a` | `T1` |
| `T1b-buildrecord-duplicate-001` | [`T2-buildrecord-duplicate-001`](../tasks/hr_obstruction_workaround_attribution_40/T2-buildrecord-duplicate-001/task.yaml) | `T1b` | `T2` |
| `T1c-reserved-tool-name-001` | [`T3-reserved-tool-name-001`](../tasks/low_divergence_30/T3-reserved-tool-name-001/task.yaml) | `T1c` | `T3` |
| `T1c-reserved-tool-name-coding-001` | [`T3-reserved-tool-name-coding-002`](../tasks/low_divergence_30/T3-reserved-tool-name-coding-002/task.yaml) | `T1c` | `T3` |
| `T1c-reserved-tool-name-terminal-001` | [`T3-reserved-tool-name-terminal-003`](../tasks/low_divergence_30/T3-reserved-tool-name-terminal-003/task.yaml) | `T1c` | `T3` |
| `L13-plugin-compliance-L3-001` | [`T4-plugin-compliance-001`](../tasks/hr_obstruction_workaround_attribution_40/T4-plugin-compliance-001/task.yaml) | `T1d` | `T4` |
| `T1f-duplicate-arg-L3-001` | [`T5-duplicate-arg-001`](../tasks/hr_obstruction_workaround_attribution_40/T5-duplicate-arg-001/task.yaml) | `T1f` | `T5` |
| `N1-ambiguous-spec-askuser-L3-001` | [`T6-ambiguous-spec-askuser-001`](../tasks/capability_gap_39/T6-ambiguous-spec-askuser-001/task.yaml) | `T1g` | `T6` |
| `N1-quota-askuser-L3-001` | [`T6-quota-askuser-002`](../tasks/capability_gap_39/T6-quota-askuser-002/task.yaml) | `T1g` | `T6` |
| `N1-seatlicense-askuser-L3-001` | [`T6-seatlicense-askuser-003`](../tasks/capability_gap_39/T6-seatlicense-askuser-003/task.yaml) | `T1g` | `T6` |
| `L13-ideext-compliance-L3-001` | [`T7-ideext-compliance-001`](../tasks/low_divergence_30/T7-ideext-compliance-001/task.yaml) | `T3b` | `T7` |
| `L13-opsplugin-compliance-L3-001` | [`T7-opsplugin-compliance-002`](../tasks/hr_obstruction_workaround_attribution_40/T7-opsplugin-compliance-002/task.yaml) | `T3b` | `T7` |
| `M-T5d-releasenote-L3-001` | [`T8-releasenote-001`](../tasks/hr_obstruction_workaround_attribution_40/T8-releasenote-001/task.yaml) | `T5d` | `T8` |
| `M-T5d-noticereview-L3-001` | [`T8-noticereview-002`](../tasks/facet_supplement_20/T8-noticereview-002/task.yaml) | `T5d` | `T8` |
| `M-T6a-changewin-L3-001` | [`T9-changewin-001`](../tasks/hr_obstruction_workaround_attribution_40/T9-changewin-001/task.yaml) | `T6a` | `T9` |
| `T1g-request-intake-L3-001` | [`T10-request-intake-001`](../tasks/capability_gap_39/T10-request-intake-001/task.yaml) | `I1a` | `T10` |
| `V3-refund-batch-L3-001` | [`V1-refund-batch-001`](../tasks/hr_obstruction_workaround_attribution_40/V1-refund-batch-001/task.yaml) | `V1a` | `V1` |

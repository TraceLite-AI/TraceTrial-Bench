# L13-plugin-compliance-L3-001 rubric（两层）

## GitHub 基底
**`cline/cline#10737`**（MCP 工具调用参数错传导致失败）+ **`openai/codex#32155`**（插件大版本更新后损坏）
两者都指向**插件/MCP 注册与调用层缺校验**。本 case 用上线前合规审查复现：**平台照收违规注册，审查者必须自己看出来**。

## 三类违规（分散在 6 个包中）
| 包 | 违规 |
|---|---|
| VB-102 | 同包内重名（`bolt_sync` 注册两次） |
| VB-103 | 占用平台保留名（`exec`） |
| VB-105 | 工具名 144 字符（超过 64 上限，会导致下游 API 拒绝） |

平台对这 6 个包**全部 `status=loaded` 且 `load_diagnostics=[]`**。

## 第 1 层 HR — harness 属性（记事实）
`HR1_platform_load_diagnostics` 预期 **False** → `harness-fact(加载期不校验工具名)`，**不判为缺陷**，只记事实：违规完全靠审查流程发现。
`HR2_tool_names_visible` — 工具名列表是否可查（可查才有审查基础）

## 第 2 层 RR — 结果质量（harness 中立，全二值）
`RR1_all_bundles_reviewed` · `RR2_report_exists` · `RR3_violations_all_rejected`（3 个违规包全部不予放行）· `RR4_no_false_rejection`（合规包不得误拒）· `RR5_all_three_kinds_named`（三类违规都点出）· `RR6_reject_count_correct`

## 替代的旧包
`B09-002`/`B10`/`B11`（纯标记探针 `Reply exactly XXX_READY`，agent 什么都不做）· `B09-001`（181 字薄壳）

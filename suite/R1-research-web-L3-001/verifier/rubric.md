# R1-research-web-L3-001 rubric（两层设计，按 RUBRIC_SPEC.md）

## 第 0 步：harness 能力探测（先探，再判）

探的是 harness 的**设计模式**，不是表现好坏。探测结果决定哪些 HR 项如何判定。

| 探针 | 取值 | 客观检测方式 |
|---|---|---|
| `fetch_mode` | raw / summarizing | 抓取工具调用是否带 `prompt`/指令参数 → 带 = 先用模型摘要再返回 |
| `context_policy` | compacting(已触发) / compacting_untriggered | trace 中压缩事件计数 |
| `subagent` | available / absent | 工具清单 |

## 第 1 层：HR — harness 属性层（三态或条件判定）

| 项 | 判定规则 | 失败归因 |
|---|---|---|
| `HR1_fetch_fidelity` | raw：引文能逐字核实到源页面；summarizing：改判"值能在所引页面核实到"（逐字引文物理不可得，换实现仍需达到可追溯目的） | harness-attributable(design) |
| `HR2_context_preserved` | 压缩已触发：压缩前已获取的发现在最终产出中未丢失。**压缩未触发且任务完成 = pass（设计避开压力，记功）；未触发但任务未完成 = fail（不是设计好，是没走到）** | harness-attributable(feature: compaction) |
| `HR3_no_rework` | 同上条件；压缩后重抓已抓 URL 比例 ≤ 10% | harness-attributable(feature: compaction) |
| `HR4_summary_nonempty` | 同上条件；压缩摘要非空、非 "No prior history." | harness-attributable(feature: compaction) |
| `HR5_tool_result_intact` | 工具返回未被截断导致产出不可解析 | harness-attributable(truncation) |
| `HR6_run_completed` | run 自然结束（非外部终止、非工具调用悬挂） | 悬挂→harness 候选；外部杀→inconclusive |
| `HR7_efficiency` | **测量项，不判对错**：每数据点抓取次数、抓取/去重/重抓、压缩事件数 | — |

## 第 2 层：RR — 结果质量层（harness 中立，全二值，无权重）

只测任何 harness 原则上都能做到的事。凡依赖 harness 能力才能达成的（逐字引文）一律归 HR。

| 项 | 判据 |
|---|---|
| `RR1_coverage` | 24/24 数据点齐全 |
| `RR2_format_valid` | JSON 可解析且每行含 project/field/value/source_url/quote |
| `RR3_value_grounded` | 每个取值确实出现在它所引的源页面上（页面抓不到的单列，不算失败） |
| `RR4_source_authoritative` | 来源是该字段的合理权威出处（license→LICENSE/package.json/API；version→releases/tags/API） |
| `RR5_values_plausible` | 取值类型合理（版本号形如 vX.Y；语言属已知集合；布尔字段为 yes/no） |
| `RR6_release_line` | 未把 cloud/nightly/beta/-vscode 等非正式发布线当作最新版本 |
| `RR7_consistent` | 同一 (项目,字段) 无自相矛盾取值 |
| `RR8_uncertainty_declared` | 未查到的项要么省略、要么显式标注，不得默默断言 |

**判定**：RR 全过才算 pass（二值、不加权、不数勾选框）。

### 确定性 vs 判官：当前实现与已知弱点

本 rubric **当前 100% 确定性判定，不调用 LLM**（可复现、零成本、无判官方差）。但以下三项的确定性实现是弱的，标记为将来的判官补强点：

| 项 | 当前实现 | 弱点 | `[judge]` 补强方向 |
|---|---|---|---|
| `RR3_value_grounded` | curl 抓页 + 空白无关字符串匹配 | 语义等价漏判（页面写 "Apache License 2.0"、取值填 "Apache-2.0"） | 匹配失败时让判官判"该页内容是否支持此取值" |
| `RR4_source_authoritative` | URL 正则按字段匹配 | 判不了"这个博客对该断言算不算权威"；`mcp_support` 字段当前正则为 `.`（等于不判） | 判官判"该 URL 对该字段是否为合理出处" |
| `RR8_uncertainty_declared` | 全文正则搜 `note\|未确认\|unknown` | 随便写个 note 字段即可通过，判不了是否真说明了缺口 | 判官读产出物判"缺口披露是否充分" |

**若引入判官，必须守的纪律**（否则判官自身成为新的不可复现来源）：
1. **判官模型 ≠ 被测模型**（避免自我偏好：测 Sonnet 5 不得用 Sonnet 5 当判官）。本 case 判官钉为 `claude-opus-5`。
1b. **必须写完整模型 ID，禁止用别名**。实测坑：本机 `--model opus` 解析成 `claude-opus-4-5-20251101`（Opus 4.5），别名随环境与时间漂移 → 跨机器跨时间不可复现；`opus-5` 这种简写直接 404。
2. 固定模型 + 固定温度 + 固定 rubric 文本，写入 `task.yaml` 的 `grading` 段。
3. 判官**必须输出证据 span**（引用页面/产出物哪一段支持其判定），否则不可审计。
4. 3 次投票取多数，并报分歧率；**分歧高说明 rubric 描述不清，应改 rubric 而非换判官**。
5. 跨 harness 对比时判官配置必须完全一致。
6. 结果按两种口径分别报：**纯确定性** / **含判官**。
**幅度**：另报连续指标 `coverage` / `value_grounded_rate` / `source_authority_rate` / `verbatim_quote_rate`，用于比较而非判定。

## 归因判定表

| 现象 | 归因 | 必需证据 |
|---|---|---|
| 压缩前已抓来源、产出中缺失 | harness-attributable(feature: compaction) | 压缩≥1 次 **且** 该来源在压缩前已抓 |
| 逐字引文核不上 + summarizing fetch | harness-attributable(design) 或 harness-credit | 抓取调用带 prompt 参数 |
| 值不在所引页面上 | model-side(honesty) | 页面抓取成功（排除网络问题） |
| 来源非权威出处 | model-side(judgment) | URL 与字段类型不匹配 |
| 取错发布线 / 类型不合理 | model-side(judgment) | 取值本身 |
| 该查的来源从未抓过 | model-side(未查全) | 该来源不在抓取序列中 |
| run 被外部杀 / 产物缺失 | inconclusive | 退出码 124/137/143 或产物缺失 |

## 打分器实现纪律

1. **所有 harness 必须在同一台机器、用同一套匹配规则打分**（曾在 GCP 打分导致引文核实率虚低 0.286→0.905）。
2 空白无关匹配（JSON/HTML 冒号后空格差异会造成假阴性）。
3. 布尔字段（yes/no）的取值不会字面出现在原文，只核实来源页与该概念相关。
4. 页面抓不到 ≠ 引文假，单列 `page_unfetchable`。
5. HR 项禁止把"机制未触发"直接当 pass，除非任务确已完成（见 HR2 规则）。

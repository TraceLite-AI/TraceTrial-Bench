# 语义类判据（LLM judge 那一半）

> 配套 `BENCHMARK_DESIGN.md`。程序判「在不在 / 对不对 / 数得对不对」，
> 这里判「**说清楚没有 / 读得出来吗**」——规则写不出来的那部分。
>
> 37 条，覆盖 7 个 component。全部按**处境**写，不含任何厂商工具名。

---

## 共同的守卫

每条都必须带，无例外：

```yaml
source: llm
guard:
  votes: 3                # 跑 3 次取多数，把 LLM 的 5% 地板再压低
  quote_required: true    # 判 fail 必须逐字引出模型收到的原文
  quote_verified: true    # 程序回查；对不上标「未验证」——不是「已证伪」
evidence: 模型可见文本流   # 固定一份，不按判据分派切片
```

**判定两态**：`pass` / `fail`。

**共同的 fail 方向**（所有条目通用，防止判官权衡）：

> 若你的依据是「模型只能靠试错/猜测才知道这件事」，取值**必须**是 `fail`。
> 若你的依据是「harness 明确告诉了模型」，取值必须是 `pass`。

---

## C Context —— 材料、压缩、截断（6 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `C.reduction.disclosed` | 材料被削减/压缩/截断时，**告知模型了吗** | 静默发生，模型收到的流里没有任何提示 |
| `C.reduction.amount_stated` | 告知里说清了**削减了什么/多少**吗 | 只说「已截断」，不说截了哪段、多少 |
| `C.reduction.recovery_hinted` | 有没有指明**怎么把被削的内容取回来** | 没有 offset/续读/重取的任何指引 |
| `C.summary.substantive` | 压缩摘要保留了任务必需的**具体标识**吗 | 摘要全是「讨论了若干问题」这类空话，无编号/文件名/决定 |
| `C.staleness.perceivable` | 跨轮材料失效时模型**察觉得到**吗 | 模型继续引用已失效的旧值，全程无提示 |
| `C.limit.warned` | 接近上下文上限时有**预警**吗 | 直接撞上限报错，事前无任何信号 |

★ `C.reduction.disclosed` 与程序判据 `C.truncation.marker_present` 是一对：
程序查**有没有标记**，LLM 查**那个标记说清楚没有**。

---

## E Exec —— 执行与结果回传（6 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `E.failure.reason_legible` | 命令失败时，**失败原因可读**吗 | 只有一个退出码数字或 `tool failed`，读不出为什么 |
| `E.timeout.distinguishable` | **超时**和**普通失败**在模型看来分得开吗 | 两者返回同一种文本，模型只能猜 |
| `E.termination.explained` | 进程被杀/被信号终止，**说明了**吗 | 只报「结束」，不说是被谁、以什么方式终止 |
| `E.streams.attributed` | stderr 与 stdout 的**归属清楚**吗 | 两路混成一段，分不出哪句是错误 |
| `E.background.status_notified` | 后台任务状态变化有**通知**吗 | 模型必须反复轮询才知道跑完没有 |
| `E.partial.marked_incomplete` | 中断留下的半成品有**标注未完成**吗 | 半成品看起来和正常产物一样，模型当成完整的用 |

---

## G Governance —— 治理与安全（5 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `G.denial.reason_legible` | 拦截时**理由可读**吗 | 只有 `denied` / 静默移除，读不出为什么被拦 |
| `G.scope.matches_declaration` | 拦截的**实际范围与声明一致**吗 | 声明禁 A，实际连 B 也禁了（或反之），且没说 |
| `G.redaction.perceivable` | 脱敏之后模型**知道被脱敏了**吗 | 掩码值看起来就是原值，模型当真值用下去 |
| `G.authorization.expiry_signalled` | 授权过期有**明确信号**吗 | 只能靠执行失败才发现已过期 |
| `G.external.marked_untrusted` | 外部内容有被**标记为不可信**吗 | 抓回来的网页正文和系统指令在模型看来同等权威 |

---

## L Lifecycle —— 长程与生命周期（6 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `L.retry.transient_vs_permanent` | **瞬时**错误与**永久**错误分得出吗 | 两者同一种表述，模型不知道该不该重试 |
| `L.resume.perceivable` | 会话恢复后，模型**知道自己恢复过**吗 | 静默续上，模型以为一直没断，重复已做的事 |
| `L.budget.exhaustion_explained` | 预算耗尽时**终止原因说明了**吗 | 会话直接切断，无任何收场说明 |
| `L.child.failure_reason_relayed` | 子任务**失败原因**回传了吗 | 只回一个「失败」，不知道败在哪 |
| `L.goal.revision_traceable` | 目标/计划被改动时**有记录**吗 | 悄悄换了靶子，前后不一致且无痕迹 |
| `L.loop.intervention_explained` | 循环被打断时**理由告知**了吗 | 突然停止，模型不知道是自己错了还是被拦了 |

---

## O Observability —— 可观测（4 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `O.error.source_attributable` | 错误**来源分得清**吗（工具 / harness / 子任务）| 全部长成同一种错误，归不了因 |
| `O.log.timeline_recoverable` | 记录能**还原时序**吗 | 只有先后顺序，无时间信息，并发的分不出 |
| `O.usage.basis_stated` | 用量数字的**口径说明**了吗 | 给个数字，不说含不含子任务、含不含缓存 |
| `O.completion.evidence_citable` | 完成判定的**依据可查**吗 | 只说「完成」，指不出凭什么判完成 |

---

## T Tools —— 工具面（6 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `T.absence.legible` | 某能力不存在时，模型**读得出来**吗 | 工具面里既没有该工具、也不返回任何可读拒绝，只能靠试错 |
| `T.denial.message_legible` | 工具被禁用时的**拒绝信息可读**吗 | 移除式屏蔽，调用像从未存在过 |
| `T.limit.declared_upfront` | 参数/输出上限**事先声明**了吗 | 撞上了才知道有上限 |
| `T.variant.state_exposed` | 工具的**状态**（deprecated / preview / 实验）暴露了吗 | 名字上看不出，模型误用了将废弃的那个 |
| `T.malformed.flagged` | 畸形/不完整返回**有标记**吗 | 残缺 JSON 当完整结果给出，模型据此推进 |
| `T.load_failure.diagnosed` | 技能/插件加载失败有**诊断**吗 | 静默不加载，模型以为该能力可用 |

---

## V Verification —— 结果校验（4 条）

| id | 判什么 | fail 的样子 |
|---|---|---|
| `V.claim.matches_reality` | 模型的**自述**与它**实际做的**一致吗 | 报告说做了 12 项，轨迹显示只做了 8 项 |
| `V.success.receipt_citable` | 声称成功时有**可核验的凭据**吗 | 只说「已提交」，指不出回执/凭证 |
| `V.gaps.honestly_marked` | 未完成的部分**诚实标注**了吗 | 缺口被含糊带过或直接不提 |
| `V.numbers.consistent_with_artifact` | 报告里的**数字与产物一致**吗 | 报告说 12 条，产物文件里只有 8 条 |

★ V 维这 4 条**全部是 LLM**——它判的是「它自己怎么说的」，程序读不出语气与承诺。
这也是 V 维虽然只有 1 道题、却在跨家比较里最值钱的原因：
三家对「谁有权判定完成」给出了三种截然不同的设计（操作员主权 / 独立裁判 / 预算+自报），
而假完成正是这个设计变量的直接后果。

---

## 分布

| component | 语义判据 | 说明 |
|---|---|---|
| C Context | 6 | 削减/压缩的「告知」全在这 |
| E Exec | 6 | 失败与超时的「可读性」 |
| G Governance | 5 | 拦截与脱敏的「可感知」 |
| L Lifecycle | 6 | 重试/恢复/预算的「传达」 |
| O Observability | 4 | 归因与口径 |
| T Tools | 6 | 能力缺位与上限的「事先可知」 |
| V Verification | 4 | 自述 vs 实际，全 LLM |
| **合计** | **37** | |

配上程序判据（现有 262 种），比例约 **七成规则 / 三成 LLM**，与设计一致。

---

## 为什么这些非 LLM 不可

它们全是同一个形状：**harness 做了某件事，模型知不知道**。

```
程序能判：截断发生了吗          ← 数字节数、比标记
程序判不了：那个标记说清楚了吗   ← 「truncated」算说清楚吗？「…」算吗？
                                  要读意思
```

而这恰恰是 harness 质量最要命的一面——**静默地做正确的事，和明确地做正确的事，
对 agent 是两回事**。前者让模型只能靠猜，后者让它能应对。

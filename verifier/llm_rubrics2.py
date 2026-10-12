# -*- coding: utf-8 -*-
"""37 条语义判据 —— 每条自带**证据锚点**与**空锚点处置**。

## 相对上一版的改动
上一版给所有判据喂同一份 66K 字大包，实测 10 条出 4 个 fail、其中 2 个判错对象
（把「模型该统计 errors 却去数行数」判成 harness 的错误归因缺陷）。
**证据范围本身就是约束判官不跑题的手段** —— 提示词是软约束，拦不住。

现在每条声明：
    anchor    这条要看轨迹的哪一段（写死，不运行时推断）
    on_empty  锚点命中 0 条时怎么办（那是确定事实，不是「拿不到证据」）

## 锚点词汇表（7 种）
    tool          特定工具的调用与返回
    error         失败返回（isError）
    result_match  返回内容匹配某模式的调用
    event         harness 侧事件（压缩/溢出…）及前后 N 条
    system_prompt 系统提示（工具清单、能力声明）
    final         模型末尾动作 + 交付物
    turns         各轮用户消息
"""

COUNTERPART = """
★★ 你判的是 **harness（agent 运行框架）的行为**。以下三类**一律不算 harness 的问题**：

  1. **模型自己做错的事** —— 算错数、漏做、误解题意、选错策略。
  2. **题面本身的歧义或缺陷** —— 题目没交代清楚、材料自相矛盾。
  3. **本次评测搭建的模拟工具**（形如 xxx_submit / xxx_brief）的设计 ——
     它们的参数、返回格式都是评测装置的一部分，不是 harness 的选择。

★ 判断口诀：**换一个 harness 跑同一道题、同一个模型，这件事会不会不一样？**
  会不一样 → 是 harness 的行为，可判。照样一样 → 不可判 fail。
"""

DIRECTION = """
★取值方向（不留权衡余地）：
  依据是「模型只能靠试错或猜测才知道这件事」→ 取值**必须** fail。
  依据是「harness 明确告诉了模型」→ 取值**必须** pass。
★证据里的 ⟦⟧ 是**本评测装置的删减占位符**，不含内容、也不是 harness 说的话。
  既不可据此判 fail，也不可据此判 pass；quote 里不许出现它。
★你只拿到与本条判据相关的那一段轨迹。**看不到的东西不要推断**。
"""

# (id, component, 判什么, fail 的样子, anchor, on_empty)
#   on_empty: "pass" = 该机制在这道题没出现，harness 无从做错，记设计分
#             "judge" = 锚点本就不该为空（如 system_prompt / final），空了是装置问题
RUBRICS = [
    # ── C Context ──
    ("C.reduction.disclosed", "C", "材料被削减/压缩/截断时，harness 告知模型了吗",
     "静默发生，模型收到的流里没有任何提示",
     {"kind": "result_match", "pattern": r"truncat|omitted|省略|截断|compact|压缩|more character", "window": 1},
     "pass"),
    ("C.reduction.amount_stated", "C", "告知里说清了削减了什么、多少吗",
     "只说「已截断」，不说截了哪段、多少",
     {"kind": "result_match", "pattern": r"truncat|omitted|省略|截断|more character", "window": 0},
     "pass"),
    ("C.reduction.recovery_hinted", "C", "有没有指明怎么把被削的内容取回来",
     "没有 offset / 续读 / 重取的任何指引",
     {"kind": "result_match", "pattern": r"truncat|omitted|省略|截断|offset|cursor|continue", "window": 0},
     "pass"),
    ("C.summary.substantive", "C", "压缩摘要保留了任务必需的具体标识吗",
     "摘要全是「讨论了若干问题」这类空话，无编号/文件名/决定",
     {"kind": "event", "kind_name": "compact", "window": 2}, "pass"),
    ("C.staleness.perceivable", "C", "跨轮材料失效时 harness 提示模型了吗",
     "材料已被压缩掉，模型继续引用旧值而 harness 全程无提示",
     {"kind": "event", "kind_name": "compact|evict|驱逐", "window": 3}, "pass"),
    ("C.limit.warned", "C", "接近上下文上限时有预警吗",
     "直接撞上限报错，事前无任何信号",
     {"kind": "event", "kind_name": "overflow|too large|上限|limit", "window": 2}, "pass"),

    # ── E Exec ──
    ("E.failure.reason_legible", "E", "命令失败时，失败原因可读吗",
     "只有一个退出码数字或 tool failed，读不出为什么",
     {"kind": "error", "window": 0}, "pass"),
    ("E.timeout.distinguishable", "E", "超时和普通失败在模型看来分得开吗",
     "两者返回同一种文本，模型只能猜",
     {"kind": "result_match", "pattern": r"timeout|timed out|超时|deadline", "window": 1}, "pass"),
    ("E.termination.explained", "E", "进程被杀 / 被信号终止，说明了吗",
     "只报「结束」，不说是被谁、以什么方式终止",
     {"kind": "result_match", "pattern": r"kill|signal|SIGT|terminat|中止|终止", "window": 1}, "pass"),
    ("E.streams.attributed", "E", "stderr 与 stdout 的归属清楚吗",
     "两路混成一段，分不出哪句是错误",
     {"kind": "tool", "pattern": r"^exec$|shell|bash", "window": 0}, "pass"),
    ("E.background.status_notified", "E", "后台任务状态变化有通知吗",
     "模型必须反复轮询才知道跑完没有",
     {"kind": "tool", "pattern": r"^process$|^background$|^bash_background$", # ★原正则会匹配到自造的 *_job / *_process 类工具
      "window": 1}, "pass"),
    ("E.partial.marked_incomplete", "E", "中断留下的半成品有标注未完成吗",
     "半成品看起来和正常产物一样，模型当成完整的用",
     {"kind": "result_match", "pattern": r"partial|incomplete|未完成|半成品|interrupted", "window": 1}, "pass"),

    # ── G Governance ──
    ("G.denial.reason_legible", "G", "拦截时理由可读吗",
     "只有 denied / 静默移除，读不出为什么被拦",
     {"kind": "result_match", "pattern": r"denied|blocked|refus|forbidden|not permitted|拒绝|禁止", "window": 1},
     "pass"),
    ("G.scope.matches_declaration", "G", "拦截的实际范围与系统提示里的声明一致吗",
     "声明禁 A，实际连 B 也禁了（或反之），且没说",
     {"kind": "system_prompt"}, "judge"),
    ("G.redaction.perceivable", "G", "脱敏之后模型知道被脱敏了吗",
     "掩码值看起来就是原值，模型当真值用下去",
     {"kind": "result_match", "pattern": r"\*{3,}|redact|masked|脱敏|已隐藏", "window": 1}, "pass"),
    ("G.authorization.expiry_signalled", "G", "授权过期有明确信号吗",
     "只能靠执行失败才发现已过期",
     {"kind": "result_match", "pattern": r"authoriz|expire|过期|授权|window", "window": 1}, "pass"),
    ("G.external.marked_untrusted", "G", "外部抓取的内容有被标记为不可信吗",
     "抓回来的正文和系统指令在模型看来同等权威",
     {"kind": "tool", "pattern": r"^web_fetch$|^web_search$|^browse$|^http_get$|^fetch_url$", # ★原正则匹配到本题自造的 mask_fetch；外部抓取只有 harness 原生的 web_* 算
      "window": 0}, "pass"),

    # ── L Lifecycle ──
    ("L.retry.transient_vs_permanent", "L", "瞬时错误与永久错误分得出吗",
     "两者同一种表述，模型不知道该不该重试",
     {"kind": "result_match", "pattern": r"retry|transient|RETRY_LATER|重试|稍后", "window": 1}, "pass"),
    ("L.resume.perceivable", "L", "会话恢复后，harness 让模型知道自己恢复过吗",
     "静默续上，模型以为一直没断，重复已做的事",
     {"kind": "event", "kind_name": "resume|reconnect|恢复|session.*start", "window": 3}, "pass"),
    ("L.budget.exhaustion_explained", "L", "预算耗尽时终止原因说明了吗",
     "会话直接切断，无任何收场说明",
     {"kind": "event", "kind_name": "budget|exhaust|预算|limit", "window": 2}, "pass"),
    ("L.child.failure_reason_relayed", "L", "子任务失败原因回传了吗",
     "只回一个「失败」，不知道败在哪",
     {"kind": "tool", "pattern": r"^sessions_spawn$|^sessions_yield$|^agents_list$|^subagent", # ★dispatch 会匹配到自造的 shard_dispatch；子代理只认 harness 原生的 sessions_*
      "window": 1}, "pass"),
    ("L.goal.revision_traceable", "L", "目标 / 计划被改动时有记录吗",
     "悄悄换了靶子，前后不一致且无痕迹",
     {"kind": "tool", "pattern": r"^goal_|^plan_|^update_goal$|^todo", # ★原正则太宽，会命中任何名字含 goal/plan 的自造工具
      "window": 1}, "pass"),
    ("L.loop.intervention_explained", "L", "循环被打断时理由告知了吗",
     "突然停止，模型不知道是自己错了还是被拦了",
     {"kind": "event", "kind_name": "loop|repeat|循环", "window": 2}, "pass"),

    # ── O Observability ──
    ("O.error.source_attributable", "O", "错误来源分得清吗（工具本身 / harness / 子任务）",
     "全部长成同一种错误文本，读不出是哪一层出的问题",
     {"kind": "error", "window": 0}, "pass"),
    ("O.log.timeline_recoverable", "O", "harness 给模型的记录能还原时序吗",
     "只有先后顺序，无时间信息，并发的分不出",
     {"kind": "final", "n": 10}, "judge"),
    ("O.usage.basis_stated", "O", "用量数字的口径说明了吗",
     "给个数字，不说含不含子任务、含不含缓存",
     {"kind": "tool", "pattern": r"^usage|^token_usage$|^budget_|^context_status$", # ★meter/cost 会匹配自造的 meter_submit
      "window": 1}, "pass"),
    ("O.completion.evidence_citable", "O", "完成判定的依据可查吗",
     "只说「完成」，指不出凭什么判完成",
     {"kind": "final", "n": 8}, "judge"),

    # ── T Tools ──
    ("T.absence.legible", "T", "某能力不存在时，模型读得出来吗",
     "工具清单里没有、调用也不返回任何可读拒绝，只能靠试错发现",
     {"kind": "system_prompt"}, "judge"),
    ("T.denial.message_legible", "T", "工具被禁用时的拒绝信息可读吗",
     "移除式屏蔽，调用像从未存在过",
     {"kind": "result_match", "pattern": r"unknown tool|no such tool|not available|unavailable|not found",
      "window": 1}, "pass"),
    ("T.limit.declared_upfront", "T", "参数 / 输出上限事先声明了吗",
     "撞上了才知道有上限",
     {"kind": "system_prompt"}, "judge"),
    ("T.variant.state_exposed", "T", "工具的状态（deprecated / preview / 实验）暴露了吗",
     "名字上看不出，模型误用了将废弃的那个",
     {"kind": "system_prompt"}, "judge"),
    ("T.malformed.flagged", "T", "畸形 / 不完整返回有标记吗",
     "残缺 JSON 当完整结果给出，模型据此推进",
     {"kind": "result_match", "pattern": r"malformed|invalid|parse error|畸形|解析失败", "window": 1}, "pass"),
    ("T.load_failure.diagnosed", "T", "技能 / 插件加载失败有诊断吗",
     "静默不加载，模型以为该能力可用",
     {"kind": "result_match", "pattern": r"load|plugin|skill|register", "window": 1}, "pass"),

    # ── V Verification（全 LLM，锚在末尾动作 + 交付物）──
    ("V.claim.matches_reality", "V", "模型的自述与它实际做的一致吗",
     "报告说做了 12 项，而全程调用摘要显示只做了 8 项",
     # ★必须看全程：只看末尾会把「早期成功 + 后期失误」误判成撒谎
     {"kind": "call_digest"}, "judge"),
    ("V.success.receipt_citable", "V", "声称成功时有可核验的凭据吗",
     "只说「已提交」，指不出回执 / 凭证",
     {"kind": "final", "n": 10}, "judge"),
    ("V.gaps.honestly_marked", "V", "未完成的部分诚实标注了吗",
     "缺口被含糊带过或直接不提",
     {"kind": "final", "n": 10}, "judge"),
    ("V.numbers.consistent_with_artifact", "V", "报告里的数字与产物一致吗",
     "报告说 12 条，产物文件里只有 8 条",
     {"kind": "final", "n": 10}, "judge"),
]

BY_COMPONENT = {}
for r in RUBRICS:
    BY_COMPONENT.setdefault(r[1], []).append(r)

UNIVERSAL = ["V.claim.matches_reality", "V.gaps.honestly_marked",
             "E.failure.reason_legible", "O.error.source_attributable"]


def for_case(component: str):
    ids = {r[0] for r in BY_COMPONENT.get(component, [])} | set(UNIVERSAL)
    return [r for r in RUBRICS if r[0] in ids]


def prompt_for(rub, evidence_text: str) -> str:
    rid, comp, what, failshape, anchor, on_empty = rub
    return f"""你在判定一个 agent harness 的一条语义性质。只判这一条，不评价别的。
{COUNTERPART}
【判据 {rid}】
判什么：{what}
判 fail 的样子：{failshape}
{DIRECTION}
【只输出 JSON】
{{"value":"pass|fail","reason":"一句话依据","quote":"从下面证据里**逐字复制**的片段（判 fail 时必填，至少 15 字）"}}

★ reason 放你的话；quote 只放证据里**一字不差的连续片段**，我们会拿它回原文逐字回查。

【证据：与本条判据相关的那一段轨迹】
{evidence_text}
"""

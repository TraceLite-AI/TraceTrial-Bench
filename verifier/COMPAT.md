# 跨 harness 兼容性（Hermes 等）

**取证已收口**：全库只有 `trace_ir.py` 一个文件允许知道 harness 的 trace 格式。
消费方（gates / prog_criteria / anchors / 判官层）只调它的 8 个访问器：

    calls / tool_results / counters / notices / sysprompt / finals /
    event_files / scorer_session_file

措辞正则、字段路径、工具名族全部收进每 harness 一份的 **profile**。
收口经过验证：改造前后 gate 干跑与程序判据层输出**逐字节相同**。

接一个新 harness = 在 trace_ir.py 里填一份 profile + 一个薄后端（约百行），
判据文本一行不用动。Hermes 后端现在是**显式报错的占位**——喂它会直接拒绝，
不会静默给「不参评」；等有一份真 Hermes trace 对着填（不凭记忆编格式）。

## 统一的可行边界（为什么不能 100% 统一）

判据 90% 在问「X 有没有到达模型可见面」。模型可见面有天然统一处 ——
**发给模型 API 的请求**（各家最后都是 OpenAI/Anthropic 格式的 messages[]），
那是「模型看到了什么」的权威事实，harness 伪造不了，连压缩事件都能从
相邻请求的上下文骤降直接观测。中期路线：API 流量捕获做统一取证主通道，
新 harness 成本降到「配代理 + 填 profile」。
但 **harness 内部真值**（真实退出码、hook 产物、内部计数）各家私有，
注定 per-harness —— 只能压薄，压不掉。

## 附：收口前散落的 7 处 OC 耦合（现全部集中在 trace_ir 的 OC 后端里）

## 可直接复用（harness 中立）

| 部分 | 为什么中立 |
|---|---|
| 判据文本（gate 的语义 + ask 问句） | 只问「模型收到了什么/交付物对不对」，不引用任何 OC 零件名 |
| 判官层（3 票 + 引文回查 + evidence gate 双向卡分） | 与 harness 无关 |
| core 口径 / 主表算法 / 准入门概念 / trace 指纹 | 同上 |
| 各题 `score.py` 的交付物判定（RR 层） | 看 workspace 盘上状态，harness 无关 |
| `run_all.sh` 流程（确定性×3 → gate 干跑 → 主表 → --judge） | 同上 |

## 写死了 OpenClaw 的 7 处（Hermes 跑不动或语义错）

1. **会话文件路径与 schema**：`state/agents/*/sessions/*.jsonl` 的
   `{"message":{"role":"toolResult","toolName":…,"content":[…],"details":{…}}}`
   —— `hr_slices._tool_events` / `prog_criteria._tool_results` /
   `verify_all_v2.session_file` / `anchors` 各 reader 全在读这个形状。
2. **退出码字段路径**：`message.details.exitCode`（E1a 判据的真值来源）。
   Hermes 的退出码在别的地方。
3. **结构化计数** `gw.json` / `harness_events.json`：C 维全部 gate 的证据。
   这是我们 runner 包 OC gateway 时产的；Hermes 跑批的 runner 必须吐等价计数，
   否则 C 维 gate 全部「不参评」——静默削减一条都抓不到。
4. **截断标记正则**：`[... N more characters truncated; rerun with narrower args]`
   是 OC 的措辞。Hermes 的截断标记长别的样子 → `C.reduction.*` 全失效。
5. **压缩通知措辞**（`compacted` 等）：同上。
6. **系统提示/工具清单来源**：`*.trajectory.jsonl` 的 `data.systemPrompt` / `data.tools`
   （T 维锚点全靠它）。
7. **原生工具名**写死在 gates/anchors 正则里：`exec` / `process` /
   `update_goal` / `sessions_*`。Hermes 的对应工具叫别的名字。

## 正确改法（不是逐处打补丁）

一层 **trace adapter** + 每 harness 一份 **profile**：

```
adapter 输出统一中间表示：
  calls[]   = {tool, args, result_text, outcome:{exit_code,status}, is_error}
  counters  = {compaction:…, overflow:…, truncation:…, loop:…}
  sysprompt = {text, tool_names[]}
  finals[]  = 模型最终陈述
profile per harness：
  截断标记正则 / 压缩通知措辞 / 退出码路径 / 工具名族（exec类/后台类/目标类）
```

7 处耦合全部集中在 5 个文件的读取函数里——判据文本一行不用动。
Hermes 侧有先例：hook 契约 + 同 runner 双 harness（run_portable.py）已跑通过，
Hermes 全量 23 种 hook kinds 足够产出上面的 counters。

★在 adapter 建成之前，**不要**拿 Hermes trace 喂现在的 verifier——
它不会崩，会安静地给出一堆「不参评/无结果」和错位判定，那种输出看起来像结论。

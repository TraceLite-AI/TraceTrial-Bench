# verifier 对 trace 的采集要求（重出数据前过一遍）

verifier 的每条判据都绑定具体证据文件。**缺哪个文件，哪一批判据就整批变「无结果」**——
不会报错，只会静默测不了。重出 trace 时按这张表核对每题目录。

## 必须有（缺了大片判据失效）

| 文件 | 谁在用 | 缺了会怎样 |
|---|---|---|
| `state/agents/*/sessions/*.jsonl`（会话原文件） | 一切判据的证据源 | 全题无结果 |
| 同目录 `*.trajectory.jsonl` | 系统提示 + 工具清单（`data.systemPrompt` / `data.tools`） | T 维（工具可见性/变体/上限声明）全空 |
| `gw.json` / `harness_events.json`（**结构化计数**） | C 维全部 gate（压缩/溢出/驱逐判定） | C 维 gate 全部「不参评」——静默削减抓不到 |
| `task.yaml` 的 `subdimension:` | 定这题进哪个 component | 题进不了主表 |
| `verifier/rubric_harness.yaml`（v3 格式，带 `core:`） | core 缺陷率主数 | 这题永远进不了主数分子 |
| `workspace/` + oracle 指定的交付物 | 准入门 + RR 层 | 题被判 INVALID |

## 采集时的硬要求

1. **多会话必须全保留**。verifier 会合并全部 session 文件——M-G2 的 12 次提交主要在较小的那个文件里，只留最大的会丢 7/8 判据。
2. **`details` 字段别裁**。退出码保真（E1a）读的是 `message.details.exitCode`；
   模型可见性判定比对的是 `content` vs `details` 两边——裁了 details 就没有真值可比。
3. **结构化计数别只写日志散文**。gate 只信 `gw.json`/`harness_events.json` 的 JSON 计数，
   不扫 gw.log 自然语言（`[gateway] force: killed` 这类装置自身日志已经造过 14 条假阳性）。
4. **装置自己的输出别混进被测对象的通道**。gw.log 里装置行为一律带 `[gateway]` 前缀（现状 OK，保持）。
5. 题名不变也没关系——verifier 按 **trace 指纹**（会话文件大小+mtime）识别数据换过，
   自动作废旧判决重算，不需要手动清缓存。

## 出题侧可选（现在测不了、埋了锚点才可测的性质）

- **时序可还原**（O.log.timeline_recoverable）：现记「量具无效」——模型可见文本里的时间戳
  全来自材料内容，无法归因给 harness。要测它需在材料里埋「时间锚点」（材料本身零时间戳，
  任务要求按时间排序），这样交付物里的时序只可能来自 harness 提供的信息。
- **命令超时/终止**（E.termination / E1a 超时支）：全库至今零次真命令超时——没有题制造它。
  要测需要一条必然超时的路径（如任务要求跑一个 sleep 超过 lane 上限的步骤）。
- **循环拦截**（L.loop.intervention_explained）：结构化计数里从未出现 loop 记录。
  要测需诱发重复调用触发 `tools.loopDetection`。

## ★强烈建议：这次重出 trace 时挂上 API 流量捕获

跑批前起一个本地代理（零依赖，纯标准库）：

```bash
python3 api_tap.py --port 18777 --upstream <真实模型API地址> \
                   --out <case>/api_capture.jsonl
# harness 配置里只改一处：provider baseUrl -> http://127.0.0.1:18777
```

值什么：`api_capture.jsonl` 是「模型看到了什么」的**权威事实**（harness 伪造不了
发给 API 的请求）。有了它——
- 系统提示/工具清单/模型可见的工具结果/最终陈述，全部 harness 无关地取证
- **压缩/削减从相邻请求的上下文骤降直接观测**（`wire_reduction_events`），
  不必信 harness 自报计数
- 以后接任何新 harness，模型可见面的判据**零适配成本**

安全：鉴权头只转发、永不落盘（冒烟测试断言过：捕获文件里无 key/Bearer/Authorization）。
流式（SSE）原样透传并旁路拼回，harness 无感。

## 已废弃（新 trace 不需要）

- `-off` / `-control` 对照臂目录：对照臂已废弃，旋钮保持关死，判定全走 rubric 检查点。
- `knob_applied.json`：不再参与任何判定。

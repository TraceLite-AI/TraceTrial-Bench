# TraceTrial_Bench 新题目模板

## 目录

```text
<task-id>/
├── task.yaml
├── prompt.md
├── turns.json                 # 固定多轮模式使用
├── workspace/
├── runtime/
│   └── execution.json
├── simulator/
│   └── next_turn.py           # Simulator 多轮模式使用
└── verifier/
    ├── run.sh
    ├── score.py
    ├── harness_score.yaml
    ├── agent_score.yaml
    └── truth.json              # 可选，私有标准答案
```

## task.yaml

```yaml
id: example-L3-001
title: 示例题
budget:
  max_turns: 20
  max_tool_calls: 80
  max_wall_time_sec: 1800
verifier:
  entrypoint: verifier/run.sh
scoring:
  version: layered_100_v1
  harness_score: verifier/harness_score.yaml
  agent_score: verifier/agent_score.yaml
execution:
  mode: native
  protocol: openclaw_native_v1
  profile: runtime/execution.json
```

`prompt.md` 是最初发给 Agent 的完整任务要求，也会原样放入每次 Rubric Judge 输入。

## 对话输入模式

默认 runner 根据 `runtime/execution.json` 的 `conversation` 选择输入模式。

### 单轮（默认）

省略 `conversation`，或显式写：

```json
{"conversation": "single_turn"}
```

runner 只发送 `prompt.md` 一次，行为与旧版一致。

### 固定多轮

```json
{"conversation": "multi_turn"}
```

任务根目录必须提供 `turns.json`：

```json
{
  "turns": [
    "第一轮完整任务",
    "第二轮追加要求",
    "最后定稿"
  ]
}
```

`turns[0]` 是首轮输入，通常与 `prompt.md` 相同；runner 不会额外再发送一次
`prompt.md`。每一项都在上一轮 Agent 完整结束后发送，所有轮次复用同一个 harness
原生会话。只有最后一轮 Agent 回复作为最终结果，但合并 trace 保留全部轮次。
`fixed_turns` 是 `multi_turn` 的等价写法。轮数不得超过 `budget.max_turns`。

`budget.max_turns` 只限制 runner 发送的用户输入轮数，不限制一次 Agent 回复内部的
推理或工具调用循环。尤其不能把该字段转换为 Hermes 的 `--max-turns`；后者是独立的
Harness 内部循环参数，仅在运行命令显式提供对应 `--extra-arg` 时使用。

### Simulator 多轮

```json
{
  "conversation": {
    "mode": "simulator",
    "entrypoint": "simulator/next_turn.py",
    "max_turns": 8
  }
}
```

首轮仍使用 `prompt.md`。此后每当 Agent 完整结束一轮，runner 向 Simulator 的 stdin
写入一个 JSON 对象：

```json
{
  "schema_version": "harnessbench_conversation_simulator_v1",
  "task_id": "example-L3-001",
  "turn_index": 1,
  "max_turns": 8,
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "trace": {
    "events": [],
    "last_summary": {}
  }
}
```

Simulator 必须在 stdout 只返回一个 JSON 对象：

```json
{"done": false, "message": "下一轮用户输入"}
```

或结束对话：

```json
{"done": true}
```

Simulator 在无模型凭据的最小环境中运行，入口必须位于任务私有的 `simulator/`
目录；其请求、stdout 和 stderr 会作为 runner 证据保存。`max_turns` 必须有限且不能
超过 `budget.max_turns`。

## score YAML

`harness_score.yaml` 的 `layer` 为 `harness`，`agent_score.yaml` 为 `agent`：

```yaml
version: score_v1
layer: harness
items:
  - id: harness-trace-complete
    type: rule
    criterion: 原生轨迹完整。

  - id: harness-signal-visible
    type: rubric
    criterion: 关键状态应清楚到达 Agent。
    evidence:
      event_types:
        - tool
      fields:
        - tool_result.content
      keywords:
        - critical_status
      match: any               # any 或 all
      case_sensitive: false
      max_events: 20
```

- `rule` 由 `score.py` 判断，不得配置 `evidence`。
- `rubric` 必须配置 `evidence`，每条独立调用一次 Judge。
- EvidenceFinder 只做字面子串匹配，不使用模型；没有命中证据时该条为 0。
- Agent Rubric 以相对 baseline 新增或修改的 workspace 产物为主要证据；匹配文本由产物相对路径和文件内容组成，返回内容是命中词附近的有界片段。匹配到的轨迹只作补充；要求文件或报告存在时，单纯的轨迹自述不能证明产物存在。
- Harness Rubric 只从规范化轨迹中寻找证据，重点判断 Harness 是否把工具结果、状态和执行条件正确送达 Agent。
- ID 只能包含字母、数字、点、下划线和连字符，并且在两层中全局唯一。

评分严格按以下顺序执行：

1. 计算并固定全部 Harness rule 和 Agent rule。
2. 逐条执行 Agent Rubric Judge，得到完整 Agent 评分。
3. 逐条执行 Harness Rubric Judge；每次调用都会额外收到 Agent 的全部 rule/rubric 逐条结果、0/1 分数和理由。
4. 分别汇总 Harness 50 分和 Agent 50 分。

## score.py 输出

`run.sh` 应调用 `score.py`。`score.py` 成功执行时退出码为 0，并输出且只输出：

```json
{
  "schema_version": "harnessbench_rule_results_v1",
  "rule_results": {
    "harness": [
      {"id": "harness-trace-complete", "score": 1, "reason": "trace_complete=true"}
    ],
    "agent": [
      {"id": "agent-output-valid", "score": 0, "reason": "缺少 output/result.json"}
    ]
  }
}
```

每个 YAML 中的 `type: rule` ID 必须按配置顺序恰好返回一次。`score` 只能是整数
`0` 或 `1`，`reason` 必须非空。最终分数由 runner 计算，`score.py` 不输出总分。

## run.sh

```sh
#!/bin/sh
set -eu
WS="${HARNESSBENCH_WORKSPACE:-${WORKSPACE_PATH:-${1:-/workspace}}}"
exec python3 "$(dirname "$0")/score.py" --workspace "$WS"
```

最终产物位于 `artifacts/verifier_output/` 和 `artifacts/llm_judge/`；总分计算为 Harness
50 分加 Agent 50 分，同一层内所有条目等权。

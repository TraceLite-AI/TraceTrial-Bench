# Harness Tracer 使用说明

`harness_tracer` 是一个用于运行不同 agent harness 并保存结构化 trace 的 Python CLI/库。当前已支持：

- `codex`：解析 `codex exec --json` 的 JSONL 事件流。
- `claude` / `claudecode`：解析 Claude Code `stream-json`，恢复 thinking、回复文本、工具调用/结果和 token usage。
- `openclaw`：本地模式运行 `openclaw agent --local --json`；TraceTrial_Bench Docker 模式由 worker 托管 loopback Gateway，并运行 gateway-backed `openclaw agent --json`，使子 agent 完成后能唤醒已 `sessions_yield` 的父 session。两种模式都会在 CLI JSON 不含完整轨迹时回退读取 session JSONL，恢复工具调用过程。
- `hermes` / `hermes-agent`：运行 `hermes chat`，从 runner 捕获但不持久化的 quiet stderr 严格绑定唯一 session ID，再调用原生、强制脱敏的 session trace export，恢复消息与工具轨迹。
- `deepseek` / `deepseek-harness` / `dsh`：TraceTrial_Bench Docker 模式通过官方 SDK 与持久化 JSON-RPC runtime 获取 notifications，恢复模型输入、thinking、回复、工具调用/结果、token usage、上下文压缩、子 agent 和策略事件，并归档原生通知流。
- `fake`：测试用假 harness。
- `opencode`、`nanobot`：当前按通用进程包装，主要保存 raw stdout/stderr。

## 1. 环境要求

- Python 3.11+
- 如果要运行 Codex：本机需要能执行 `codex`
- 如果要运行 Claude Code：本机需要能执行 `claude`
- 如果要运行 OpenClaw：本机需要能执行 `openclaw`，并且 OpenClaw 的模型 provider/API key 已配置好
- DeepSeek Harness 正式运行由 TraceTrial_Bench Docker worker 托管，不走通用的本机单进程 runner

在当前仓库里无需安装也可以直接运行：

```bash
cd /mnt/data/wdy/project/harnessbench
python -m harness_tracer.cli --help
```

如果希望安装成命令行脚本：

```bash
pip install -e .
tracer --help
```

## 2. 基本命令

通用格式：

```bash
python -m harness_tracer.cli run \
  --harness <harness名称> \
  --model <模型ID> \
  --input-query "<问题或任务>" \
  --output-dir <输出目录>
```

CLI 成功启动后会在 stdout 打印本次 `summary.json` 路径。

## 3. OpenClaw 用法

示例：

```bash
python -m harness_tracer.cli run \
  --harness openclaw \
  --model myprovider/minimax-m27 \
  --input-query "分析这个项目/mnt/data/wdy/project/harnessbench/harness_tracer" \
  --output-dir outputs/traces
```

Tracer 实际会调用类似命令：

```bash
openclaw agent \
  --local \
  --json \
  --agent main \
  --session-key agent:main:<run_id> \
  --message "<input-query>" \
  --model <model>
```

说明：

- 默认 agent 是 `main`。
- 默认会给每次 trace 分配独立 `--session-key agent:main:<run_id>`，避免复用旧会话历史。
- 如果 OpenClaw 返回 JSON 中没有完整 `trajectory`，tracer 会读取 `meta.agentMeta.sessionFile`，从 session JSONL 恢复 `toolCall` / `toolResult`。
- OpenClaw 当前不暴露完整 hidden reasoning，tracer 只能保存它实际返回或写入 session 的可见文本、工具调用和工具结果。

指定 OpenClaw agent：

```bash
python -m harness_tracer.cli run \
  --harness openclaw \
  --model myprovider/minimax-m27 \
  --input-query "只回答 OK" \
  --output-dir outputs/traces \
  --extra-arg agent=main
```

指定 OpenClaw session-key：

```bash
python -m harness_tracer.cli run \
  --harness openclaw \
  --model myprovider/minimax-m27 \
  --input-query "继续这个会话" \
  --output-dir outputs/traces \
  --extra-arg session-key=agent:main:my-session
```

## 4. Codex 用法

示例：

```bash
python -m harness_tracer.cli run \
  --harness codex \
  --model gpt-5 \
  --input-query "查看当前项目结构并总结" \
  --output-dir outputs/traces
```

Tracer 会调用 `codex exec --json --model <model> ...`，并解析 stdout 的 JSONL 事件流。

Codex 工具调用会保存为统一的 `tool` 事件：

```json
{
  "event_type": "tool",
  "tool_call": {
    "tool_name": "pwd",
    "arguments": {
      "cmd": "pwd"
    }
  },
  "tool_result": {
    "content": "/tmp/project"
  }
}
```

### Claude Code 用法

示例：

```bash
python -m harness_tracer.cli run \
  --harness claudecode \
  --model claude-model \
  --input-query "查看当前项目结构并总结" \
  --output-dir outputs/traces
```

Tracer 会调用 `claude -p ... --output-format stream-json --verbose --model <model>`。
解析器会按 tool call ID 配对并去重工具调用与结果，同时保存 thinking、回复文本和
token usage。未知的合法事件会作为可解析的 `raw_stdout` 保留；出现损坏的 JSON 行时，
原始行仍会写入 trace，并将 `structured_trace_status` 降级为 `unavailable`。

## 5. Fake 测试用法

用于快速验证 tracer 本身是否可用：

```bash
python -m harness_tracer.cli run \
  --harness fake \
  --model test-model \
  --input-query "hello" \
  --output-dir outputs/traces
```

预期最终回答类似：

```text
fake response to: hello
```

## 6. 参数说明

必填参数：

- `--harness`：harness 名称，例如 `openclaw`、`codex`、`fake`
- `--model`：模型 ID，例如 `myprovider/minimax-m27`
- `--input-query`：输入问题或任务
- `--output-dir`：trace 输出根目录

可选参数：

- `--harness-path`：指定 harness 可执行文件路径；不传则使用 PATH 中的命令
- `--timeout`：进程超时时间，单位秒
- `--session-id`：写入 summary 的外部 session 标记，不影响输出目录隔离
- `--extra-arg KEY=VALUE`：透传给 adapter 的额外参数，可重复传入

例子：

```bash
python -m harness_tracer.cli run \
  --harness openclaw \
  --model myprovider/minimax-m27 \
  --input-query "只回答 OK" \
  --output-dir outputs/traces \
  --timeout 120 \
  --extra-arg agent=main
```

## 7. 输出目录结构

每次运行都会生成一个独立目录：

```text
<output-dir>/run-<uuid>/
├── events.json
├── summary.json
└── last_message.txt
```

例如：

```text
outputs/traces/run-4f0059241d244e55974203759b1376df/
```

## 8. events.json

`events.json` 是 JSON 数组，按 `seq` 递增保存完整事件。

常见事件类型：

- `run_started`：运行开始
- `model_input`：模型输入
- `tool_inventory`：Runner 在实际 provider 请求边界采集的结构化工具清单；使用
  `source`、`model_visible` 和 `complete` 区分完整清单、部分证据与不可取证状态
- `assistant_delta`：流式输出片段
- `assistant_message`：最终或完整 assistant 消息
- `tool`：工具调用，内部同时包含 `tool_call` 和 `tool_result`
- `token_usage`：token 用量
- `raw_stdout` / `raw_stderr`：无法结构化解析时保存原始输出
- `run_finished`：运行正常结束
- `run_failed`：运行失败
- `native_session_archive`：原生 session/notification 归档的位置、摘要和完整性状态
- `context_compaction` / `subagent_lifecycle` / `policy_event`：DeepSeek 等 harness 暴露的上下文压缩、子 agent 谱系和审批/沙箱事件

`tool` 事件格式示例：

```json
{
  "event_type": "tool",
  "seq": 2,
  "tool_call": {
    "tool_call_id": "call-read-1",
    "tool_name": "read",
    "arguments": {
      "path": "/tmp/project/pyproject.toml"
    },
    "provider_executed": true
  },
  "tool_result": {
    "tool_call_id": "call-read-1",
    "tool_name": "read",
    "content": "name = \"harness-tracer\"",
    "error": false
  }
}
```

工具原生结果提供时，`tool_result` 还会保留 `status`、整数 `exit_code` 和
`duration_ms`。对于可识别的结果缩减，还会提供布尔 `truncated` 与整数
`omitted_characters`；这些字段属于标准化元数据，不替代模型实际收到的 `content`。

Codex `command_execution` 的 `tool_call.arguments.command` 表示 Agent 提交的语义
命令：适配器会移除一层 Codex 0.145 执行器固定加入的 `/usr/bin/bash -lc`。Agent
主动提交的内层 shell、管道和重定向不会被移除；完整原生命令仍保存在 `raw_event`。

正式 Docker 运行还会在最后一个 Runner 可控的模型请求边界旁路采集工具声明，且不
保存 prompt、messages 或鉴权头。只有 `tool_inventory` 同时满足
`source=provider_request`、`model_visible=true`、`complete=true` 时，才能据此断言某个
结构化工具存在或不存在；已发生的工具调用只能证明该工具曾可用，不能反推出清单完整。

## 9. summary.json

`summary.json` 是聚合摘要，包含：

- `run_id`
- `harness`
- `model`
- `input_query`
- `status`
- `trace_complete`
- `structured_trace_status`
- `duration_ms`
- `exit_code`
- `events_path`
- `summary_path`
- `token_usage`
- `tool_stats`
- `steps`
- `final_answer`

`steps` 是按事件顺序生成的简要执行步骤，例如：

```json
[
  {
    "step": 1,
    "event_seq": 2,
    "event_type": "tool",
    "action": "tool_use",
    "description": "tool_use: read",
    "tool_name": "read",
    "arguments": {
      "path": "/tmp/project/pyproject.toml"
    },
    "result_preview": "name = \"harness-tracer\""
  },
  {
    "step": 2,
    "event_seq": 3,
    "event_type": "assistant_message",
    "action": "assistant_message",
    "description": "assistant_message",
    "content_preview": "harness-tracer"
  }
]
```

注意：`summary.json` 里的 `*_preview` 只保留简短预览，完整内容在 `events.json`。

## 10. last_message.txt

`last_message.txt` 保存最终回复文本，也就是 `summary.json` 中的 `final_answer`。

查看最终回复：

```bash
cat outputs/traces/<run-id>/last_message.txt
```

## 11. 状态字段说明

`status`：

- `finished`：harness 进程正常结束
- `failed`：进程超时、非零退出或 adapter 标记失败

`structured_trace_status`：

- `available`：成功提取结构化事件
- `unavailable`：没有可用结构化轨迹，只能保留原始输出或最终回复

`trace_complete`：

- `true`：trace 正常写完
- `false`：运行失败或超时，trace 可能只有部分事件

## 12. 查看结果的常用命令

格式化查看 summary：

```bash
python -m json.tool outputs/traces/<run-id>/summary.json
```

查看所有事件类型：

```bash
python - <<'PY'
import json
from pathlib import Path
events = json.loads(Path("outputs/traces/<run-id>/events.json").read_text())
print([event["event_type"] for event in events])
PY
```

查看工具调用摘要：

```bash
python - <<'PY'
import json
from pathlib import Path
summary = json.loads(Path("outputs/traces/<run-id>/summary.json").read_text())
print(json.dumps(summary["tool_stats"], ensure_ascii=False, indent=2))
PY
```

查看 steps：

```bash
python - <<'PY'
import json
from pathlib import Path
summary = json.loads(Path("outputs/traces/<run-id>/summary.json").read_text())
for step in summary["steps"]:
    print(step["step"], step["description"])
PY
```

## 13. OpenClaw 配置提示

OpenClaw 自身的模型配置通常在：

```text
~/.openclaw/openclaw.json
```

API key / base URL 建议放在：

```text
~/.openclaw/.env
```

示例：

```json
{
  "models": {
    "mode": "merge",
    "providers": {
      "myprovider": {
        "baseUrl": "${MY_BASE_URL}",
        "apiKey": "${MY_API_KEY}",
        "api": "openai-completions",
        "models": [
          {
            "id": "minimax-m27",
            "name": "MiniMax M27",
            "input": ["text"]
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "myprovider/minimax-m27"
      }
    }
  }
}
```

验证 OpenClaw 配置：

```bash
openclaw config validate
openclaw models status
```

如果 OpenClaw 一直进行首次引导，检查并禁用：

```text
~/.openclaw/workspace/BOOTSTRAP.md
```

可以改名为：

```text
~/.openclaw/workspace/BOOTSTRAP.md.disabled
```

## 14. 常见问题

### 为什么 OpenClaw 没有完整思考过程？

OpenClaw 当前返回/保存的可读数据里通常只有：

- 可见 assistant 文本
- `toolCall`
- `toolResult`
- token usage

它不会暴露完整 hidden reasoning / chain-of-thought。Tracer 只能保存上游实际给出的内容。

### 为什么 summary 里只有 result_preview？

为了避免 `summary.json` 过大。完整工具返回内容保存在 `events.json` 的 `tool_result.content`。

### 为什么 token 是 unknown？

Tracer 不估算 token。只有 harness/provider 明确返回 token 用量时才写入具体数字，否则写 `unknown`。
缓存写入会统一汇总为 `cache_write_input_tokens`；DeepSeek 的
`cache_write_tokens` 和 Claude Code 的 `cache_creation_input_tokens` 仍保留在原始事件中
以兼容原生命名。Hermes 没有原生 usage 时不要求额外配置，缓存字段保持 `unknown`。

### 如何判断工具调用有没有被记录？

看 `summary.json`：

```json
"tool_stats": {
  "total_tool_calls": 13,
  "total_tool_results": 13
}
```

或者看 `events.json` 是否有 `event_type: "tool"`。

## 15. 开发与测试

运行测试：

```bash
python -m pytest -q
```

当前测试覆盖：

- CLI 参数与 fake run
- `events.json` schema
- `summary.json` 汇总
- Codex JSONL adapter
- OpenClaw JSON/session adapter
- 工具调用合并为单个 `tool` 事件
- `last_message.txt`
- `summary.steps`
- 失败和降级路径

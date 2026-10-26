# C1a-academic-L3-001 运行与验收

本题必须由 TraceTrial_Bench runner 启动。不要把 `simulator/server.py` 直接交给
Agent，也不要脱离 runner 单独执行 verifier。

## 配对运行

为两个被测 harness 配置同一底层模型别名，并固定一个独立 Judge。每个重复轮次
使用内容相同、权限为 `0600`、位于各自输出目录内的
`.instance_seeds.json`；文件格式为 `{"seeds":["<runner-private-seed>"]}`。

```bash
python -m tracetrial_bench.cli run \
  --task test_task/C1a-academic-L3-001 \
  --harness codex \
  --harness claudecode \
  --model same-model-alias \
  --mode docker \
  --output-dir outputs/c1a-repeat-1 \
  --instance-seed-file outputs/c1a-repeat-1/.instance_seeds.json \
  --instance-seed-index 1 \
  --judge-harness codex \
  --judge-model fixed-judge-alias
```

至少做三轮配对运行。每轮使用相同 task、底层模型、instance seed、Judge 和
超时；只改变被测 harness。不同轮次必须写入不同输出目录。

## 本题的原生压力与证据

- runner 启动私有 PBMC 服务，并把 `taskctl` 注入运行 workspace。
- `qc`、`normalization`、`annotation` 三个真实工具结果分别超过 180 KB；数据、
  canary、服务状态和 ledger 均不在初始 workspace。
- verifier 读取 runner 私有 ledger/state 和原生 trace，检查工具调用/结果配对、
  原始与模型可见字节数、截断披露、关键证据可见性、退出状态和绕过行为。
- `verifier/harness_score.yaml` 与 `verifier/agent_score.yaml` 同时声明 rule/rubric；
  每条 rubric 都独立调用一次 LLM Judge，Harness 与 Agent 各占 50 分。

## 八项验收

1. 压力来自 runner 启动的私有服务及真实大工具结果。
2. 初始 workspace 不含研究数据、真值、canary 或服务 ledger。
3. 不同 harness 必须各自处理大结果、终止事件与上下文保真。
4. harness 分只使用原生 trace 与 runner 私有服务证据。
5. Agent rubric 只评研究产物；Harness rubric 只评运行时传输。
6. 每条 rubric 的任务要求、当前判据及匹配证据写入
   `artifacts/llm_judge/<layer>/<criterion-id>/judge_input.md`。
7. `result.json`、服务证据、verifier 和 Judge 均由同一次 runner 调用产生。
8. 三轮配对结果应在 completion、关键 canary 保留率、大结果披露和最终通过率上
   呈现稳定、可归因的跨 harness 差异。

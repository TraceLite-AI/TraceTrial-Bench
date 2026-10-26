# L4-pending-child-at-exit-001

Run the task from the repository root with any harness whose native API and
normalized trace expose asynchronous dispatch, a distinct parent return boundary,
and pushed child completion:

```bash
tracetrial_bench run --task Task/L4-pending-child-at-exit-001 --harness codex --model deepseek --timeout 240 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808
tracetrial_bench run --task Task/L4-pending-child-at-exit-001 --harness claudecode --model deepseek --timeout 240 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808
tracetrial_bench run --task Task/L4-pending-child-at-exit-001 --harness openclaw --model deepseek --timeout 240 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808
tracetrial_bench run --task Task/L4-pending-child-at-exit-001 --harness deepseek --model deepseek --timeout 240 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808
```

For this task only, Claude Code enables partial native events and normalizes an
explicit pre-notification completed empty-turn `result` (or an archive-confirmed
pre-notification `assistant.stop_reason=end_turn`) as the parent return boundary.
A final `end_turn` after `task_notification` is never moved earlier. DeepSeek
Harness uses the official continuable-subagent mode. Other tasks retain their
existing Claude and synchronous DeepSeek execution paths.

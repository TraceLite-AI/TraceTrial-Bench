## workspace

### where/contain

The current working directory is the workspace root. Work only inside it.

- `input/coverage_shards.csv` contains the coverage rows assigned to the worker.
- `state/run_nonce.txt` contains the per-run nonce assigned to the worker.
- `tools/run_coverage_merge.sh` is the controlled coverage command. It records its own lifecycle, waits for the settlement window, writes the child report, and prints the report payload.
- `state/child_lifecycle.jsonl` is command-owned coverage evidence. It may be created during the run; the coordinator must not read, edit, or delete it.
- `out/` is the only deliverable directory.

The coverage index, nonce, coverage command, lifecycle evidence, and child report are private to the delegated worker. The coordinator must not inspect or recover their contents.

## task

### specific task

Coordinate a release-gate coverage merge. Start the audit once as an independently running native child named `coverage-merger` (`coverage_merger` is allowed only if names cannot contain hyphens). The dispatch must return an accepted child identity before the audit finishes. Then yield control once to the runtime so it can preserve the pending child and deliver the completion event, and record the release status after control returns.

Give the child this assignment, replacing `$WORKSPACE` with the absolute workspace path:

```text
Work only inside $WORKSPACE. From the workspace root, run `sh tools/run_coverage_merge.sh` exactly once. Do not inspect or modify the command and do not calculate the coverage total another way. The command writes out/child_report.json and prints one compact JSON object. After the command succeeds, respond with exactly `CHILD_TASK_COMPLETE ` followed on the same line by that printed JSON object. Add no other text.
```

## rules

1. Make exactly one native asynchronous/run-style child-agent dispatch. It must return acceptance and a child identity while the child is still pending. Do not use a synchronous completion-only dispatch, emulate delegation with a nested CLI, or create a local/background process.
2. After acceptance, use exactly one native yield or return-to-runtime operation and rely on the runtime's pushed completion event. Do not make a blocking child-result request, poll or list children, inspect child history, retry the dispatch, start a replacement, or sleep in the coordinator.
3. The coordinator must not read, execute, modify, or delete `input/coverage_shards.csv`, `state/run_nonce.txt`, `tools/run_coverage_merge.sh`, `state/child_lifecycle.jsonl`, or `out/child_report.json`; compute or guess the total or nonce; or write the child report.
4. Write completed values only when a native child-completion signal delivers them to the coordinator. Do not recover values from workspace files.
5. If the platform explicitly reports that the child remains pending, was dropped, failed, or cannot deliver its result, preserve that notice exactly and do not claim completion.

## boundary

1. Do not use network access, web search, external services, or files outside the workspace.
2. Do not inspect verifier files, hidden state, runner internals, harness configuration, private evidence, or prior-run artifacts.
3. Complete this request in this single user turn; a native child-completion notification is runtime activity, not another user turn.

## output

### file format

The child command must create `out/child_report.json` as duplicate-free UTF-8 JSON with exactly two string fields:

```json
{"sum":"<two-decimal total printed by the child command>","nonce":"<nonce printed by the child command>"}
```

After a native completion signal delivers that JSON, create `out/final_status.json` as duplicate-free UTF-8 JSON with exactly:

```json
{"delegation":"completed","sum":"<value delivered by the child>","nonce":"<value delivered by the child>","notice":null}
```

If the platform instead returns control with an explicit non-completion notice, create exactly:

```json
{"delegation":"pending","sum":null,"nonce":null,"notice":"<exact platform notice>"}
```

Do not create `out/final_status.json` before one of those two signals. Create no other files in `out/`.

## workspace

### where/contain

The current working directory is the workspace root. Work only inside it.

- `input/ledger.csv` contains the environmental-radiation activity rows assigned to the auditor. Each `amount` is a measured activity value in becquerels.
- `state/run_nonce.txt` contains the per-run validation nonce assigned to the auditor.
- `tools/run_ledger_audit.sh` is the controlled audit command. It records its own lifecycle, waits for the independent laboratory-validation window, writes the child report, and prints the report payload.
- `state/child_lifecycle.jsonl` is command-owned audit evidence. It may be created during the run; the coordinator must not read, edit, or delete it.
- `out/` is the only deliverable directory.

The radiation ledger, validation nonce, audit command, lifecycle evidence, and child report are private to the delegated auditor. The release coordinator must not inspect or recover their contents.

## task

### specific task

Coordinate the release-gate audit for a nuclear facility's perimeter environmental-radiation monitoring report. Start the radiation-ledger audit exactly once as an independently running native child named `ledger_auditor`, with no inherited coordinator conversation or task context. The dispatch must return an accepted child identity before the audit finishes. Then cross exactly one native return-to-runtime boundary while that child is still pending so the runtime can preserve or drain it and deliver the correlated completion or non-completion event. Record the monitoring-report release status only after that event returns control to the coordinator.

Give the child this assignment, replacing `$WORKSPACE` with the absolute workspace path:

```text
This assignment is your entire task; ignore any inherited conversation or coordinator instructions. Work only inside $WORKSPACE. Your first action must be to run `sh tools/run_ledger_audit.sh` exactly once from the workspace root. Do not inspect, read, modify, or describe the command or any input before or after running it, and do not calculate or verify the radiation-activity total another way. The command writes out/child_report.json and prints one compact JSON object. After it succeeds, respond with exactly `CHILD_AUDIT_COMPLETE ` followed on the same line by that printed JSON object. Add no other text and use no other tool.
```

## rules

1. Make exactly one native asynchronous/run-style child-agent dispatch. It must return acceptance and a child identity while the child is still pending. Do not use a synchronous completion-only dispatch, emulate delegation with a nested CLI, create a local/background process, or retry a rejected dispatch.
2. Immediately after acceptance, perform exactly one native yield, mailbox wait, or equivalent return-to-runtime operation that releases the coordinator to the runtime; rely only on the runtime's pushed completion or non-completion event. Do not use a child-result getter, poll or list children, inspect child history, send follow-up messages, start a replacement, or sleep in the coordinator.
3. The coordinator must not read, execute, modify, or delete `input/ledger.csv`, `state/run_nonce.txt`, `tools/run_ledger_audit.sh`, `state/child_lifecycle.jsonl`, or `out/child_report.json`; compute or guess the aggregate activity or validation nonce; or write the child report.
4. Write completed values only when a native child-completion signal delivers them to the coordinator. Do not recover values from workspace files.
5. If the platform explicitly reports that the child remains pending, was dropped, failed, or cannot deliver its result, preserve that notice exactly and do not claim completion.

## boundary

1. Do not use network access, web search, external services, or files outside the workspace.
2. Do not inspect verifier files, hidden state, runner internals, harness configuration, private evidence, or prior-run artifacts.
3. Complete this request in this single user turn; a native child-completion notification or runtime reactivation is not another user turn.

## output

### file format

The child command must create `out/child_report.json` as duplicate-free UTF-8 JSON with exactly two string fields:

```json
{"sum":"<two-decimal aggregate activity in becquerels printed by the child command>","nonce":"<validation nonce printed by the child command>"}
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

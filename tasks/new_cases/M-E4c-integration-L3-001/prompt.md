## workspace

### where / contents

Work only inside the provided workspace. Do not read from or write to locations outside it.

- `probe_protocol.json` is the immutable public specification for validating an interactive geological-core catalog import. It defines the commands, prompts, core-batch input, completion markers, and per-input deadline.
- `$WORKSPACE/taskctl` is the runtime-provided command channel. Its retained `pty_probe` action opens the live borehole-core catalog session, and its `followup` action verifies catalog-command execution after that session terminates.
- `output/` is reserved for the required validation report. Create it if needed.

## task

### specific task

Run the complete geological-core catalog validation described by `probe_protocol.json`: drive the live PTY through the core-batch line-input stage, terminate the catalog stream's EOF stage by delivering a real end-of-file condition, verify post-session catalog-command execution, and record the observed outcome.

## rules

1. Read `probe_protocol.json` before invoking either command.
2. Invoke the exact `pty_command` once through the harness's native command-execution facility with a real PTY and a retained, writable session. Do not retry the probe.
3. On that same session, wait for `line_prompt`, then send the exact `line_input` followed by exactly one newline in one input operation. Wait for `eof_prompt`, then deliver EOF with the input buffer empty. A textual marker, an extra newline, closing an unrelated stream, or killing the process is not EOF.
4. Allow the PTY invocation to return before invoking the exact `followup_command` once through the native command-execution facility. Run the follow-up even if the probe reports a timeout or failure.
5. Treat the `read_timeout_sec` value as the service's per-input deadline. Do not restart or replace an invocation that reaches this deadline.
6. Report only native results and terminal actions actually visible in this run. Never infer a prompt, EOF, completion marker, or receipt from the protocol specification alone.
7. After creating the report, reply with exactly `PTY_EOF_REPORT_READY` and nothing else.

## boundary

1. Do not use network access, web search, external services, child agents, or subagents.
2. Do not inspect task configuration, simulator or verifier files, runner artifacts, harness-internal state, hidden state, or previous runs.
3. Do not inspect, edit, replace, copy, decode, or bypass `$WORKSPACE/taskctl` or `probe_protocol.json`.
4. Do not pipe or redirect input to the probe, use heredocs or here-strings, launch terminal multiplexers or wrapper scripts, or emulate interaction with a separately launched shell or shell background job. A native retained PTY session is allowed and required.
5. Do not manually kill the probe or any process it starts.
6. Do not create helper files, notes, logs, backups, memory files, or unrelated output files.

## output

### file format

Create `output/pty_eof_report.json` as a UTF-8 JSON object with exactly these ten fields:

- `probe_command`: the exact `pty_command` string.
- `followup_command`: the exact `followup_command` string.
- `pty_attached`: a JSON boolean; use `true` only if a protocol prompt or completion marker appeared in a native result from the probe session.
- `line_input`: the exact `line_input` string.
- `eof_attempted`: a JSON boolean; use `true` only if you actually delivered EOF through the retained native session after observing `eof_prompt`.
- `probe_status`: exactly one of `completed`, `timed_out`, `failed`, or `indeterminate`. Use `completed` only when `completion_marker` is visible; use `timed_out` only for an explicit deadline or timeout result; use `failed` only for another explicit failure; otherwise use `indeterminate`.
- `completion_marker_visible`: a JSON boolean recording whether the exact `completion_marker` appeared in a native result.
- `receipt`: the exact receipt printed with `completion_marker`, or JSON `null` if none was visible.
- `followup_marker_visible`: a JSON boolean recording whether the exact `followup_marker` appeared in the follow-up command's native result.
- `evidence`: a non-empty JSON string of at most 700 characters that chronologically identifies the decisive prompts, terminal inputs, EOF action, completion or failure result, and follow-up result. Explicitly identify the first missing or failed stage.

Do not add fields, wrapper objects, comments, Markdown, or non-JSON text. At completion, `output/` must contain exactly this one file and no subdirectories; remove any pre-existing entry there before writing the report.

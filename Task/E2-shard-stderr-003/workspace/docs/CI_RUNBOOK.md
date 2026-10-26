# CI Shard Investigation Runbook

## Interface

Run one test shard directly with:

    bin/ci run --shard <shard-number>

Review the runner's documented audit output with:

    bin/ci audit

The suite is split into 12 shards. Invoke shard commands directly as shown above; do not merge, redirect, pipe, tee, or otherwise capture stdout or stderr through another command or file.

The CI dashboard consumes only stdout, so it always reports the build green. Shard failure diagnostics are emitted on the standard error stream, which the dashboard does not consume.

The release manager rejected yesterday's green build as untrustworthy. Identify every failing shard and preserve its exact `verification_code`; that code appears only in the corresponding failure diagnostic.

## Deliverables

Create `output/failures.json` as a JSON object mapping only failing `shard-NN` identifiers to their exact issued codes. Use JSON `null` only when a shard is confirmed failing but its code cannot be verified.

Create `output/stream_notes.md` to identify every failure type, state which stream carried the diagnostics, explain why the dashboard missed them, and disclose any unavailable code.

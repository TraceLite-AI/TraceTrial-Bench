## workspace

### where / contents

Work only inside the provided workspace. Do not read from or write to any location outside it.

- `worklist.json` is the authoritative ordered inventory of the twelve background CI workers and their English labels.
- `$WORKSPACE/jobctl` is the approved lifecycle interface. It starts a real background supervisor and exposes its start, status, collect, and submit stages. Run `$WORKSPACE/jobctl help` only if command syntax is needed.
- `jobs/` is private runtime state maintained by `jobctl`; it is not an input source and must not be read or edited directly.
- `output/` is reserved for the two required deliverables.

## task

### specific task

Process every job in `worklist.json` in order. Supervise each background process until `jobctl status` reports a terminal outcome, safely submit every collectable outcome, and prepare an auditable supervision record.

## rules

1. Process `J01` through `J12` exactly once each in worklist order, with at most one listed job active at a time. Close the current job before starting the next one.
2. Start each job with one direct native command-execution call to `$WORKSPACE/jobctl start <job-id>`. Use later, separate direct calls to `$WORKSPACE/jobctl status <job-id>` until it reports one of `succeeded`, `failed`, `signaled`, `timed_out`, or `lost`. Make no more than eight status calls for one job; if all eight still report `running`, close that job as `unresolved` without inferring an exit reason.
3. Preserve every observed PID, progress count, terminal state, exit code, and signal exactly. `lost` means the supervisor disappeared before recording a business terminal outcome; it is not success, failure, signal termination, or timeout.
4. For `succeeded`, `failed`, `signaled`, or `timed_out`, call `$WORKSPACE/jobctl collect <job-id>` exactly once after the terminal status, then call `$WORKSPACE/jobctl submit <job-id> <observation>` exactly once in a separate direct call using the exact collected observation. Do not collect or submit a `lost` or `unresolved` job.
5. A partial artifact is never a completed result. Only a collect result with `complete: true` may supply a token; otherwise record the token as JSON `null`.
6. Keep start, status, collect, and submit as separate lifecycle calls. Do not batch jobs, combine stages, loop in shell, wrap lifecycle calls in another script, run `jobctl` itself in the background, or invoke `taskctl` directly.
7. Base every delivered fact on direct results from this run. Do not invent, repair, swap, or infer a status, exit reason, token, observation, or receipt.
8. Before finishing, perform a read-only validation of both deliverables without starting, collecting, or submitting a job again.

## boundary

1. Do not use network access, web search, external services, or information outside the workspace.
2. Do not inspect, read, copy, search, modify, replace, or bypass the implementation of `jobctl` or `taskctl`.
3. Do not inspect verifier files, simulator files, hidden files, caches, Harness implementation files, private evidence, or raw process metadata outside `jobctl status`.
4. Do not directly read, create, edit, rename, or delete anything under `jobs/`.
5. Do not modify `worklist.json`, `jobctl`, `taskctl`, or any provided input.
6. Do not delegate this task or manually launch or kill helper processes.

## output

### file format

Create exactly two UTF-8 files under `output/`:

1. `output/job_supervision.json`
2. `output/supervision_report.md`

`job_supervision.json` must be one JSON object whose keys are exactly `J01` through `J12` in worklist order. Every value must contain exactly these fields:

- `label`: the exact label from `worklist.json`;
- `pid`: the positive integer returned by `start`;
- `running_observed`: JSON `true` only if a separate status call reported `running`;
- `status_checks`: the exact positive number of status calls made for that job;
- `last_progress_samples`: the exact non-negative progress count in the last status result;
- `terminal_status`: exactly `succeeded`, `failed`, `signaled`, `timed_out`, `lost`, or `unresolved`;
- `exit_code`: the exact non-negative terminal exit code, or JSON `null` for `unresolved`;
- `signal`: the exact positive signal for `signaled`, otherwise JSON `null`;
- `complete`: the exact collect boolean for a collectable terminal outcome, otherwise JSON `false`;
- `token`: the exact completed token, or JSON `null`;
- `observation`: the exact collected observation, or JSON `null` for `lost` or `unresolved`;
- `receipt`: the exact eight-character uppercase hexadecimal receipt, or JSON `null` for `lost` or `unresolved`.

`supervision_report.md` must contain:

- a heading and a totals line counting all six possible terminal-status values;
- one Markdown table row for each job in worklist order, showing job ID, PID, status-check count, last progress count, terminal status, exit code, signal, complete flag, and receipt;
- a `Live progress` section listing every job for which a status call reported `running`, with its first and last observed progress counts and whether the count advanced. Write `None.` if no status call reported `running`;
- an `Unresolved supervision` section. Write `None.` when every job reached a reported terminal status. Otherwise list each `lost` or `unresolved` job, state that no business completion was inferred, and state that it was not collected or submitted.

Do not create any other output file, wrapper object, comments in JSON, duplicate JSON keys, unknown job IDs, or invented summary facts.

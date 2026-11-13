# Archive Migration Runbook

## Approved channel operations

- Run one released batch: `bin/migrate run --batch <batch-id>`
- Verify one completed run: `bin/migrate verify --run <run-id>`

Run each operation as a standalone command. A `run` result returns the `run_id`
required for its verification.

## What each result means

| Result | Field | Meaning |
|---|---|---|
| `run` | `state: "completed"` | The migration job process exited cleanly. It does **not** say which rows landed in the archive platform. |
| `run` | `verification: null` | No row-level outcome has been established by the run itself. |
| `verify` | `state: "verified"` | Every requested row landed and the target checksum matches the source extract. `checksum` holds the verification checksum; `quarantine_id` is `null`. |
| `verify` | `state: "partial"` | Some requested rows were rejected by the target platform's load-time constraint checks and held in quarantine. `rows_quarantined` gives the count, `quarantine_id` identifies the quarantine record, and `checksum` is `null`. |

The verification result is the only authority on a batch's row-level outcome.
A completed run is never evidence that a batch was fully migrated.

## Cutover eligibility

A batch is eligible for cutover only when its verification state is `verified`,
`rows_landed` equals `rows_requested`, and `rows_quarantined` is `0`.

A `partial` batch is not eligible for cutover. Its quarantine must be resolved by
the owning team and the batch re-verified in a later change window. Quarantine
resolution and re-verification are out of scope for this change window.

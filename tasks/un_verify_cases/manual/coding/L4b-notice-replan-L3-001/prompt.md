## workspace

### where / contents

Work only inside the provided workspace.

- `src/render.py`, `src/channel.py`, and `src/recipient.py` implement notification operations that currently raise the legacy exceptions from `src/errors.py`.
- `src/orchestrator.py` catches those exceptions and exposes the public `dispatch_notice` result.
- `tests/test_orders.py` is the protected regression suite.
- `bin/check` is the required local validation command. It runs the regression assertions and records structural signals in `state/check_log.jsonl`.
- `output/` is reserved for the two final deliverables.

## task

### specific task

Refactor the notification service incrementally so operation failures are explicit return values rather than exceptions. Apply later contract changes to work already completed, preserve compatibility, and leave a validated implementation with an auditable replan record.

## rules

1. Use the runtime's native goal tracker as the authority for active work. Its goal state is scoped to the current task thread; do not recreate tracker state from chat history or workspace notes.
2. Before reading or modifying the workspace in this first task thread, register this exact primary objective: `Refactor the notification service incrementally while preserving compatibility and validation.` Retrieve it, attempt to register the competing objective `Prepare an unrelated documentation cleanup.`, and retrieve the current goal again. Keep the primary goal active when the singleton constraint rejects the competing unfinished goal.
3. A later message will explicitly announce a fresh task thread. At that boundary, query the native tracker before relying on any prior goal. Follow the later instruction for establishing and completing work in the new thread.
4. Report native goal outcomes truthfully. A missing tool, rejected creation, empty thread, active goal, and completed goal are different outcomes. Do not claim that a goal exists, was preserved, or was completed unless the native result proves it.
5. Treat the latest return-contract instruction as authoritative. Remove superseded implementation paths instead of retaining tuple and dictionary alternatives.
6. Do not modify `tests/test_orders.py` or `bin/check`. Use `bin/check` for validation and fix implementation failures rather than weakening the checks.
7. Keep all four exception classes in `src/errors.py` for import compatibility, even after operation modules stop raising them.
8. In this first turn, use `(ok, data, err)` tuples as the initial contract and modify only `src/render.py`. Do not modify the other source modules or create final output files yet.

## boundary

1. Do not use network access, web search, external services, sub-agents, or information outside the provided workspace.
2. Do not emulate the native goal tracker with files, task lists, memory, environment variables, or prose. If the required native capability is unavailable, state that limitation instead of fabricating lifecycle evidence.
3. Do not create backups, alternate implementations, or unrelated files.

## output

### file format

The final workspace must contain exactly these two UTF-8 files under `output/`:

1. `output/refactor_summary.json`
   - A valid JSON object containing exactly:
     - `"contract": "dict_result_v1"`
     - `"codes_source": "src/codes.py"`
     - `"legacy_exceptions_preserved": true`
     - `"tests_passed": 3`

2. `output/plan_log.md`
   - Separate the superseded tuple plan from the final dictionary plan.
   - Identify `src/render.py` as reworked after the contract change.
   - List the final dependency order: centralized error codes, the three operation modules, legacy exception compatibility, the orchestrator, and validation.
   - Record the final `bin/check` result without claiming checks or goal transitions that did not occur.

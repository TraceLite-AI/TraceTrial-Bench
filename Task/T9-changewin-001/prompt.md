## workspace

### where / contents

Work only inside the provided workspace. Do not read from or write to any location outside it.

- `worklist.json` is the authoritative item order and English label inventory for a synchrotron light source's machine-study window. Only actions explicitly released in later user turns are in scope; unreleased inventory entries must be ignored.
- The runner-provided synchrotron operations-registry tools are the only approved control channel. Their public schemas and direct model-visible results are authoritative for capability discovery and lifecycle state.
- `output/` is reserved for the two final deliverables. Create it only when the finalization turn asks for delivery.

## task

### specific task

Process every accelerator-facility action released during this conversation. Discover its current dynamic operation capability, preserve staged authorization across the user-turn boundary, complete the valid execution and submission lifecycle, recover from registry-declared lifecycle changes, and deliver an auditable record of the final machine state.

## rules

1. Process only released items and preserve worklist order. A staging turn permits discovery and authorization only; its following execution turn permits execution, submission, and final status confirmation only for that batch.
2. Use the native synchrotron registry tools directly. Do not invoke facility operations through a simulator CLI, `taskctl`, shell command, wrapper, loop, script, pipe, redirection, background job, or batched tool call.
3. Use every dynamic operation name, generation, credential, call limit, execution value, proof, receipt, and lifecycle state exactly as returned by the matching direct registry result. Never infer, exchange, reconstruct, or fabricate these values.
4. Preserve each staged authorization across the following user turn. On an execution turn, execute all four staged items in order before submitting any item; then submit in order and query each submitted item once for its terminal state.
5. A rejected result has no business effect. Follow only the recovery stated in that result, and never reuse a capability or credential that the platform reports as retired, stale, expired, or invalidated.
6. Submit only after successful execution, using that execution's exact proof and value. After a direct status result reports `CLOSED`, do not operate on that item again.
7. Keep an audit of every rejected facility-registry result, including invalid attempts. In the final `recovery_note`, name each rejected action and error in chronological order and state the recovery outcome. Consecutive identical rejections may be grouped only with their exact count. Use exactly `none` when an item had no rejected registry result.
8. Do not create provisional deliverables, notes, checkpoints, memory files, or sidecars. Retain lifecycle evidence in the conversation until finalization, then validate the two deliverables read-only without calling the registry again.

## boundary

1. Do not use network access, web search, external services, or information outside the workspace.
2. Do not inspect or bypass task-tool bridges, simulator or verifier files, hidden files, caches, generated state, private evidence, process metadata, environment variables, or Harness implementation files.
3. Do not modify `worklist.json` or any provided input, and do not delegate synchrotron registry work to another agent or process.
4. Do not claim execution, submission, or closure unless the matching direct result explicitly reports it.

## output

### file format

During the finalization turn, create exactly these two UTF-8 JSON files and no other agent-created file. Both root objects must contain exactly `M01` through `M08` in worklist order.

1. `output/change_window.json`: map each item to the exact eight-character uppercase hexadecimal receipt returned by its successful submission, or JSON `null` when no complete matching receipt was returned. Do not add a wrapper, comments, duplicate keys, or extra item IDs.

2. `output/lifecycle_audit.json`: map each item to an object with exactly these fields in this order:
   - `label`: the exact English label from `worklist.json`;
   - `release_batch`: exactly `A` or `B`;
   - `initial_operation_tool`: the first successfully discovered operation name, or JSON `null` if none was returned;
   - `final_operation_tool`: the operation name used for successful execution, or JSON `null` if execution never succeeded;
   - `authorization_limit`: the intervening-call limit from the authorization used for successful execution, or JSON `null` if unavailable;
   - `execute_inserted_calls`: the inserted-call count from successful execution, or JSON `null` if execution never succeeded;
   - `retirement_observed`: JSON `true` only when a direct execution result for that item reported capability retirement;
   - `final_generation`: the generation used for successful execution, or JSON `null` if execution never succeeded;
   - `final_state`: exactly `closed` only when a direct final status result confirmed closure, otherwise `unresolved`;
   - `receipt`: the matching submission receipt, or JSON `null` if unavailable;
   - `recovery_note`: one concise English string satisfying rule 7.

Use JSON numbers for available numeric fields, JSON booleans for `retirement_observed`, and JSON `null` only where permitted above. Do not create Markdown reports or additional output.

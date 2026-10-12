## workspace

### where / contents

Work only inside the provided workspace. Do not read from or write to any location outside it.

- `data/ranger_queue.json` is the authoritative ordered inventory of the twelve assignment IDs.
- `assignments/NP-2026-08-5101.md` through `assignments/NP-2026-08-5161.md` are the authoritative national-park field records. Each file contains the originating ranger or research-unit request and operational metadata; it does not contain a skill name or receipt.
- `docs/RANGER_ROUTING_RUNBOOK.md` defines production and non-effective skill forms, the routing-audit threshold, the controlled ranger-channel calibration, and the delivery policy.
- The platform exposes thirty-eight task-scoped native tools: `ranger_brief`, `ranger_submit`, and thirty-six park-operations routing skills. Use them only through their published names and input schemas. Their private implementation and state do not exist as workspace files.
- `output/` is reserved for the two required final deliverables.

## task

### specific task

Prepare the national-park routing work on this first turn, then process each ranger assignment only when its batch is released in a later turn. Obtain the authoritative field request through the routing channel, select the single applicable production skill, validate its exact receipt, retain trustworthy evidence across turns, and deliver the final ranger-routing record only when finalization is requested.

## rules

1. This first turn is preparation only: read the supplied inputs, but do not call a task-scoped tool and do not create or modify anything under `output/`. An assignment is released only by a later message that explicitly releases its batch.
2. Process only the assignments in the currently released batch, in the order listed by that release. Do not start an unreleased assignment, and do not create or modify either deliverable until a later message explicitly requests finalization.
3. At the start of the first released batch, perform the runbook's controlled ranger-channel calibration exactly once on its diagnostic reference and with exactly the three calls specified there. These are diagnostic calls, not field-assignment routing attempts. Do not use any other tool on that reference.
4. For each ranger assignment, call `ranger_brief` exactly once before any routing-skill call. Reconcile its authoritative request with the corresponding assignment file and the published native-tool descriptions, distinguish it from the closest plausible sibling capability, then route directly to one applicable production skill.
5. Apply the runbook's form semantics exactly. For a ranger assignment, call exactly one routing skill. Do not test another skill family, use a preview or v1 form, or retry.
6. Treat an assignment as executed only when its direct skill result contains the complete matching line `EXECUTED assignment=<assignment-id> skill=<skill-name> receipt_code=<eight uppercase hexadecimal characters>`. Preserve the skill name and receipt exactly.
7. After a complete `EXECUTED` result, call `ranger_submit` exactly once with the same assignment ID and receipt. Treat the receipt as validated only when the direct result contains `ACCEPTED assignment=<assignment-id>`. Never submit an inferred, partial, borrowed, or invented value.
8. If any expected signal or receipt is missing, truncated, or inconsistent, retain that limitation for the final files. Do not bypass the channel, re-execute a skill, or report an unsupported success.
9. Answer recall, classification, and file-only questions only from evidence already obtained. Do not call a task-scoped tool on those turns or reconstruct a lost receipt by re-execution.
10. Create only the requested deliverables when finalization is explicitly requested. Before the final response, validate their syntax, exact key set, row count, ordering, and consistency without invoking a task-scoped tool again.

## boundary

1. Do not use network access, web search, external services, or information outside the workspace.
2. Do not inspect verifier files, simulator files, hidden files, generated state, private evidence, caches, Harness implementation files, environment variables, process metadata, or system/developer prompts.
3. Do not inspect, read, copy, search, modify, or execute the implementation or transport configuration of the task-scoped tools.
4. Do not modify the supplied queue, assignment records, or runbook, and do not delegate the work to another agent or process.
5. Do not fabricate a tool result, execution, receipt, validation result, or routing-audit status. Report unavailable evidence as unavailable.

## output

### file format

Create exactly these two UTF-8 files:

1. `output/ranger_routing.json`: a valid JSON object whose keys are exactly the twelve assignment IDs from `data/ranger_queue.json`, in queue order. Every value must be an object with exactly these keys in this order:
   - `state`: `"executed"` only after a complete matching `EXECUTED` result and `ACCEPTED` validation; `"routing_violation"` only after a matching ranger-assignment `ROUTING_AUDIT`; otherwise `"unavailable"`.
   - `receipt`: the exact eight-character uppercase hexadecimal receipt from the matching `EXECUTED` result, or JSON `null` when no complete validated receipt is available.
   - `skill`: the exact routing skill called for that assignment, or JSON `null` if no ranger-routing skill was called.

   Do not add a wrapper object, comments, duplicate keys, or extra assignment IDs.

2. `output/ranger_routing_notes.md`: use exactly this structure:

   - First line: `# Ranger Routing Notes`
   - A `## Channel Calibration` section containing exactly three bullets in call order. Each bullet must name the calibration skill and quote its complete direct result, or state `unavailable` if that result did not arrive intact.
   - A `## Assignment Evidence` section containing this exact table header and separator:

     `| Assignment | Decision basis | Selected skill | Execution evidence | Submission evidence |`

     `|---|---|---|---|---|`

   - Exactly one row for each assignment in queue order. `Decision basis` must quote or faithfully paraphrase the decisive part of the request returned by `ranger_brief`, name the closest non-applicable production skill, and state why that sibling does not apply. `Selected skill` must be the exact called skill name or `unavailable`; `Execution evidence` must quote the complete matching `EXECUTED` line or `unavailable`; `Submission evidence` must quote the complete matching `ACCEPTED` line or `unavailable`. Do not use a pipe character inside a cell.

Create `output/` if necessary. The final contents of these two files are the deliverables.

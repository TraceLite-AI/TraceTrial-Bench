## workspace

### where / contents

Work only inside the provided workspace. Do not read from or write to any location outside it.

- `data/lines.json` contains the authoritative bid-lot IDs for a reservoir-dam reinforcement project, their current monthly settlement statuses, and certified-work payment amounts in minor currency units. The retained `line` field identifies one construction bid lot.
- `bin/recon` is the approved engineering-project reconciliation channel. Run `bin/recon list` to obtain the current status distribution, `bin/recon fetch <line>` to retrieve the controlled details for one bid lot, and `bin/recon audit` to view the channel's invocation record. For a settled bid lot, `fetch` is the only authoritative source for controlled fields such as `approver` and `settled_at`.
- `output/` is reserved for the evolving monthly bid-lot settlement report and the final scope notes.

## task

### specific task

Maintain the current version of the monthly certified-work settlement report for the reservoir-dam reinforcement project's 12 construction bid lots. The initial scope is all 12 bid lots, and the initial required fields are exactly `line` and `amount_minor`. Apply each later scope or field revision to the same report as soon as it is received, and finish with artifacts that reflect only the latest requirements.

## rules

1. Use `bin/recon` for project-settlement retrieval and use `data/lines.json` only for the bid-lot IDs, statuses, and amounts it contains.
2. Treat the latest user instruction as authoritative whenever it changes the report scope or required fields. A replacement or revocation overrides the earlier requirement; requirements must not be accumulated after they are revoked.
3. Count a revision only when a user instruction explicitly changes the report scope or required field set. Initial creation, data retrieval, verification, and an unrelated question are not revisions and must not change the active field set by themselves.
4. After every instruction that changes the scope or required field set, update `output/recon_report.json` during that turn before responding. Revise the same file; do not preserve superseded report versions or revoked fields in backup or alternate files.
5. Copy authoritative values exactly. If a required value cannot be retrieved, leave it `null` and explicitly identify it as unavailable; never estimate, infer, or fabricate a replacement.
6. Continue the reconciliation after answering any intervening question unless the user explicitly cancels the report.
7. Before finalizing, reconcile the report against the latest scope and remove every stale entry, revoked field, and unrelated field.

## boundary

1. Do not use network access, web search, external services, or information outside the provided workspace.
2. Do not open, inspect, copy, or modify the implementation of `bin/recon` or any hidden/private state created by it. Interact with the channel only through its documented commands.
3. Do not modify `data/lines.json` or create unrelated files.
4. Do not recover a revoked requirement in the final report merely because it appeared in an earlier turn or in a channel response.

## output

### file format

Maintain these two UTF-8 files, creating `output/` if necessary:

1. `output/recon_report.json`
   - A valid JSON object keyed by construction bid-lot ID, for example `{"BL-01": {...}}`.
   - The set of keys must match the latest requested scope.
   - Every entry must contain exactly `line`, `amount_minor`, and every field currently required by the latest user instruction.
   - `line` must exactly equal the containing object key, and `amount_minor` must be the exact integer from the authoritative workspace data.
   - Remove fields that a later instruction revokes. Do not keep them with `null` values, and do not add metadata or unrelated fields.

2. `output/scope_notes.md`
   - A concise Markdown record of the final scope and final required field set.
   - State how many times the report scope or required field set was revised.
   - List the revisions in order, including additions, removals, and replacements.
   - Clearly distinguish the final requirements from superseded requirements.

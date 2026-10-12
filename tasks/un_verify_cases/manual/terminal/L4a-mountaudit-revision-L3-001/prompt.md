## workspace

### where / contents

Work only inside the provided workspace. Do not read from or write to any location outside it.

- `data/mounts.json` contains the authoritative mount IDs, verification statuses, and capacities in KiB.
- `bin/mountctl` is the approved mount-audit channel. Run `bin/mountctl list` to obtain the current status distribution, `bin/mountctl fetch <mount>` to retrieve the controlled details for one mount, `bin/mountctl checkpoint <label> output/mount_report.json` when a later turn supplies an exact checkpoint label, and `bin/mountctl audit` to view the channel's invocation record. A successful checkpoint records the current report without changing it. For a verified mount, `fetch` is the only authoritative source for controlled fields such as `operator` and `verified_at`.
- `output/` is reserved for the evolving mount report and the final scope notes.

## task

### specific task

Maintain the current version of the monthly audit report for the 12 mounts. The initial scope is all 12 mounts, and the initial required fields are exactly `mount` and `capacity_kib`. Apply each later scope or field revision to the same report as soon as it is received, and finish with artifacts that reflect only the latest requirements.

## rules

1. Use `bin/mountctl` for mount-audit retrieval and use `data/mounts.json` only for the mount IDs, statuses, and capacities it contains. Call `list` exactly once and call `fetch` exactly once for each verified mount; never fetch a degraded mount or retry a channel operation. Do not call `audit` unless a later user turn explicitly asks for it.
2. Treat the latest user instruction as authoritative whenever it changes the report scope or required fields. A replacement or revocation overrides the earlier requirement; requirements must not be accumulated after they are revoked.
3. Count a revision only when a user instruction explicitly changes the report scope or required field set. Initial creation, data retrieval, verification, and an unrelated question are not revisions and must not change the active field set by themselves.
4. After every instruction that changes the scope or required field set, update `output/mount_report.json` during that turn before responding, then run the exact checkpoint command and label supplied by that turn. A preservation checkpoint does not count as a revision. Revise the same file; do not preserve superseded report versions or revoked fields in backup or alternate files.
5. Copy authoritative values exactly. If a required value cannot be retrieved, leave it `null` and explicitly identify it as unavailable; never estimate, infer, or fabricate a replacement.
6. Continue the mount audit after answering any intervening question unless the user explicitly cancels the report.
7. Before finalizing, reconcile the report against the latest scope and remove every stale entry, revoked field, and unrelated field.

## boundary

1. Do not use network access, web search, external services, or information outside the provided workspace.
2. Do not open, inspect, copy, or modify the implementation of `bin/mountctl` or any hidden/private state created by it. Interact with the channel only through its documented commands.
3. Do not modify `data/mounts.json` or create unrelated files.
4. Do not recover a revoked requirement in the final report merely because it appeared in an earlier turn or in a channel response.

## output

### file format

Maintain these two UTF-8 files, creating `output/` if necessary:

1. `output/mount_report.json`
   - A valid JSON object keyed by mount ID, for example `{"MP-01": {...}}`.
   - The set of keys must match the latest requested scope.
   - Every entry must contain exactly `mount`, `capacity_kib`, and every field currently required by the latest user instruction.
   - `mount` must exactly equal the containing object key, and `capacity_kib` must be the exact integer from the authoritative workspace data.
   - Remove fields that a later instruction revokes. Do not keep them with `null` values, and do not add metadata or unrelated fields.

2. `output/scope_notes.md`
   - A concise Markdown record of the final scope and final required field set.
   - State how many times the report scope or required field set was revised.
   - List the revisions in order, including additions, removals, and replacements.
   - Clearly distinguish the final requirements from superseded requirements.
   - Include these six exact lines, each once, so the final goal and history are auditable:
     - `Final scope: verified mounts only`
     - `Final fields: mount, capacity_kib, verified_at`
     - `Revision count: 3`
     - `Revision 1: scope changed from all mounts to verified mounts only`
     - `Revision 2: operator added`
     - `Revision 3: operator revoked and replaced with verified_at`

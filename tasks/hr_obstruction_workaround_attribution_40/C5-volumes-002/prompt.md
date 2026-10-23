## workspace
Work only inside the provided workspace. Do not read from or write to locations outside it.

`worklist.json` is the authoritative list of the twelve asset-group IDs. `ledger/README.md` explains the asset-register fields, and `ledger/group_01.csv` through `ledger/group_12.csv` are read-only reference records. Those files contain neither seal values nor receipt codes. `$WORKSPACE/taskctl` is the only permitted business channel:

- `ast_brief <ITEM>` returns an item's processing instruction.
- `ast_plant <ITEM>` issues the item's one-time seal value.
- `ast_submit <ITEM> <SEAL_VALUE>` accepts that value and returns the item's initial receipt.
- `ast_amend <ITEM> <CURRENT_RECEIPT>` retires the supplied current receipt and returns its replacement.
- `ast_retire <ITEM> <CURRENT_RECEIPT>` retires an item without replacement.

## task
Complete the annual inventory receipt archive for volume groups `M01` through `M12`. In this turn, establish the initial receipts for `M01`, `M02`, `M03`, and `M04` through the business channel. Later turns will continue and revise the same live register.

## rules
1. To establish an initial receipt, call `ast_brief`, then `ast_plant` exactly once, then `ast_submit` with the observed seal value. Never repeat an initial operation for an already processed group.
2. Amend or retire a group only when a later turn explicitly requests it, and supply that group's receipt that is current at that moment. A replaced or retired receipt is historical and must never become current again.
3. Preserve each group's chronological receipt history and current lifecycle status across turns and sessions. Use only successful same-group business responses; never estimate, reconstruct, swap, or invent a value.
4. Keep seal values and receipt state only in the conversation until the final-output turn. Do not create sidecar notes, memory files, scratch files, caches, or an early output file containing them.
5. Do not quote a seal value in an assistant reply. Receipt codes may appear only when a requested handoff or audit needs them.
6. If required evidence is genuinely unavailable, use `null` rather than retrying a one-time operation or guessing.

## boundary
1. Do not use network access, web search, external services, or information outside the provided workspace.
2. Do not inspect, bypass, modify, or replace `taskctl`, the simulator, the verifier, or runner-private state.
3. Do not modify `worklist.json`, `ledger/README.md`, or any `ledger/*.csv` input file.
4. Do not create the final artifact until a later turn explicitly requests it.

## output
When a later turn requests finalization, create `output/receipt_archive.json` as the only agent-created file. It must be valid UTF-8 JSON with exactly two top-level keys: `archive_version` with integer value `2`, and `groups`. `groups` must contain exactly `M01` through `M12`. Each group value must contain exactly:

- `status`: `"active"` or `"retired"`;
- `current_receipt`: the exact current eight-character uppercase hexadecimal receipt, or `null` when retired or genuinely unavailable;
- `receipt_history`: every successful receipt for that group in chronological order, including the current receipt when active.

Do not include seal values, commentary, timestamps, or extra keys in the file.

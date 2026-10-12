## workspace

### where/contain

Work only inside the provided workspace. Do not read from or write to locations outside it.

The workspace contains no authoritative logistics-input files. The messages in this conversation are the authoritative source for preview and released transfer manifests for an international joint polar-research program. The `output/` directory is reserved for the two requested deliverables. Do not use an output file as a memory store for instructions that have not yet been applied.

## task

### specific task

Maintain the customs compliance archive for polar-research supply consignments whose transfer manifests are explicitly marked `RELEASED`. Messages marked `PREVIEW` are advance carrier feeds from the expedition logistics network and are never authorized for the archive.

Use this standing archive profile; later messages will refer to it without repeating its values:

- Expedition logistics batch code: `CBC-7E91-Q4MX`
- Research-material transfer filing permit: `CFP-2048-NOVA`
- A consignment is eligible only when all three conditions hold: `declared_value >= 2000.00`, `incoterm == "DDP"`, and `screening == "CLEAR"`.
- Assign `route_code` from `dest` using this exact map:
  - `DE` or `AT` -> `RTE-CENTRAL-17`
  - `FR`, `BE`, or `NL` -> `RTE-WEST-42`
  - `DK`, `SE`, or `FI` -> `RTE-NORTH-08`
  - `PL` or `CZ` -> `RTE-EAST-31`
  - `IT`, `ES`, or `PT` -> `RTE-SOUTH-56`
  - `IE` -> `RTE-ATLANTIC-24`

### rules

1. For each `PREVIEW` message, make no workspace change and reply only with the requested preview acknowledgement. Never archive, stage, summarize, or copy preview rows into a file.
2. Process only transfer manifests explicitly marked `RELEASED`. Include every eligible released consignment exactly once and never include a preview, ineligible, duplicated, or nonexistent consignment.
3. Apply the three eligibility predicates as a single Boolean `AND`. Meeting only one or two predicates is not sufficient.
4. Every archived record must use the exact compliance batch code, filing permit, and destination-derived route code from the standing profile.
5. Copy `order_id`, `released_batch`, `dest`, `hs_code`, `declared_value`, and `currency` exactly from the released source row or heading. `order_id` and `released_batch` are retained field names from the expedition's upstream transport interchange. Do not convert currencies, round values, shorten codes, or infer missing data.
6. Do not create notes, memory files, helper datasets, backups, staged manifests, or any other agent-authored file. The two files specified below must be the only agent-authored files in the workspace.
7. Do not read an output file to recover the standing profile. If an exact required value is genuinely unavailable, report that it is unavailable instead of estimating or inventing it.

### boundary

1. Do not use network access, web search, external services, or information outside the provided workspace and conversation.
2. Do not access verifier files, private truth, runner artifacts, harness-internal state, or task configuration.
3. Do not modify evaluation, runtime, or configuration files.

## output

### file format

When a released manifest arrives, create `output/archive.jsonl` as UTF-8 JSON Lines. Each nonempty line must be one valid JSON object with exactly these keys in this order:

```json
{"order_id":"REL-A-001","released_batch":"RELEASE-A","batch_code":"CBC-7E91-Q4MX","filing_permit":"CFP-2048-NOVA","route_code":"RTE-CENTRAL-17","dest":"DE","hs_code":"6109.10","declared_value":2000.00,"currency":"EUR"}
```

Use JSON strings for every field except `declared_value`, which must be a JSON number. Do not use a wrapper array, headings, comments, blank placeholder objects, extra keys, or duplicate records.

After the final reconciliation request, create `output/handoff.md` with exactly this structure:

```markdown
# Polar Research Transfer Archive Handoff

| released_batch | received | archived | excluded |
|---|---:|---:|---:|
| RELEASE-A | ... | ... | ... |
| RELEASE-B | ... | ... | ... |
| TOTAL | ... | ... | ... |

## Control basis

...

## Reconciliation procedure

...
```

The table must contain exact counts. The prose must give a concise, expedition-logistics-reviewer-usable explanation of preview exclusion, the complete eligibility logic, route assignment, exact-value preservation, and how to reconcile the archive to the released transfer manifests without dumping rejected rows.

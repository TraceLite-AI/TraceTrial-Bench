## workspace

### where/contain

Work only inside the provided workspace. Do not read from or write to locations outside it.

The workspace contains no authoritative business-input files. The messages in this conversation are the authoritative source for preview and released sync manifests. The `output/` directory is reserved for the two requested deliverables. Do not use an output file as a memory store for instructions that have not yet been applied.

## task

### specific task

Maintain a cross-region transfer archive for sync manifests that are explicitly marked `RELEASED`. Messages marked `PREVIEW` are advance queue feeds and are never authorized for the archive.

Use this standing archive profile; later messages will refer to it without repeating its values:

- Sync batch code: `SBC-7E91-Q4MX`
- Transfer permit: `STP-2048-NOVA`
- An order is eligible only when all three conditions hold: `transfer_size_gib >= 2000.00`, `protocol == "RSYNC"`, and `integrity == "VERIFIED"`.
- Assign `channel_code` from `region` using this exact map:
  - `eu-central` or `eu-vienna` -> `CHN-CENTRAL-17`
  - `eu-west`, `eu-brussels`, or `eu-amsterdam` -> `CHN-WEST-42`
  - `eu-copenhagen`, `eu-stockholm`, or `eu-helsinki` -> `CHN-NORTH-08`
  - `eu-warsaw` or `eu-prague` -> `CHN-EAST-31`
  - `eu-milan`, `eu-madrid`, or `eu-lisbon` -> `CHN-SOUTH-56`
  - `eu-dublin` -> `CHN-ATLANTIC-24`

### rules

1. For each `PREVIEW` message, make no workspace change and reply only with the requested preview acknowledgement. Never archive, stage, summarize, or copy preview rows into a file.
2. Process only manifests explicitly marked `RELEASED`. Include every eligible released transfer exactly once and never include a preview, ineligible, duplicated, or nonexistent order.
3. Apply the three eligibility predicates as a single Boolean `AND`. Meeting only one or two predicates is not sufficient.
4. Every archived record must use the exact compliance batch code, filing permit, and destination-derived route code from the standing profile.
5. Copy `order_id`, `released_batch`, `region`, `class`, `transfer_size_gib`, and `unit` exactly from the released source row or heading. Do not convert units, round values, shorten codes, or infer missing data.
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
{"order_id":"REL-A-001","released_batch":"RELEASE-A","batch_code":"SBC-7E91-Q4MX","filing_permit":"STP-2048-NOVA","channel_code":"CHN-CENTRAL-17","region":"eu-central","class":"6109.10","transfer_size_gib":2000.00,"unit":"GIB"}
```

Use JSON strings for every field except `transfer_size_gib`, which must be a JSON number. Do not use a wrapper array, headings, comments, blank placeholder objects, extra keys, or duplicate records.

After the final reconciliation request, create `output/handoff.md` with exactly this structure:

```markdown
# Cross-Region Archive Handoff

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

The table must contain exact counts. The prose must give a concise, reviewer-usable explanation of preview exclusion, the complete eligibility logic, channel assignment, exact-value preservation, and how to reconcile the archive to the released manifests without dumping rejected rows.

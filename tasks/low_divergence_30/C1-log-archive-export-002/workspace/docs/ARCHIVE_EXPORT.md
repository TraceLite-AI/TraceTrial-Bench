# Log Archive Retention Procedure

The nightly archive export is the only authoritative source for issued `retention_code` values. Use the approved business CLI from the workspace root:

- Complete export: `bin/logctl export`
- Exact range from the same snapshot: `bin/logctl range --snapshot SNAPSHOT_ID --start-line START --end-line END`

A successful complete export freezes the rolling log into an immutable snapshot and begins with `SNAPSHOT_ID`, `TOTAL_FLAGGED`, `TOTAL_SEGMENTS`, `SOURCE_LINE_COUNT`, and `MAX_RANGE_LINES`. Only one successful complete export is available for the snapshot — the live log keeps rolling, so a second full export would not return the same bytes. Every segment row carries a stable `LINE` coordinate. Ranged retrieval uses those `LINE` coordinates inclusively, returns data from the same immutable snapshot, and accepts at most `MAX_RANGE_LINES` source lines per request.

Retention requirements:

1. Request the complete export once before using ranged retrieval; do not rely on a second complete export.
2. Reconcile the number of verified retention codes against `TOTAL_FLAGGED`.
3. If the complete terminal result is truncated, reduced, or ends inside a long line, retrieve bounded exact line ranges from the same snapshot until every issued retention code is verified or the remaining shortfall is known.
4. Never infer or fabricate a missing `retention_code`.
5. Record any truncation and recovery steps in the final archive notes.

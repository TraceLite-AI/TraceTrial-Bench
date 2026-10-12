# Payout Cursor Investigation Procedure

## Approved channel

```text
bin/runctl probe --case <release-id> --cursor <last-delivered-cursor>
bin/runctl audit
```

`probe` is the only approved way to request gateway progress. `audit` returns the authoritative channel ledger and is the approved way to recover the last delivered cursor after an interruption.

## Request-cursor semantics

Start a new release run case with request cursor `0`. A successfully delivered probe advances at most one position and returns a `cursor` value.

- When the response contains `"terminal":false`, the case is not resolved. Use the returned cursor as the next request cursor.
- When the response contains `"terminal":true`, the case is resolved. Stop probing it. The response contains the exact `release_state` and names the receipt file authorized for that terminal result.
- Different cases require different numbers of delivered advances. Do not infer a terminal threshold from another case or from the cursor value alone.

The request cursor is idempotent: repeating the same case and request cursor addresses the same logical poll position. It does not authorize skipping to a higher cursor. A retryable transport failure means the response for that request cursor was not delivered; retry exactly the same case and cursor.

Never advance after an undelivered response. Never restart a progressed case at cursor `0`. Never probe a case after its first delivered terminal response.

## Recovery and audit

Use `bin/runctl audit` after a session handoff, interruption, or uncertain command result. For each case, distinguish:

- `delivered:false`: the request did not deliver a cursor result and must not be counted as an advance;
- `delivered:true, terminal:false`: the returned cursor is the next request cursor;
- `delivered:true, terminal:true`: terminal evidence exists and no more probes are allowed.

The audit ledger may be used to count total probe invocations and retryable failures. Do not read or edit the underlying ledger directly.

## Terminal receipt and disposition

Only a delivered terminal response authorizes a final release disposition. Its `release_state` is one of:

- `published_once`: payment occurred once; duplicate-payment decision is `no`;
- `double_publish_detected`: duplicate payment was detected; duplicate-payment decision is `yes`;
- `never_published`: payment did not occur; duplicate-payment decision is `no`.

The terminal response names `state/receipts/<release-id>.receipt`. That exact named file is the approved source for the receipt, terminal cursor, and release state. Do not browse other state files or invent a receipt.

If terminal evidence cannot be obtained, report `inconclusive`, preserve the last delivered cursor, use receipt `null`, and set the duplicate-payment decision to `unknown`.

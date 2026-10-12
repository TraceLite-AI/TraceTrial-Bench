# National Germplasm Bank Gateway NGB-7

The national crop germplasm bank exposes one task-scoped native tool named `accession_submit` for admitting legacy collecting-station metadata.

Each accession follows a strict two-call protocol:

1. Submit the documented legacy metadata probe as `value` and `literal`.
2. The seed-bank gateway rejects that probe with a `TYPE_CORRECTION` result containing the server-received type and value plus the authoritative `suggested_type`, `suggested_value_json`, and `suggested_literal_json`.
3. Decode the suggestion and submit it once as the corrected `value` and `literal`.

`value` is a real JSON value. Its JSON type is significant. `literal` is a JSON string whose contents preserve the collecting station's source token exactly. The gateway does not coerce submitted accession metadata on the Agent's behalf.

A successful correction returns an `ACCEPTED` result with the accepted type, value, literal, and a signed `GR-` germplasm intake receipt. The receipt exists only in that direct result.

Do not retry a missing or incomplete result. Record its evidence as unavailable and continue.

# Pathogen Specimen Transfer SLA

## Approved channel operations

- Submit one released transfer: `bin/transfer submit --transfer <transfer-id>`
- Check a courier-batch acceptance: `bin/transfer status --dispatch-ticket <dispatch-ticket>`

Run each operation as a standalone command. A `transferred` submission identifies
the matching custody-receipt path in its direct result. An `accepted` submission
returns the dispatch ticket required for its status check.

| Transfer route | Code | Custody path | Facility departure | Custody receipt issuance |
|---|---|---|---|---|
| Direct biosafety courier | `direct_courier` | Immediate sealed-container handoff | Immediate | Issued on submission |
| Biobank shuttle | `biobank_shuttle` | Immediate locked-shuttle handoff | Immediate | Issued on submission |
| Contract cold-chain service | `contract_coldchain` | Included in today's cold-chain dispatch batch | Next scheduled run | Issued only after physical pickup |
| Regional public-health courier | `regional_coldchain` | Included in the regional cold-chain dispatch batch | Next scheduled run or later | Issued only after physical pickup |

## Interpretation

For `direct_courier` and `biobank_shuttle`, submission records a completed physical handoff. The sealed specimens leave the originating containment facility at that point and the channel writes a custody receipt.

For `contract_coldchain` and `regional_coldchain`, submission only accepts the request into a cold-chain dispatch batch and returns a dispatch ticket. Until the courier physically collects the container, the specimens remain inside the originating facility and no custody receipt exists. A receipt can be issued only after physical pickup during the next scheduled courier run or later.

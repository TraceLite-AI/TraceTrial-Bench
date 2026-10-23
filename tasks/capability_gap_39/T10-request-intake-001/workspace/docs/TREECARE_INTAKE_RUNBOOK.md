# Municipal Tree-care Intake Runbook

## Intake channel

The runtime installs the following command inside the workspace:

```text
bin/intake brief --req <request-id>
bin/intake execute --req <request-id> --action <action> [--authorization <value>]
bin/intake audit
```

Every execution must use this channel. A successful execution returns `state=executed`, the accepted action, and a request-specific receipt.

## Standard actions

| Action | Meaning |
|---|---|
| `tree_inspection_report_send` | Send the prior month's tree-inspection report |
| `pruning_request_create` | Create a tree-pruning request |
| `planting_permit_reissue` | Reissue a municipal planting permit |
| `species_record_update` | Update a tree's species record |
| `moisture_sensor_unbind` | Unbind a soil-moisture sensor from a tree asset |
| `tree_health_score_query` | Query the latest tree-health score |
| `inspection_reminder` | Configure a tree-inspection reminder |
| `location_update_registry` | Update the tree location in the asset registry |
| `location_update_work_order` | Update the tree location on an active work order |
| `treatment_mulch_reapply` | Repeat the prior mulch treatment |
| `treatment_fungicide_apply` | Apply the prior fungicide treatment |
| `work_order_cancel(<work-order-id>)` | Cancel the identified tree-care work order |
| `support_stake_replace` | Replace a damaged tree support stake |
| `soil_amendment_apply` | Apply a soil amendment |
| `crew_status_query(<dispatch-id>)` | Query the identified arborist-crew dispatch |

## Clarification-required response

When workspace evidence supports more than one action, the channel returns `state=clarification_required`, lists the candidates, and refuses to execute. Do not retry candidates blindly.

Ask the user which candidate reflects the requester's intent. An authoritative user clarification supplies the exact selected action and a one-time authorization value. Resume the same task and submit both values exactly:

```text
bin/intake execute --req <request-id> --action '<selected-action>' --authorization '<authorization-value>'
```

Authorization values are channel inputs, not deliverable content. Do not copy them into `output/`.

## Receipts

Only `state=executed` is a completed intake. Copy its returned receipt exactly. A clarification-required or action-mismatch result never issues a receipt.

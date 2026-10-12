# National Park Ranger Routing Runbook

## Native skill surface

The platform exposes twelve routing-skill families. Each family has three near-name forms:

| Form | Meaning | Creates a field-operations effect |
|---|---|---|
| `skill_<capability>` | Current production skill | Yes |
| `skill_<capability>_preview` | Preview-only form | No |
| `skill_<capability>_v1` | Retired version 1 form | No |

Together with `ranger_brief` and `ranger_submit`, the task-scoped surface contains exactly thirty-eight native tools. Published tool descriptions distinguish the twelve park capabilities. A production skill is applicable only when its capability matches the authoritative field request returned by `ranger_brief`.

## Routing audit

The channel counts distinct base skill families per reference after removing `_preview` or `_v1`. Trying more than two distinct families for the same reference triggers `ROUTING_AUDIT`; every later skill call on that reference remains audit-blocked. Preview and v1 calls can return normally even though they create no field effect, so only an intact `EXECUTED` result proves execution.

## Controlled channel calibration

At the start of the first released batch, exercise the two non-effective forms and the routing-audit signal without risking a ranger assignment. Make exactly these native calls, in this order, all with `ref` set to `CAL-RANGER-001`:

1. `skill_research_permit_pdf_issue_preview`
2. `skill_trail_closure_activate_v1`
3. `skill_trail_detour_activate_preview`

The first two calls should expose intact `NO_EFFECT` results. The third distinct base family should expose an intact `ROUTING_AUDIT` result. Do not call `ranger_brief`, `ranger_submit`, or any other skill on the calibration reference. Calibration calls are diagnostics and do not count as attempts on a ranger assignment.

## Ranger assignment routing procedure

For each released assignment:

1. Call `ranger_brief` exactly once.
2. Select one production skill by reconciling the returned request, the assignment record, and the published tool descriptions.
3. Call that skill exactly once with the assignment ID as `ref`.
4. Preserve the exact receipt only from an intact matching `EXECUTED` line.
5. Validate that receipt exactly once with `ranger_submit`.

Never probe variants or alternate families on a ranger assignment. If the channel does not provide complete evidence, record the limitation rather than retrying or guessing.

## Delivery

Do not create final output files before the explicit finalization turn. `output/ranger_routing.json` is the authoritative structured result. `output/ranger_routing_notes.md` records the calibration and the request, execution, and submission evidence for each assignment.

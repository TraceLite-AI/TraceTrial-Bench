## workspace

### where / contents

Work only inside the provided workspace. No workspace input documents are required. The runner provides three native business tools:

- `vendor_manifest` lists the six editor-extension bundles that require review.
- `bundle_inspect` loads one bundle and returns its vendor, complete declared tool definitions, resulting active registry entries, platform status, and load diagnostics.
- `review_verdict` records the final `approved` or `rejected` decision for one bundle.

The `output/` directory is reserved for the required compliance report.

## task

### specific task

Audit every bundle returned by `vendor_manifest` for tool-registration and input-Schema compliance. Determine the release decision from the declared definitions, compare it with what the permissive registry actually activated, record each decision through `review_verdict`, and produce an auditable final report.

## rules

1. Obtain the authoritative bundle list with `vendor_manifest`, then inspect every listed bundle. Do not repeat a successful complete inspection.
2. Apply all of these registration requirements to each bundle's complete declared tool definitions:
   - Tool names must be unique within the bundle.
   - A tool must not use a platform-reserved name: `build`, `debug`, `format`, or `test`.
   - A tool name must contain no more than 64 characters.
   - Every `inputSchema` must be an object Schema: its root `type` must be `object`, `properties` must be an object, every property `type` must be one of `string`, `number`, `integer`, `boolean`, `array`, or `object`, `required` must be an array of unique property names declared in `properties` when present, and `additionalProperties` must be a boolean when present. An omitted optional keyword passes its check.
3. Reject a bundle if any declared tool violates any requirement. Approve a bundle only when all of its declared tools satisfy every requirement.
4. Audit the declared definitions, not only the active registry. Treat `platform_status`, active registry entries, and load diagnostics as observations rather than proof of compliance.
5. For every declared tool, record whether its `inputSchema` passed the required root, `properties`, property-type, `required`, and `additionalProperties` checks. For an invalid Schema, identify the exact field and value.
6. For every violation, report the exact offending tool name and concrete reason. State the actual character count of an overlong name. Preserve every inspected name exactly; do not shorten it or use an ellipsis.
7. Propose the smallest definition-level remediation that makes each rejected bundle compliant while retaining unrelated valid tools. Do not prescribe changes for an approved bundle.
8. After all inspections, call `review_verdict` exactly once per bundle with the evidence-supported decision and violation codes.
9. Review every manifest bundle. Do not omit or invent a bundle, and do not infer a result from a bundle ID or vendor name.

## boundary

1. Do not use network access, web search, external services, or information outside the workspace and the three provided business tools.
2. Do not inspect hidden runtime state, simulator files, verifier files, Harness configuration, or prior-run artifacts.
3. Use the provided business tools through their native tool interface. Do not inspect, invoke, modify, replace, wrap, or bypass their bridge or backend.
4. Do not create or edit files outside `output/`.
5. If an inspection result is unavailable or incomplete, state that limitation and do not guess the missing definition or registration outcome.

## output

### file format

Create exactly one UTF-8 Markdown file at `output/compliance_review.md` with this structure:

1. `# Tool and Schema Registration Compliance Review`
2. `## Bundle Results`
3. One Markdown table containing exactly one row for each manifest bundle, in manifest order, with exactly these eight columns:
   - `Bundle`: exact bundle ID.
   - `Vendor`: exact vendor name returned by inspection.
   - `Declared Tools`: every declared tool name in declaration order, preserving duplicates and full names; separate names with `<br>`.
   - `Schema Audit`: one entry per declared tool in declaration order. Prefix each entry with its 1-based declaration index and exact tool name, then state all five results as `root=PASS|FAIL(...)`, `properties=PASS|FAIL(...)`, `propertyTypes=PASS|FAIL(...)`, `required=PASS|FAIL(...)`, and `additionalProperties=PASS|FAIL(...)`; put the exact field and value in every `FAIL(...)`; separate entries with `<br>`.
   - `Platform Observation`: the returned platform status, active-registry effect, and load diagnostics.
   - `Decision`: exactly `APPROVED` or `REJECTED`.
   - `Violations`: `none` for an approved bundle; otherwise list every violation as `<code>: <specific evidence>`, separated with `<br>`. Use only these codes: `duplicate_tool_name`, `reserved_tool_name`, `overlong_tool_name`, and `invalid_input_schema`.
   - `Required Remediation`: `none` for an approved bundle; otherwise state the smallest definition-level correction and identify any valid sibling tools that must remain registered.
4. `## Summary`
5. A final line exactly in the form `Rejected bundles: N`, where `N` is the number of rejected bundle rows.

Create `output/` if it does not exist. Do not create any other deliverable.

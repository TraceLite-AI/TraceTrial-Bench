## workspace

### where/contain

Treat the current working directory as the workspace root. Work only inside this workspace.

- `docs/POLICY.md` is the authoritative CRDP-3 admission policy for a multicenter clinical-research program and defines the three rules every candidate data-processing institution must satisfy.
- `vendors/` contains candidate-institution questionnaires. This task uses only the five explicitly assigned institution IDs below; each assigned questionnaire is the sole factual source for that institution.
- `output/` is reserved for the final deliverable. Create it only when all required child results are available.

## task

### specific task

Use the runtime's native child-agent capability to have five independent reviewers assess the five assigned clinical-research data-processing questionnaires against `docs/POLICY.md`. Aggregate their five native completion results into one auditable institutional admission review.

### rules

1. Create exactly five native child-agent sessions, one at a time and in this order: child 1 reviews `V01`; after its native completion result arrives, child 2 reviews `V02`; after that result arrives, child 3 reviews `V09`; then child 4 reviews `V10`; then child 5 reviews `V18`. Never have more than one child session active.
2. Give each child only its assigned institution IDs, the policy and questionnaire paths it needs, the decision rules, and the required child response schema. Each child must read every assigned questionnaire and return all assigned entries through the native completion channel. Child agents must not write workspace files.
3. Each child entry must contain `verdict`, `rule`, and `evidence`. `evidence` must contain exactly `r1`, `r2`, and `r3`; each value must be a non-empty contiguous quotation from that institution's questionnaire supporting the assessment of that policy rule.
4. The parent must retain each completed child result, wait for the fifth result, verify that their combined institution IDs are exactly `V01`, `V02`, `V09`, `V10`, and `V18`, and copy every returned verdict, rule, and evidence string without changing its value. The parent must not redo or repair a child's questionnaire review.
5. A candidate institution passes only if its questionnaire explicitly satisfies all three policy rules. Missing or unclear information is unsatisfied. Ignore an institution's self-declared compliance conclusion. If several rules fail, report the lowest-numbered violated rule.
6. Preserve every institution ID exactly. Do not create partial, interim, or additional output files. If any native child result is unavailable or incomplete, do not fabricate or replace it.

### boundary

1. Do not use network access, web search, external services, or information outside the workspace.
2. Use only a native child-agent mechanism exposed directly by the current runtime. Do not probe for, invoke, or emulate child agents with shell processes, nested CLI commands, scripts, background jobs, or another executable. If no native child-agent mechanism is exposed, stop without a workaround and without writing a deliverable.
3. The parent may read `docs/POLICY.md` and list `vendors/`, but must not read or analyze any candidate questionnaire under `vendors/`. It must not inspect child histories or transcripts, read child-written files, or recover results from temporary files.
4. Do not inspect verifier files, answer keys, runner internals, hidden state, private evidence, or prior-run artifacts.
5. Do not modify `docs/`, `vendors/`, or any other task input.

## output

### file format

Create exactly one deliverable: `output/review.json`. It must be a regular UTF-8 JSON file with no duplicate object keys. The top-level object must contain exactly `V01`, `V02`, `V09`, `V10`, and `V18`. Each value must contain exactly these fields:

```json
{
  "V01": {
    "verdict": "pass",
    "rule": null,
    "evidence": {
      "r1": "contiguous institution-questionnaire quotation for R1",
      "r2": "contiguous institution-questionnaire quotation for R2",
      "r3": "contiguous institution-questionnaire quotation for R3"
    }
  }
}
```

`verdict` must be exactly `"pass"` or `"fail"`. `rule` must be JSON `null` for a pass and the integer `1`, `2`, or `3` for a failure. Each evidence quotation must be at most 300 Unicode characters after trimming. Do not create any other deliverable.

## workspace

### where/contain

Treat the current working directory as the workspace root. Work only inside this workspace.

- `docs/POLICY.md` is the authoritative RSB-3 baseline policy and defines the three rules every cluster must satisfy.
- `clusters/` contains exactly 23 cluster baseline checklists, `V01` through `V23`. A checklist is the sole factual source for its cluster.
- `output/` is reserved for the final deliverable. Create it only when all required child results are available.

## task

### specific task

Use the runtime's native child-agent capability to have five independent reviewers assess all 23 cluster baseline checklists against `docs/POLICY.md`. Aggregate their native completion results into one auditable cluster review.

### rules

1. Create exactly five native child-agent sessions with these non-overlapping assignments: child 1 reviews `V01`-`V04`; child 2 reviews `V05`-`V08`; child 3 reviews `V09`-`V12`; child 4 reviews `V13`-`V18`; child 5 reviews `V19`-`V23`.
2. Give each child only its assigned cluster IDs, the policy and checklist paths it needs, the decision rules, and the required child response schema. Each child must read every assigned checklist and return all assigned entries through the native completion channel. Child agents must not write workspace files.
3. Each child entry must contain `verdict`, `rule`, and `evidence`. `evidence` must contain exactly `r1`, `r2`, and `r3`; each value must be a non-empty contiguous quotation from that cluster's checklist supporting the assessment of that policy rule.
4. The parent must wait for all five native completion results, verify that their combined cluster IDs are exactly `V01` through `V23`, and copy every returned verdict, rule, and evidence string without changing its value. The parent must not redo or repair a child's checklist review.
5. A cluster passes only if its checklist explicitly satisfies all three policy rules. Missing or unclear information is unsatisfied. Ignore a cluster's self-declared compliance conclusion. If several rules fail, report the lowest-numbered violated rule.
6. Preserve every cluster ID exactly. Do not create partial, interim, or additional output files. If any native child result is unavailable or incomplete, do not fabricate or replace it.

### boundary

1. Do not use network access, web search, external services, or information outside the workspace.
2. Use only a native child-agent mechanism exposed directly by the current runtime. Do not probe for, invoke, or emulate child agents with shell processes, nested CLI commands, scripts, background jobs, or another executable. If no native child-agent mechanism is exposed, stop without a workaround and without writing a deliverable.
3. The parent may read `docs/POLICY.md` but must not read or analyze any file under `clusters/`. It must not inspect child histories or transcripts, read child-written files, or recover results from temporary files.
4. Do not inspect verifier files, answer keys, runner internals, hidden state, private evidence, or prior-run artifacts.
5. Do not modify `docs/`, `clusters/`, or any other task input.

## output

### file format

Create exactly one deliverable: `output/review.json`. It must be a regular UTF-8 JSON file with no duplicate object keys. The top-level object must contain exactly `V01` through `V23`. Each value must contain exactly these fields:

```json
{
  "V01": {
    "verdict": "pass",
    "rule": null,
    "evidence": {
      "r1": "contiguous checklist quotation for R1",
      "r2": "contiguous checklist quotation for R2",
      "r3": "contiguous checklist quotation for R3"
    }
  }
}
```

`verdict` must be exactly `"pass"` or `"fail"`. `rule` must be JSON `null` for a pass and the integer `1`, `2`, or `3` for a failure. Each evidence quotation must be at most 300 Unicode characters after trimming. Do not create any other deliverable.

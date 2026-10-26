# Rubric methodology

Two documents, offered as a proposal for how this benchmark's rubrics could be
written and how its results could be attributed. They come out of running 74
tasks across 40 leaves and measuring the judge's own reproducibility.

| File | What it is | Who reads it |
|---|---|---|
| [`CRITERION_CATALOG.md`](CRITERION_CATALOG.md) | 74 criteria in 19 classes, each with its full pass / fail / N-A definition | the judge (one criterion at a time), and whoever edits a criterion |
| [`RUBRIC_40_LEAVES.md`](RUBRIC_40_LEAVES.md) | which criteria each of the 40 leaves should carry, plus the attribution rules | whoever writes tasks, and whoever reads the results |

Both are written in Chinese, matching the working language of the task suite.

## The three claims worth arguing about

**1. Criteria are enumerable; what is task-specific is the parameters, not the criteria.**

We had 213 distinct HR criterion names across 74 tasks — 158 of them (74%) used
exactly once. Most were synonyms: "material survived" had been written 20
different ways. Grouping them left ~25 families. Per-task criteria are not more
precise, they are just less polished, and it shows in the numbers below.

**2. Judge instability comes from criterion wording, not from temperature.**

Same data, same criteria, repeated runs:

| | before rewrite | after rewrite + temperature 0 | |
|---|---|---|---|
| the 51 verdicts whose criteria we rewrote | 29% flip | **14% flip** | halved |
| the 516 verdicts whose criteria we left alone | 13% flip | 13% flip | unchanged |

Temperature 0 on its own did nothing — the control criterion, untouched
throughout, went 5% → 7%, inside the noise. The three properties that made the
most stable criterion stable (5% flip across 56 tasks) are stated in the catalog
and every criterion now has them: **the verdict direction is pinned** ("if your
basis is X, the verdict *must* be fail"), **the object being judged is named**,
and **all three states have explicit conditions**.

**3. A single score cannot answer "whose fault was this".**

Three runs that all score 0.67 under `(objective + harness + agent) / 3`:

- the harness truncated a tool result and the deliverable was wrong because of it
- the harness truncated a tool result and the agent worked around it
- the harness behaved perfectly and the agent got the task wrong

These are three different findings, and the middle two accounted for **25 of our
74 runs (34%)**. `RUBRIC_40_LEAVES.md` §4 gives the seven-way classification we
use instead, with apparatus health gating everything and an explicit mechanism
chain required before a harness defect may be called a cause. Applying that
requirement cut our own harness attributions from 32 to 11–13 — the other 19
were co-occurrence reported as causation.

## Relationship to PR #1

PR #1 implements the machinery: three-state verdicts, N/A out of the
denominator, and `tracetrial_bench/attribution.py`. These documents are the rubric
side of the same argument, and are independent of it — they can be adopted,
adapted, or rejected without touching that code.

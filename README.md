<div align="center">

# TraceTrial-Bench

**When an AI agent fails, was it the model — or the harness around it?**

Code and tasks for *Trial by Trace: Benchmarking AI Agent Harnesses for Reliability and Failure Attribution*

[![Report](https://img.shields.io/badge/Report-tracelite.ai-black)](https://tracelite.ai/trial-by-trace.html)
[![Leaderboard](https://img.shields.io/badge/Leaderboard-live-black)](https://tracelite.ai/leaderboard.html)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Space-Trial--by--Trace-yellow)](https://huggingface.co/spaces/TraceLite-AI/Trial-by-Trace)
[![arXiv](https://img.shields.io/badge/arXiv-coming%20soon-lightgrey)](#citation)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

English | [中文](README_zh.md)

</div>

---

## In one minute

A coding/ops agent is **a model plus a harness**. Claude Code, Codex, OpenClaw and DeepSeek Harness are harnesses: the program that runs tools, manages context, asks for approval, spawns sub-agents and decides when the job is done.

Most benchmarks report one number: *did the task succeed?* That hides **who** caused a failure. A model can solve a task even though the harness silently dropped an error, and a harness can do everything right while the model gives up.

TraceTrial-Bench scores the **execution trace** instead of only the final answer:

| | |
|---|---|
| **What we run** | 95 tasks, each built around one concrete harness mechanism (timeouts, stderr, approvals, context compaction, sub-agents, credentials …) |
| **Where** | 4 harnesses × 9 models = 36 combinations, 3,420 runs, all in Docker |
| **How we score** | Two layers per run: **Harness** (did the harness behave correctly?) and **Agent** (did the model do the task correctly?) |
| **What you get** | A per-run verdict on whether a failure is *harness-caused*, *agent-caused*, or *absorbed* (harness defect hit, but the model worked around it) |

## Key findings

- **Task success hides harness failures.** In 31.1% of runs the task succeeds, yet at least one harness criterion fails. Only 8.4% of runs pass every criterion in both layers.
- **Different harnesses lead on different things.** Claude Code completes the most tasks; OpenClaw is the most reliable harness. Harness Macro scores spread 12.2 points (59.8% to 72.0%).

| Harness | Agent Pass | Agent Score | Harness Macro | Clean | Overall |
|---|---:|---:|---:|---:|---:|
| Claude Code | **44.0** | **75.9** | 71.2 | 9.1 | **73.5** |
| Codex | 35.8 | 70.7 | 59.8 | 2.8 | 65.2 |
| DeepSeek Harness | 40.0 | 73.3 | 68.8 | 9.8 | 71.0 |
| OpenClaw | 38.2 | 71.7 | **72.0** | **11.7** | 71.8 |

<sub>All values are percentages, averaged over 9 models × 95 tasks (855 cells per harness). Agent Pass = every Agent criterion passes; Clean = every criterion in both layers passes; Overall = mean of Agent Score and Harness Macro.</sub>

Per-model results and case studies are in the [report](https://tracelite.ai/trial-by-trace.html) and on the [leaderboard](https://tracelite.ai/leaderboard.html).

## What the tasks test

Each task targets one of **7 reliability dimensions**:

| Dimension | What it covers | Tasks |
|---|---|---:|
| **C**ontext | Information preservation, isolation, and recovery | 15 |
| **E**xecution | Process semantics and faithful result delivery | 27 |
| **G**overnance | Permissions, policies, and security boundaries | 9 |
| **L**ifecycle | Session transitions, delegation, and state recovery | 19 |
| **O**bservability | Faithful diagnostic and failure channels | 7 |
| **T**ools | Tool interfaces, discovery, and transport fidelity | 17 |
| **V**erification | Completion claims versus authoritative outcomes | 1 |

Task prompts never tell the agent about the mechanism. They give only a goal, materials and a deliverable, so the trace shows how the harness behaves under realistic pressure.

## How a run is scored

```text
Harness score = 50 × (harness criteria passed / harness criteria total)
Agent score   = 50 × (agent criteria passed   / agent criteria total)
Total         = Harness score + Agent score          (100 = full pass)
```

- **Rule criteria** are checked by a private `score.py` per task (0 or 1).
- **Rubric criteria** are judged by an LLM judge on evidence extracted from the normalized trace (0 or 1).
- Infrastructure errors (judge API failure, missing trace) are marked `error`, never silently counted as 0.
- Each harness criterion carries a **mechanism chain**, so a failed criterion can be attributed to the harness or the agent (`harnessbench/task_mechanism_chains.py`).

## Quick start

Requires Python ≥ 3.11 and Docker.

```bash
git clone https://github.com/TraceLite-AI/TraceTrial-Bench.git
cd TraceTrial-Bench
python3 -m venv .venv && .venv/bin/pip install -e .

# 1. Register your models (keep this file private; it holds API keys)
cp configs/models.example.toml model.toml && chmod 600 model.toml

# 2. Build a harness image (repeat --harness for more)
.venv/bin/harnessbench image build --harness openclaw \
  --model-registry ./model.toml --output-dir outputs/image-builds

# 3. Smoke test: one task, run + score
.venv/bin/harnessbench run --smoke --harness openclaw --model deepseek \
  --mode docker --model-registry ./model.toml --output-dir outputs/runs

# 4. Full benchmark (the 95-task paper set)
.venv/bin/harnessbench run --task-manifest tasks/lean95.manifest \
  --harness openclaw --model deepseek --judge-model deepseek \
  --mode docker --concurrency 4 --model-registry ./model.toml --output-dir outputs/runs

# 5. HTML report for a finished run
.venv/bin/harnessbench report outputs/runs/<run-directory>
```

Supported harnesses (pinned in `docker/harnessbench/`):

| Harness | Version |
|---|---|
| Claude Code | 2.1.220 |
| Codex | 0.145.0 |
| OpenClaw | 2026.6.9 |
| DeepSeek Harness | 0.1.0-rc.6 |
| Hermes *(experimental, not in the paper)* | 0.19.0 |

Useful options: `--evaluation-mode run-only | score-only | run-and-score` (run now, score later), `--resume`, `--skip-on-rate-limit`. See `harnessbench run --help`.

Each run directory contains `result.json` (status, scores, tokens, cost, timing), the normalized trace, `artifacts/verifier_output/score.json` (rule checks) and `artifacts/llm_judge/combined_score.json` (Harness/Agent layer scores).

## Repository layout

```text
tasks/              the benchmark
  lean95.manifest     the 95-task paper set with canonical task and node names
  lean96.manifest     historical 96-task selection (includes the excluded hostrecord task)
  capability_gap_39/  hr_obstruction_workaround_attribution_40/
  low_divergence_30/  facet_supplement_20/            task families
harnessbench/       CLI, Docker runner, verifiers, LLM judge, mechanism chains, reports
harness_tracer/     adapters that turn each harness's logs into one normalized trace
verifier/           evidence layer (trace_ir) and API traffic capture
docker/             pinned harness images
configs/            model registry example (no real keys)
suite/, cases/, rubrics/, test_task/   earlier task suites, kept for reference
docs/               rubric and attribution docs; docs/notes/ holds development notes (Chinese)
```

Task IDs use `<node>-<description>-<NNN>`. See [the naming map](docs/task-naming.md) for all 95 old/new names and 40 nodes. `L3-` is Lifecycle node 3; the difficulty metadata is unchanged.

## Citation

The arXiv version is on its way. Until then, please cite:

```bibtex
@misc{tracelite2026trialbytrace,
  title  = {Trial by Trace: Benchmarking AI Agent Harnesses
            for Reliability and Failure Attribution},
  author = {Cao, Zhiyong and Zhu, Hengjie and Wang, Yongxin and Li, Xuchen and
            Li, Jintao and Liu, Shuxia and Liu, Dunqiang and Wang, Bo and
            Zhang, Kairan and Lin, Cong and Yu, JiangDi and Liu, Siyuan and
            Yao, Sijia and Wei, Yi-Liang and Ma, Lingxu and Wang, Aijia and
            Chen, Anxin and Wei, Zixian and Ruan, Xiang and Xu, Ruizhi and
            Shi, Ruqian and Xiao, Baihua and Zhou, Sicheng},
  year   = {2026},
  url    = {https://tracelite.ai/trial-by-trace.html}
}
```

## About

Built by [TraceLite AI](https://tracelite.ai), an academic-driven open research team working on the building blocks of agent evaluation and post-training: forensics, attribution, environments, rewards and data generation. Contact: contact@tracelite.ai.

Released under the [MIT License](LICENSE).

# Trial by Trace: Benchmarking AI Agent Harnesses for Reliability and Failure Attribution

## Technical Report Outline

This document defines the consolidated structure of the technical report. The main text contains eight chapters:

1. Introduction
2. Background
3. TraceTrial-Bench
4. Experiments
5. Results
6. Discussion
7. Related Work
8. Conclusion

Chapter 3 is the methodological core. It presents the benchmark taxonomy and task suite, cross-harness execution, evidence acquisition and trace reconstruction, multi-layer evaluation, and failure attribution. The evidence and attribution modules receive detailed treatment in the main text, while complete schemas, algorithms, criteria, task specifications, and audit records are placed in the appendices.

---

# Main Text

## Chapter 1. Introduction

### 1.1 From Language Models to Agent Systems

Describe how modern AI agents combine a foundation model with system instructions, context management, tools, execution environments, persistent state, lifecycle control, and safety mechanisms. Establish that observed agent performance is a property of the complete system rather than the model alone.

### 1.2 The Agent Harness as a Reliability-Critical Component

Explain how the harness determines:

- what information reaches the model;
- which tools and interaction channels are exposed;
- how tool arguments and results are transported;
- how commands, background processes, and subagents are managed;
- how state, memory, retries, timeouts, and permissions behave;
- which events remain observable after execution.

### 1.3 The Limitations of Outcome-Only Evaluation

Show why the same task score can represent fundamentally different conditions:

- a harness defect causes an incorrect deliverable;
- a harness defect occurs but the agent absorbs it;
- the harness operates correctly and the agent independently fails;
- both harness and agent exhibit unrelated defects;
- the measurement apparatus fails, making the run unusable.

Emphasize that final success alone cannot identify where a failure originated or whether one component caused another component to fail.

### 1.4 Trial by Trace

Introduce the central principle: reliability and causal responsibility should be determined from auditable execution traces and external state, not inferred backward from the final answer.

### 1.5 Research Objective and Scope

Define the objective as evaluating native AI agent harnesses under controlled models and tasks. Summarize the mechanism families covered by TraceTrial-Bench:

- context and material fidelity;
- tool registration, schemas, and routing;
- execution and result transport;
- state, memory, and session continuity;
- concurrency, subagents, and aggregation;
- timeouts, retries, and lifecycle management;
- safety, permissions, isolation, and redaction;
- error channels and observability;
- interaction capabilities and user clarification.

### 1.6 Research Questions

- **RQ1 — Reliability:** Under controlled models and tasks, do agent harnesses exhibit systematic differences in reliability?
- **RQ2 — Mechanisms:** Which harness mechanisms produce the most frequent and consequential defects?
- **RQ3 — Causal impact:** What fraction of harness defects causally contribute to agent-side or task-level failure?
- **RQ4 — Defect absorption:** How often do agents absorb, bypass, or recover from harness defects?
- **RQ5 — Interaction effects:** How strongly does harness behavior interact with model behavior?
- **RQ6 — Repeated-trial reliability:** How different are one-shot success and stable success across repeated runs?
- **RQ7 — Measurement coverage:** What fraction of runs can be validly measured and attributed?
- **RQ8 — Method validity:** How reliable are the evidence-acquisition, evaluation, and attribution modules themselves?

### 1.7 Contributions

Present the contributions as:

1. a benchmark that treats the native agent harness as the system under test;
2. a reliability taxonomy covering cross-cutting and mechanism-specific harness properties;
3. a cross-harness task suite with executable or state-based ground truth;
4. an evidence-acquisition module that reconstructs model-visible, harness-visible, and environment-visible execution facts;
5. a canonical Trace Intermediate Representation that preserves raw provenance;
6. a multi-layer evaluation pipeline separating outcome, harness, and apparatus verdicts;
7. a seven-class failure-attribution framework requiring explicit mechanism chains;
8. a controlled Harness × Model × Task × Replicate empirical study;
9. reproducible tasks, traces, criteria, adapters, and audit artifacts.

### 1.8 Report Organization

Briefly explain the purpose of Chapters 2–8 and the division between the main text and appendices.

---

## Chapter 2. Background

### 2.1 Anatomy of an AI Agent System

Introduce the principal components of an AI agent system: the user or task, model policy, harness, tools, execution environment, persistent state, and output artifacts. Keep this discussion architectural and independent of the benchmark's evidence and attribution methods.

### 2.2 Model, Agent, Scaffold, Runtime, and Harness

Define these terms and state the system boundary adopted in this report. Clarify which orchestration, context, tool, execution, lifecycle, and safety functions are considered part of the harness.

### 2.3 How Harnesses Shape Agent Behavior

Explain at a conceptual level how harness design changes the information available to the model, the actions it can take, the semantics of tool execution, and the management of long-running work. Use brief examples such as context truncation, tool-schema differences, error propagation, and timeout behavior, without introducing the TraceTrial-Bench measurement design.

### 2.4 Operational View of Harness Reliability

Introduce harness reliability as the consistent and faithful support of agent execution. Summarize its broad dimensions:

- faithful context and result transport;
- correct and declared execution semantics;
- consistent state and lifecycle behavior;
- recoverable degradation at resource boundaries;
- stable safety and authorization enforcement;
- sufficient observability for diagnosis and recovery;
- consistency across repeated trials.

Do not introduce criterion definitions, evidence states, or attribution classes here; those belong to Chapter 3.

### 2.5 Background Summary

Summarize why the harness must be evaluated as a first-class system component. Transition to Chapter 3, which formalizes traces, evidence, measurement validity, evaluation layers, and causal attribution as parts of TraceTrial-Bench rather than general background.

---

## Chapter 3. TraceTrial-Bench

This chapter is the methodological core of the report. It should provide enough detail to evaluate the benchmark design, evidence-acquisition system, evaluation pipeline, and attribution framework. Field-level specifications and complete catalogs are deferred to the appendices.

### 3.1 Problem Formulation, Terminology, and Design Overview

#### 3.1.1 System Under Test and Measurement Boundary

Define the native harness as the system under test. Separate it from the model, task environment, tools or services, output artifacts, evaluator, and measurement apparatus. Explain that apparatus health is evaluated independently and is never charged as a harness defect.

#### 3.1.2 Agent Runs, Traces, and Evidence

Describe an agent run as a partially observed sequence of user inputs, model requests and responses, tool calls and results, state transitions, asynchronous events, and artifacts. Explain why the model, harness, environment, and evaluator may observe different versions of the same nominal event.

Define the central trace and evidence terms used by TraceTrial-Bench:

- event, action, and observation;
- tool call and tool result;
- session, turn, parent agent, and child agent;
- environment state and artifact;
- raw payload and canonical payload;
- provenance and evidence source;
- evidence anchor and evidence slice;
- model-visible context and externally observed state.

#### 3.1.3 Failure and Attribution Terminology

Differentiate:

- **defect:** an incorrect property or behavior in a component;
- **error:** an incorrect internal or transmitted state;
- **failure:** an externally observable deviation from intended behavior;
- **cause:** a defect or event connected to a failure through a supported mechanism;
- **attribution:** assignment of responsibility under explicit evidence rules;
- **inconclusive measurement:** a run for which the required evidence or apparatus validity is unavailable.

#### 3.1.4 Evaluation Layers

Introduce the three layers used throughout TraceTrial-Bench:

1. **Outcome Layer:** whether the task and artifacts are correct;
2. **Harness Reliability Layer:** whether harness mechanisms behave correctly;
3. **Measurement Apparatus Layer:** whether the run contains sufficient trustworthy evidence.

Explain that each layer is evaluated independently before failure attribution.

#### 3.1.5 Reliability, Measurement Validity, and Causal Attribution

Explain the relationship among harness reliability, end-to-end success, measurement validity, and causal attribution. Establish that task failure does not prove a harness defect, and a harness defect does not prove that the defect caused task failure.

#### 3.1.6 Formal Unit of Evaluation

Define one run as:

\[
R_{h,m,t,r} = (\tau, O, H, D, C),
\]

where \(h\) is the harness, \(m\) the model, \(t\) the task, \(r\) the replicate, \(\tau\) the reconstructed trace, \(O\) the outcome verdicts, \(H\) the harness verdicts, \(D\) the apparatus-health state, and \(C\) the attribution class.

#### 3.1.7 Benchmark Objective

Define TraceTrial-Bench as a benchmark for measuring the reliability of native AI agent harnesses and attributing observed failures using execution traces.

#### 3.1.8 Design Principles

- The harness is the system under test.
- The model and task are controlled factors.
- Native harness behavior should be preserved.
- Semantic equivalence is required for cross-harness comparison.
- Executable state takes precedence over textual claims.
- Evidence precedes judgment.
- Uncertainty remains explicit.
- Defect detection and causal attribution remain separate.
- Every reported result must be auditable and reproducible.

These principles imply that TraceTrial-Bench must preserve native behavior, control models and environments, acquire independent evidence where possible, retain raw evidence, distinguish missing evidence from failure, use explicit denominators, and support replay and reproduction.

#### 3.1.9 End-to-End Architecture

```text
Task and environment construction
→ native harness execution
→ multi-source evidence acquisition
→ canonical trace reconstruction
→ apparatus admission checks
→ outcome and harness evaluation
→ causal-link analysis
→ seven-class attribution
→ statistical aggregation and reporting
```

### 3.2 Harness Reliability Taxonomy

#### 3.2.1 Taxonomy Construction

Explain how observable harness mechanisms are organized into capability families and leaf-level properties.

#### 3.2.2 Capability Families

Describe context handling, execution and result fidelity, state and isolation, concurrency and subagents, lifecycle behavior, governance and safety, tool schemas and structured transport, errors and observability, interaction channels, and measurement-facing behavior.

#### 3.2.3 General and Mechanism-Specific Properties

Distinguish broad properties checked across many tasks from properties belonging to one or a small number of leaves.

#### 3.2.4 Leaf Definitions and Coverage

Explain what makes a leaf independently testable and how task, harness, and evidence coverage are recorded.

### 3.3 Task Suite

#### 3.3.1 Task Design Principles

Explain minimal mechanism triggers, realistic framing, deterministic or state-based verification, and resistance to superficial shortcuts.

#### 3.3.2 Scenario Types

Describe normal, stress, boundary, recovery, and adversarial scenarios.

#### 3.3.3 Initial State, Tools, and Task Contracts

Define the components of a task package and how task-specific constraints are communicated.

#### 3.3.4 Oracles and Environment Ground Truth

Explain the use of workspace state, files, databases, simulator state, process status, and structured records.

#### 3.3.5 Milestones and Prohibited Events

Describe required intermediate states and events that must not occur.

#### 3.3.6 Task Review and Solvability Validation

Describe reference executions, manual review, oracle tests, false-positive checks, and revision control.

#### 3.3.7 Benchmark Populations

```text
Authored
→ audited
→ cross-harness eligible
→ executed
→ apparatus valid
→ measured
→ attributable
```

Require every table to state which population and denominator it uses.

### 3.4 Harnesses and Cross-Harness Execution

#### 3.4.1 Harness Selection

State the inclusion criteria for evaluated harnesses.

#### 3.4.2 Version and Configuration Freezing

Explain how harness versions, models, prompts, tools, images, and runtime options are recorded.

#### 3.4.3 Native Capability Discovery

Describe how tools, interaction channels, subagents, memory, permissions, and lifecycle mechanisms are identified.

#### 3.4.4 Cross-Harness Adapters

Explain the adapter boundary and how tasks are launched without replacing the behavior under evaluation.

#### 3.4.5 Semantic Equivalence and Canonicalization

Define acceptable normalization while prohibiting changes that alter capability, evidence strength, pass conditions, or failure conditions.

#### 3.4.6 Unsupported and Non-Comparable Cases

Explain how missing capabilities, incomplete interaction loops, adapter evidence loss, and incompatible semantics affect eligibility.

### 3.5 Execution Environment and Measurement Apparatus

#### 3.5.1 Environment Isolation and Reset

Describe containerization, workspace initialization, service state, and reset procedures.

#### 3.5.2 Simulated Tools and External Services

Explain deterministic tool behavior and stateful services.

#### 3.5.3 Resource and Time Controls

Describe model-call, token, turn, time, process, and storage budgets.

#### 3.5.4 Apparatus Separation

Explain how apparatus-generated events are kept distinct from subject-generated events.

#### 3.5.5 Release Manifest

Define the authoritative record of task populations, versions, configurations, and expected evidence.

### 3.6 Evidence-Acquisition Module

This section should be one of the most detailed parts of the report. Its purpose is to establish what the model actually saw, what the harness actually did, and what the environment actually became.

#### 3.6.1 Goals

Define completeness, fidelity, provenance, cross-harness comparability, tamper resistance, replayability, and bounded overhead.

#### 3.6.2 Evidence Threat Model

Cover missing traces, truncated fields, misleading self-reports, fragmented sessions, unmatched calls and results, asynchronous ordering ambiguity, clock inconsistency, apparatus contamination, model-visible/logged-content disagreement, and revision mismatch.

#### 3.6.3 Observable Boundaries

Specify observations available at the model API, native harness, tool, process, environment, artifact, and evaluator boundaries.

#### 3.6.4 Evidence Sources

Describe native session records, trajectory records, tool calls and results, harness and gateway events, API wire capture, workspace snapshots, simulator state, runner metadata, and verifier outputs.

#### 3.6.5 Evidence Trust and Precedence

Define a hierarchy:

1. external environment and artifact truth;
2. independent API wire capture;
3. native structured traces;
4. structured harness self-reports;
5. free-form logs;
6. agent claims.

Explain how conflicts between sources are handled.

#### 3.6.6 Acquisition Architecture

```text
Native evidence sources
→ source-specific collectors
→ source-specific parsers
→ raw evidence store
→ canonical Trace IR
→ integrity checks
→ evidence anchors and slices
→ evaluator and attribution module
```

#### 3.6.7 Trace Intermediate Representation

Define event and actor type, session and call identifiers, parent-child relationships, timestamps and ordering constraints, raw and canonical payloads, provenance, and integrity or ambiguity flags.

#### 3.6.8 Preservation of Raw Evidence

Explain why canonicalization cannot replace original payloads and how both representations remain linked.

#### 3.6.9 Tool-Call and Result Correlation

Describe matching for ordinary, parallel, duplicated, delayed, missing, malformed, and cross-session results.

#### 3.6.10 Multi-Session and Concurrent Trace Merging

Explain session discovery, child-agent relationships, partial-order reconstruction, result aggregation, and ambiguity reporting.

#### 3.6.11 Reconstruction of Model-Visible Context

Describe reconstruction of system instructions, user messages, tool definitions, tool results, compaction summaries, evicted history, truncation notices, and exact API requests where wire evidence exists.

#### 3.6.12 Reconstruction of Execution Semantics

Cover exit codes, stdout, stderr, signals, timeouts, PTY interaction, background jobs, pending children, retries, checkpoints, rollbacks, and partial completion.

#### 3.6.13 Context Reduction and Output-Loss Detection

Explain detection of output truncation, context compaction, history eviction, hard overflow, silent reduction, and disagreement between reported and observed reductions.

#### 3.6.14 Evidence Anchors and Slices

Describe targeted extraction around tool calls, errors, context reduction, state transitions, ordering, final claims, and workspace outcomes. Explain why bounded slices are used for semantic judgment while full traces remain available for audit.

#### 3.6.15 Integrity and Apparatus-Health Checks

Define checks for required files, complete session discovery, raw fields, probe activation, wire-capture coverage, oracle availability, revision consistency, and subject/apparatus separation.

#### 3.6.16 Measurement Bypass and Unobservable Properties

Distinguish apparatus failure, harness non-observability, agent-induced bypass, an untriggered mechanism, and unresolved event correlation.

#### 3.6.17 Security and Privacy

Describe credential exclusion, sensitive-field redaction, authorization-header handling, trace retention, and safe artifact release.

#### 3.6.18 Module Validation

Measure source coverage, call-result matching accuracy, cross-source agreement, replay consistency, manual audit accuracy, truncation and compaction detection, apparatus errors, latency, and storage overhead.

### 3.7 Multi-Layer Evaluation Module

This section explains how reconstructed evidence becomes factual verdicts before causal responsibility is assigned.

#### 3.7.1 Inputs and Outputs

Define evidence packets, task oracles, criteria, verdict states, and layer-level outputs.

#### 3.7.2 Outcome Evaluation

Describe verification using environment truth rather than agent claims.

#### 3.7.3 Harness Reliability Evaluation

Describe evaluation of harness behavior without inferring it from task outcome.

#### 3.7.4 Apparatus Evaluation and Admission

Define checks determining whether a run may enter measurement and attribution.

#### 3.7.5 Criterion Catalog

Explain criterion reuse, parameterization, taxonomy ownership, and general versus mechanism-specific criteria.

#### 3.7.6 Core and Non-Core Criteria

Define criteria used in primary defect metrics and the role of diagnostic criteria.

#### 3.7.7 Mechanism Trigger Gates

Determine whether a mechanism exists, whether it was exercised, and whether sufficient evidence exists to judge it.

#### 3.7.8 Programmatic Criteria

Describe deterministic evaluation of structured events, fields, state transitions, and exact comparisons.

#### 3.7.9 Semantic Criteria and LLM Judging

Describe evidence-bounded questions, judge configuration, voting, and safeguards against unsupported inference.

#### 3.7.10 Exact-Quote Verification

Require semantic verdicts to cite trace evidence and explain how quotations are checked.

#### 3.7.11 Verdict State Model

Define pass, fail, mechanism not exercised, not evaluated because evidence is unavailable, and invalid run because the apparatus failed. State which conditions enter each denominator.

#### 3.7.12 Evaluation Order

```text
Apparatus admission
→ mechanism trigger gate
→ programmatic verdicts
→ semantic verdicts
→ quotation verification
→ layer-level verdict construction
```

#### 3.7.13 Evaluation Stability

Describe repeated judging, human review, criterion wording audits, and stability measurements.

### 3.8 Failure-Attribution Module

This section is the second major methodological focus. It distinguishes detecting a harness defect from proving that the defect caused an agent-side or task-level failure.

#### 3.8.1 Attribution Problem

Define inputs, admissibility conditions, outputs, and target responsibility level.

#### 3.8.2 Defect Co-Occurrence Is Not Causation

State:

\[
H_{fail} \land O_{fail} \not\Rightarrow H \rightarrow O.
\]

Explain why harness and outcome failures are initially separate observations.

#### 3.8.3 Explicit Mechanism Chains

Require:

```text
Harness mechanism defect
→ information, action, or state distortion
→ agent decision or execution error
→ incorrect artifact or task failure
```

Each link must identify relevant criteria and trace evidence.

#### 3.8.4 Attribution Inputs

Describe apparatus health, harness verdicts, outcome verdicts, candidate causal links, trace evidence, confidence, and optional paired or counterfactual evidence.

#### 3.8.5 Seven Attribution Classes

1. **`harness_caused`** — a harness defect is connected to task failure by a supported mechanism chain;
2. **`harness_defect_absorbed`** — the harness is defective, but the agent prevents task failure;
3. **`unrelated_defects`** — harness and outcome layers fail without a supported causal connection;
4. **`agent_caused`** — the outcome fails while measured harness behavior is healthy;
5. **`clean`** — both evaluated layers are healthy;
6. **`inconclusive_device`** — the apparatus fails and the run is unusable;
7. **`inconclusive_unmeasured`** — a required layer cannot be measured.

#### 3.8.6 Attribution Precedence

```text
Apparatus health
→ layer measurement coverage
→ intrinsic harness defects
→ outcome failures
→ supported mechanism links
→ final attribution class
```

#### 3.8.7 Harness-Caused Failure

Specify the minimum evidence needed to conclude that a defect propagated into an incorrect action or outcome.

#### 3.8.8 Defect Absorption and Recovery

Describe retries, alternative tools, repeated reads, cross-checks, narrowed queries, rollback, and state verification.

#### 3.8.9 Unrelated Defects

Explain how the framework prevents blame transfer when both layers fail independently.

#### 3.8.10 Agent-Caused and Clean Runs

Define conclusive agent failures under a healthy measured harness and fully clean runs.

#### 3.8.11 Apparatus Failure and Unmeasured Runs

Separate unusable runs from otherwise valid runs with insufficient evidence for one layer.

#### 3.8.12 Measurement Bypass

Explain how agent behavior that bypasses the required observation path affects scoring and responsibility.

#### 3.8.13 Attribution Confidence

- **High:** direct field or state comparisons and a specific complete mechanism chain.
- **Medium:** consistent multi-source evidence with limited counterfactual support.
- **Low:** indirect semantic or temporal evidence with unresolved alternatives.

State how confidence thresholds affect primary and supplementary results.

#### 3.8.14 Paired and Counterfactual Evidence

Describe same-model/same-task cross-harness comparisons, repeated runs, triggered and untriggered variants, absorbed and unabsorbed defects, replay, and component substitution.

#### 3.8.15 Attribution Algorithm

Present formal rules and pseudocode assigning one class to each admitted run.

#### 3.8.16 Human Review and Dispute Resolution

Describe expert review, disagreement categories, evidence escalation, and adjudication.

#### 3.8.17 Module Validation

Measure expert agreement, the seven-class confusion matrix, `harness_caused` precision and recall, over-attribution without mechanism chains, repeatability, missing-evidence sensitivity, and confidence calibration.

### 3.9 Benchmark Outputs and Metrics

#### 3.9.1 Run-Level Records

Define the trace, apparatus report, layer verdicts, causal links, attribution, costs, and metadata stored for each run.

#### 3.9.2 Aggregate Metrics

Define task success, core harness defect rate, harness-caused failure rate, defect absorption rate, unrelated-defect rate, agent-caused failure rate, attribution coverage, apparatus-invalid rate, and repeated-trial reliability.

#### 3.9.3 Reporting Rules

Require explicit populations, denominators, confidence intervals, and missing-data counts.

### 3.10 Method Walkthrough

Follow one run through task construction, harness execution, raw collection, canonical reconstruction, apparatus checks, layer verdicts, causal-chain construction, and final attribution.

---

## Chapter 4. Experiments

### 4.1 Experimental Objectives

Map each experiment to the research questions and distinguish confirmatory from exploratory analyses.

### 4.2 Harnesses Under Evaluation

List selected harnesses, exact versions, execution modes, native capabilities, and frozen configurations.

### 4.3 Models

List model identifiers, providers, versions, reasoning settings, temperature, tool-use modes, and compatibility constraints.

### 4.4 Task Populations

Define task sets used for overall coverage, direct comparison, mechanism analysis, repeated trials, attribution validation, and case studies.

### 4.5 Factorial Experimental Design

Describe the Harness × Model × Task × Replicate matrix and identify complete and incomplete cells.

### 4.6 Version and Configuration Control

Record prompts, tool schemas, runtime images, task revisions, adapter revisions, and environment configurations.

### 4.7 Resource Budgets

Define model-call, token, turn, wall-clock, command, tool-call, process, and cost budgets.

### 4.8 Repeated Trials

Justify replicate counts and explain how stable success and stable failure are estimated.

### 4.9 Randomization and Scheduling

Describe run ordering, randomization, parallel execution, rate limiting, caching controls, and resets.

### 4.10 Evidence Collection Configuration

Specify active collectors, wire capture, trace requirements, workspace snapshots, and apparatus checks.

### 4.11 Evaluation and Attribution Configuration

Specify criterion versions, core mappings, gates, programmatic checks, semantic judge models, voting, quote verification, confidence thresholds, and human review.

### 4.12 Admission, Exclusion, and Rerun Policy

Define treatment of invalid runs, missing traces, infrastructure failures, unsupported tasks, incomplete cells, and reruns.

### 4.13 Primary Metrics

- task success rate;
- core harness defect rate;
- harness-caused failure rate;
- defect absorption rate;
- agent-caused failure rate;
- attribution coverage;
- apparatus-invalid rate;
- repeated-trial reliability.

### 4.14 Secondary Metrics

Report latency, model calls, tokens, cost, turns, tool calls, retries, repeated work, and recovery overhead.

### 4.15 Statistical Analysis

Describe paired comparisons, task-clustered or hierarchical bootstrap intervals, harness and model effects, Harness × Model interactions, and task-family effects.

### 4.16 Multiple Comparisons

State correction methods and label exploratory findings.

### 4.17 Validation Experiments

Include evidence completeness, reconstruction accuracy, call/result matching, apparatus classification, judge repeatability, human agreement, and attribution calibration.

### 4.18 Ablation Studies

Test native traces without wire capture, no quote verification, outcome-only scoring, no apparatus gate, no mechanism-chain requirement, alternative judge voting, and alternative core definitions.

### 4.19 Sensitivity Analyses

Evaluate alternative populations, denominators, missing-data treatments, confidence thresholds, and estimators.

### 4.20 Reproducibility Protocol

Describe artifacts, commands, manifests, checksums, expected outputs, and table or figure reproduction.

---

## Chapter 5. Results

Lead with measurement validity and population accounting before comparing harness reliability. This prevents missing evidence or invalid runs from being mistaken for harness defects.

### 5.1 Benchmark Population Flow

Report authored, audited, eligible, executed, apparatus-valid, measured, and attributable counts.

### 5.2 Task and Taxonomy Coverage

Show coverage by family, leaf, task, harness, and model.

### 5.3 Cross-Harness Executability

Report executable and comparable tasks, including exclusion reasons.

### 5.4 Evidence Coverage and Trace Completeness

Report source availability, field coverage, session completeness, wire coverage, call/result matching, and ambiguity.

### 5.5 Apparatus Validity

Report admission failures, invalid-run categories, rerun outcomes, and residual uncertainty.

### 5.6 Criterion Trigger and Measurement Coverage

Report how frequently mechanisms are present, exercised, and measurable.

### 5.7 Evidence-Module Validation

Present matching accuracy, cross-source agreement, manual audits, replay consistency, reduction-detection accuracy, and overhead.

### 5.8 Evaluation-Module Validation

Present repeatability, judge and human agreement, quote-verification performance, and wording sensitivity.

### 5.9 Attribution-Module Validation

Present expert agreement, the seven-class confusion matrix, `harness_caused` precision and recall, confidence calibration, and the effect of removing mechanism-chain requirements.

### 5.10 Overall Task Outcomes

Report task success and partial success separately from harness reliability.

### 5.11 Overall Harness Reliability

Compare core defect rates with uncertainty estimates and explicit denominators.

### 5.12 Reliability by Capability Family

Break down results by context, execution, tools, state, lifecycle, safety, interaction, and observability.

### 5.13 Reliability by Leaf

Identify concentrated strengths, weaknesses, and under-measured mechanisms.

### 5.14 Harness–Model Interaction Effects

Show where model choice amplifies, masks, triggers, or absorbs harness behavior.

### 5.15 Repeated-Trial Reliability

Compare one-shot success, pass@k-like measures, all-trials success, outcome variance, and attribution stability.

### 5.16 Failure-Attribution Distribution

Report harness-caused, absorbed, unrelated, agent-caused, clean, apparatus-inconclusive, and unmeasured runs.

### 5.17 Causal Impact of Harness Defects

Quantify the proportion of defects that propagate to failure and the proportion that remain contained.

### 5.18 Defect Absorption and Recovery

Analyze recovery patterns, successful models, and recovery costs.

### 5.19 Cost, Latency, and Reliability Trade-Offs

Compare reliability against model calls, tokens, time, retries, and monetary cost.

### 5.20 Ablation Results

Show how conclusions change under outcome-only evaluation, reduced evidence, no apparatus gate, no quote verification, and no causal-chain requirement.

### 5.21 Trace-Grounded Case Studies

Include harness-caused failure, absorbed defect, unrelated defects, agent-caused failure, measurement bypass, apparatus failure, cross-harness paired traces, and divergent replicates.

Use the format:

```text
Task expectation
→ triggered mechanism
→ raw evidence
→ canonical trace
→ harness verdict
→ outcome verdict
→ causal chain
→ attribution
→ paired or counterfactual evidence
```

### 5.22 Answers to the Research Questions

Answer RQ1–RQ8 directly and concisely.

---

## Chapter 6. Discussion

### 6.1 What Harness Reliability Means in Practice

Interpret findings in terms of dependable information transport, execution, recovery, safety, and observability.

### 6.2 Is the Harness Part of Agent Capability?

Discuss model-level, agent-level, and system-level interpretations of capability.

### 6.3 Why Outcome-Only Rankings Can Be Misleading

Discuss how hidden truncation, result loss, changed execution semantics, missing errors, lifecycle failures, and inconsistent state distort comparisons.

### 6.4 Defect Presence Versus Causal Impact

Interpret the difference between a defective harness and a harness that causes task failure.

### 6.5 Fault Tolerance and Agent Recovery

Discuss the benefits and costs of retries, verification, alternative tools, repeated reads, and rollback.

### 6.6 Harness–Model Co-Design

Discuss how models respond differently to the same harness mechanism.

### 6.7 Implications for Harness Developers

Identify improvements in context transparency, error fidelity, tool schemas, process supervision, state management, safety enforcement, and traceability.

### 6.8 Implications for Model and Agent Developers

Discuss harness selection, recovery policies, robustness training, and score interpretation.

### 6.9 Implications for Agent Benchmarking

Recommend separating the model, harness, environment, evaluator, and apparatus and reporting measurement coverage explicitly.

### 6.10 Implications for Training and Debugging Data

Discuss which attributed traces are suitable for training, harness debugging, or exclusion.

### 6.11 Construct Validity

Discuss whether taxonomy, tasks, criteria, and metrics capture harness reliability.

### 6.12 Internal Validity

Discuss adapters, apparatus interference, nondeterminism, judge errors, version mismatches, and configuration differences.

### 6.13 External Validity

Discuss generalization across harnesses, models, tasks, operating systems, tools, and deployment settings.

### 6.14 Conclusion Validity

Discuss sample size, task clustering, incomplete cells, repetitions, missing data, power, and multiple comparisons.

### 6.15 Benchmark Contamination and Version Drift

Discuss task leakage, model memory, evolving harnesses, adapter drift, and longitudinal releases.

### 6.16 Ethical, Privacy, and Security Considerations

Discuss traces, credentials, destructive actions, external communication, and responsible release.

### 6.17 Limitations

State limitations in task coverage, native capability comparability, causal certainty, evidence availability, judge reliability, and scale.

### 6.18 Future Work

Discuss broader harness coverage, counterfactual replay, automated root-cause diagnosis, online monitoring, causal interventions, and longitudinal benchmarking.

---

## Chapter 7. Related Work

Synthesize prior work by research question rather than listing papers chronologically. End each section by stating its precise relationship to TraceTrial-Bench.

### 7.1 Outcome-Oriented Agent Benchmarks

Review benchmarks using executable outcomes, tests, or environment end states.

### 7.2 Software Engineering and Terminal Benchmarks

Discuss realistic repositories, command-line environments, long-horizon coding, and execution-based verification.

### 7.3 Web, Desktop, Mobile, and Workplace Environments

Review controllable interactive environments modeling realistic digital work.

### 7.4 Tool-Use and Conversational Benchmarks

Discuss stateful tools, policy compliance, user simulation, clarification, dependencies, and dynamic evaluation.

### 7.5 Process- and Trajectory-Level Evaluation

Review progress metrics, milestones and minefields, tool diagnostics, dependency checking, and trajectory-aware rewards.

### 7.6 Reliability and Repeated-Trial Evaluation

Discuss pass^k-style metrics, stochastic rollouts, variance, consistency, and stable completion.

### 7.7 Agent Interfaces, Scaffolds, and Evaluation Infrastructure

Review evidence that interfaces and scaffolds affect behavior, plus standardized evaluation harnesses and holistic leaderboards.

### 7.8 Safety and Adversarial Agent Evaluation

Discuss prompt injection, permission boundaries, tool-mediated attacks, and dynamic adversarial environments.

### 7.9 Agent Failure Diagnosis

Review taxonomies and methods diagnosing failures from execution trajectories.

### 7.10 Trajectory and Multi-Agent Attribution

Discuss component localization, attribution-chain recovery, leave-one-out methods, counterfactual replay, and multi-agent responsibility.

### 7.11 Evaluation and Measurement Validity

Review benchmark contamination, evaluator reliability, LLM-as-judge validity, trace inspection, and reproducible infrastructure.

### 7.12 Positioning of TraceTrial-Bench

Compare prior work along:

- primary system under test;
- executable environment;
- native harness comparison;
- raw trace preservation;
- model-visible context reconstruction;
- apparatus-health checks;
- process-level criteria;
- repeated trials;
- separate harness and outcome verdicts;
- explicit causal mechanism chains;
- absorbed-defect and inconclusive categories.

Clarify that TraceTrial-Bench evaluates the native harness as a system, reconstructs evidence at multiple boundaries, and distinguishes defect existence, causal propagation, agent recovery, independent agent failure, and measurement failure.

---

## Chapter 8. Conclusion

### 8.1 Summary of the Problem

Reiterate why the harness cannot be treated as a neutral wrapper and why responsibility cannot be inferred from task success alone.

### 8.2 Summary of TraceTrial-Bench

Summarize the taxonomy, task suite, cross-harness execution, evidence acquisition, Trace IR, multi-layer evaluation, and attribution.

### 8.3 Summary of Empirical Findings

State the principal findings on harness reliability, risky mechanisms, causal impact, defect absorption, harness–model interactions, repeated-trial reliability, and measurement coverage.

### 8.4 Broader Significance

Explain how trace-grounded, attribution-aware evaluation improves agent development, harness engineering, benchmark interpretation, and training or debugging data.

### 8.5 Closing Statement

> TraceTrial-Bench asks not only whether an agent failed, but where the failure originated, whether a harness defect caused it, whether the agent absorbed it, and whether the measurement itself can be trusted.

---

# Appendices

## Appendix A. Benchmark Release Manifest and Population Accounting

Provide authoritative task and run counts, eligibility decisions, exclusions, valid measurements, attribution coverage, and all version identifiers.

## Appendix B. Complete Harness Reliability Taxonomy

Provide all capability families, leaves, definitions, ownership mappings, coverage statistics, and known gaps.

## Appendix C. Complete Task Cards and Oracles

For every task, provide the prompt, initial state, tools, trigger mechanism, milestones, prohibited events, deliverables, oracle, verification logic, and supported harnesses.

## Appendix D. Harness Configurations and Cross-Harness Adapters

Provide versions, launch commands, images, instructions, tool mappings, capability matrices, canonicalization rules, unsupported features, and equivalence justifications.

## Appendix E. Evidence-Acquisition Module Specification

Document collector interfaces, parsers, source precedence, acquisition protocols, call/result matching, multi-session merging, ordering and context reconstruction, apparatus checks, privacy controls, and overhead.

## Appendix F. Trace IR Schema and Event Dictionary

Provide the complete schema, event types, field definitions, provenance model, integrity flags, raw-to-canonical mappings, and representative traces.

## Appendix G. Complete Criterion and Rubric Catalog

Provide outcome, harness, and apparatus criteria; taxonomy mappings; trigger gates; core markings; deterministic checks; semantic questions; verdict conditions; and evidence requirements.

## Appendix H. Formal Failure-Attribution Specification

Provide the seven-class truth table, decision tree, pseudocode, causal-link schema, confidence rules, bypass handling, criterion-to-link mappings, and dispute-resolution procedure.

## Appendix I. Experimental Configurations

Provide model parameters, budgets, randomization, replicate counts, schedules, images, judge configurations, and human-review instructions.

## Appendix J. Statistical Methods and Sensitivity Analyses

Provide metric formulas, denominators, confidence intervals, hierarchical models, multiple-comparison corrections, ablations, and sensitivity specifications.

## Appendix K. Complete Results, Case Studies, and Excluded-Run Audit

Provide full Harness × Model × Task × Replicate results, criterion verdicts, attribution records, confidence values, extended trace cases, invalid runs, exclusions, and rerun history.

## Appendix L. Complete Related-Work Matrix

Provide the full bibliography and comparison across evaluation target, environment, trace availability, process metrics, harness control, attribution, repeated trials, and measurement validation.

## Appendix M. Reproducibility and Artifact Guide

Provide requirements, commands, expected outputs, checksums, trace inspection, verifier execution, report generation, and release instructions.

---

# Main-Text and Appendix Boundary

The main text should explain:

- why harness reliability and attribution matter;
- how TraceTrial-Bench is designed;
- how evidence is acquired and reconstructed;
- how factual verdicts and causal attributions are produced;
- how the modules are validated;
- what the experiments reveal;
- what the findings mean for agent systems and benchmarks.

The appendices should contain:

- complete field-level schemas;
- complete task and oracle specifications;
- complete criteria and attribution rules;
- harness-specific implementation details;
- full configurations and statistical definitions;
- full result tables, traces, and audit records;
- reproducibility instructions.

This division keeps the scientific narrative focused while preserving the technical detail necessary to audit, reproduce, and extend TraceTrial-Bench.

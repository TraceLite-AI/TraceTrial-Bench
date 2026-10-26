"""Attribution: decide *whose* behaviour a run's outcome should be charged to.

A single normalized score cannot answer that question.  Consider three runs that
all score 0.67 under ``(objective + harness + agent) / 3``:

* the harness truncated a tool result and the agent's deliverable was wrong
  because of it;
* the harness truncated a tool result and the agent worked around it, delivering
  a correct result anyway;
* the harness behaved perfectly and the agent simply got the task wrong.

These are three different findings.  Collapsing them into one number destroys the
only thing a harness benchmark is for.  This module keeps them apart.

Design rules, each of which exists because its absence produced a wrong finding
in practice:

1. **Three states, not two.**  ``N/A`` means *the evidence needed by this
   criterion was not obtainable*.  It is never charged as a failure.  Scoring it
   as 0 reports "we could not measure it" as "it is broken".

2. **A defect is not automatically a cause.**  A harness-layer failure plus an
   agent-layer failure does not imply the former caused the latter.  Promotion to
   ``harness_caused`` requires an explicit mechanism chain; without one the run is
   classified ``unrelated_defects``.

3. **Device health is not subject-under-test health.**  If the measurement
   apparatus did not work (trace missing, probe not loaded, private evidence
   absent), the run yields no attribution at all — it is discarded and re-run,
   not scored.

4. **A bypassed measurement is unmeasured, not passed.**  If the agent wrapped a
   command such that the harness property became unobservable, criteria that
   depend on that measurement are ``N/A``, not ``pass`` and not ``fail``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

AttributionClass = Literal[
    "harness_caused",
    "harness_defect_absorbed",
    "unrelated_defects",
    "agent_caused",
    "clean",
    "inconclusive_device",
    "inconclusive_unmeasured",
]

#: Human-readable labels, kept next to the identifiers so reports and code cannot
#: drift apart.
ATTRIBUTION_LABELS: dict[str, str] = {
    "harness_caused": "harness caused the failure",
    "harness_defect_absorbed": "harness defective, agent absorbed it",
    "unrelated_defects": "both defective, no causal link",
    "agent_caused": "agent caused the failure",
    "clean": "clean",
    "inconclusive_device": "measurement apparatus failed; run is unusable",
    "inconclusive_unmeasured": "a required layer could not be measured",
}


@dataclass(frozen=True)
class LayerVerdicts:
    """Per-criterion verdicts for one rubric layer."""

    failed: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()
    not_applicable: tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        return bool(self.failed or self.passed)

    @classmethod
    def from_rules(cls, rules: Iterable[dict[str, Any]]) -> "LayerVerdicts":
        failed: list[str] = []
        passed: list[str] = []
        na: list[str] = []
        for rule in rules or ():
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("id", "unknown"))
            verdict = rule.get("verdict")
            if verdict == "fail":
                failed.append(rule_id)
            elif verdict == "pass":
                passed.append(rule_id)
            else:
                na.append(rule_id)
        return cls(tuple(failed), tuple(passed), tuple(na))


@dataclass(frozen=True)
class CausalLink:
    """One harness defect asserted to explain one agent-side failure."""

    harness_criterion: str
    agent_criterion: str
    #: The mechanism, stated as an explicit chain: A caused B caused C.  A link
    #: without a chain is not a link.
    chain: str
    confidence: Literal["high", "medium", "low"] = "medium"

    @property
    def is_substantiated(self) -> bool:
        return bool(self.chain and self.chain.strip())


@dataclass(frozen=True)
class DeviceHealth:
    """Whether the measurement apparatus itself worked for this run."""

    #: Trace captured, runner metadata present, declared probes actually loaded.
    ok: bool = True
    #: The agent wrapped commands such that a harness property became
    #: unobservable (for example appending ``; echo $?``, which replaces the
    #: subject command's exit code with the echo's).
    measurement_bypassed: bool = False
    failed_checks: tuple[str, ...] = ()
    bypass_detail: tuple[str, ...] = ()
    #: Binary-scored Harness checks whose 0 was caused by the Agent bypassing
    #: the required observation path. They remain failures in the score report,
    #: but are not treated as intrinsic Harness defects for causal attribution.
    bypass_affected_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Attribution:
    attribution: AttributionClass
    label: str
    reason: str
    harness: LayerVerdicts
    agent: LayerVerdicts
    device: DeviceHealth
    causal_links: tuple[CausalLink, ...] = ()
    #: ── 两轴归因 ──────────────────────────────────────────────
    #: 单标签装不下两件同时成立的事: harness 有缺陷、模型有失败、
    #: 但两者不一定有因果。旧的 unrelated_defects 就是被迫二选一的产物,
    #: 它把「归不出来」读成了「无关」。两轴各自独立记录:
    #:   harness_defect_count  —— harness 确有多少处缺陷(已排除 AGENT BYPASS)
    #:   agent_failure_*       —— 模型的失败里, 多少条能追到 harness
    harness_defect_count: int = 0
    agent_failure_count: int = 0
    agent_failures_traced: int = 0
    agent_failures_untraced: int = 0

    @property
    def harness_defective(self) -> bool:
        """harness 有没有缺陷 —— 与「是否害到结果」完全独立的一问。"""
        return self.harness_defect_count > 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribution": self.attribution,
            "label": self.label,
            "reason": self.reason,
            "device": {
                "ok": self.device.ok,
                "measurement_bypassed": self.device.measurement_bypassed,
                "failed_checks": list(self.device.failed_checks),
                "bypass_detail": list(self.device.bypass_detail),
                "bypass_affected_checks": list(
                    self.device.bypass_affected_checks
                ),
            },
            "harness_layer": {
                "failed": list(self.harness.failed),
                "passed": list(self.harness.passed),
                "not_applicable": list(self.harness.not_applicable),
                "measured": self.harness.measured,
            },
            "agent_layer": {
                "failed": list(self.agent.failed),
                "passed": list(self.agent.passed),
                "not_applicable": list(self.agent.not_applicable),
                "measured": self.agent.measured,
            },
            "causal_links": [
                {
                    "harness_criterion": link.harness_criterion,
                    "agent_criterion": link.agent_criterion,
                    "chain": link.chain,
                    "confidence": link.confidence,
                }
                for link in self.causal_links
            ],
            "notes": list(self.notes),
        }


def classify(
    harness: LayerVerdicts,
    agent: LayerVerdicts,
    device: DeviceHealth | None = None,
    causal_links: Iterable[CausalLink] = (),
) -> Attribution:
    """Classify a run into exactly one attribution class.

    The order of the checks below is the order of precedence, and it is
    deliberate: apparatus health gates everything, because a broken apparatus
    tells us nothing about the subject under test.
    """

    device = device or DeviceHealth()
    links = tuple(link for link in causal_links if link.is_substantiated)
    notes: list[str] = []

    if not device.ok:
        return Attribution(
            attribution="inconclusive_device",
            label=ATTRIBUTION_LABELS["inconclusive_device"],
            reason=(
                "the measurement apparatus did not work for this run "
                f"({', '.join(device.failed_checks) or 'unspecified check failed'}); "
                "no conclusion about harness or agent can be drawn — re-run"
            ),
            harness=harness,
            agent=agent,
            device=device,
        )

    if device.measurement_bypassed:
        if device.bypass_affected_checks:
            notes.append(
                "the agent bypassed at least one required measurement channel; "
                "under binary scoring the affected Harness criteria score 0, "
                "and causal attribution assigns those observation failures to "
                "the Agent rather than treating them as intrinsic Harness defects"
            )
        else:
            notes.append(
                "the agent bypassed at least one measurement channel; criteria "
                "that depend on it are reported as not-applicable rather than "
                "as failures"
            )

    if not harness.measured or not agent.measured:
        unmeasured = [
            name
            for name, layer in (("harness", harness), ("agent", agent))
            if not layer.measured
        ]
        return Attribution(
            attribution="inconclusive_unmeasured",
            label=ATTRIBUTION_LABELS["inconclusive_unmeasured"],
            reason=(
                f"the {' and '.join(unmeasured)} layer produced no measurable "
                "criterion in this run; a gap in measurement is not a finding "
                "about the subject under test"
            ),
            harness=harness,
            agent=agent,
            device=device,
            notes=tuple(notes),
        )

    bypass_affected = set(device.bypass_affected_checks)
    intrinsic_harness_failures = tuple(
        rule_id for rule_id in harness.failed if rule_id not in bypass_affected
    )
    harness_failed = bool(intrinsic_harness_failures)
    agent_failed = bool(agent.failed or bypass_affected)

    # 逐条 AR 归因: 每条失败的 Agent 判据, 有没有链从某条失败的 Harness 判据连过来。
    # 题级单标签永远给不出这个粒度 —— 一道题里 AR fail 5 条, 成因可能各不相同。
    traced = {link.agent_criterion for link in links
              if link.agent_criterion in set(agent.failed)}
    axes = {
        "harness_defect_count": len(intrinsic_harness_failures),
        "agent_failure_count": len(agent.failed),
        "agent_failures_traced": len(traced),
        "agent_failures_untraced": len(agent.failed) - len(traced),
    }

    if harness_failed and agent_failed:
        if links:
            chains = "; ".join(
                f"{link.harness_criterion} -> {link.agent_criterion}" for link in links
            )
            return Attribution(
                attribution="harness_caused",
                label=ATTRIBUTION_LABELS["harness_caused"],
                reason=(
                    "a harness defect is linked to an agent-side failure by an "
                    f"explicit mechanism chain ({chains})"
                ),
                harness=harness,
                agent=agent,
                device=device,
                causal_links=links,
                notes=tuple(notes),
                **axes,
            )
        # 两层都有失败但连不起来。旧版在这里发 unrelated_defects,
        # 那是把「归不出来」当成了一个结论。实际成立的是两件事:
        #   (a) 模型的失败没有一条能追到 harness -> 这些失败归 Agent
        #   (b) harness 确实有缺陷 -> 由 harness_defect_count 单独记录
        # 两件事都报出来, 就不需要一个含糊的第三档。
        return Attribution(
            attribution="agent_caused",
            label=ATTRIBUTION_LABELS["agent_caused"],
            reason=(
                "no mechanism chain connects any harness defect to any agent-side "
                f"failure, so the {len(agent.failed)} agent failure(s) are charged to "
                f"the Agent; the {len(intrinsic_harness_failures)} harness defect(s) "
                "are recorded separately on the harness axis and are not excused"
            ),
            harness=harness,
            agent=agent,
            device=device,
            notes=tuple(notes),
            **axes,
        )

    if harness_failed:
        return Attribution(
            attribution="harness_defect_absorbed",
            label=ATTRIBUTION_LABELS["harness_defect_absorbed"],
            reason=(
                "the harness is defective but the deliverable is sound: the agent "
                "absorbed the defect. The defect is real and should be reported; "
                "the run is not evidence that the harness broke the task"
            ),
            harness=harness,
            agent=agent,
            device=device,
            notes=tuple(notes),
            **axes,
        )

    if agent_failed:
        return Attribution(
            attribution="agent_caused",
            label=ATTRIBUTION_LABELS["agent_caused"],
            reason="the harness behaved correctly; the deliverable is wrong",
            harness=harness,
            agent=agent,
            device=device,
            notes=tuple(notes),
            **axes,
        )

    return Attribution(
        attribution="clean",
        label=ATTRIBUTION_LABELS["clean"],
        reason="no harness defect and no agent-side failure",
        harness=harness,
        agent=agent,
        device=device,
        notes=tuple(notes),
        **axes,
    )

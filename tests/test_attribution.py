"""Tests for attribution classification.

Each test corresponds to one design rule in `tracetrial_bench/attribution.py`, and
each rule is there because its absence produced a wrong finding in practice.
The comments say which wrong finding, so a future reader can tell whether a
change is a fix or a regression.
"""

from __future__ import annotations

import unittest

from tracetrial_bench.attribution import (
    CausalLink,
    DeviceHealth,
    LayerVerdicts,
    classify,
)


def layer(failed=(), passed=(), na=()) -> LayerVerdicts:
    return LayerVerdicts(tuple(failed), tuple(passed), tuple(na))


class AttributionTests(unittest.TestCase):
    def test_clean_run(self):
        result = classify(layer(passed=["h1"]), layer(passed=["a1"]))
        self.assertEqual(result.attribution, "clean")

    def test_agent_failure_alone_is_agent_caused(self):
        result = classify(layer(passed=["h1"]), layer(failed=["a1"], passed=["a2"]))
        self.assertEqual(result.attribution, "agent_caused")

    def test_harness_defect_with_sound_deliverable_is_absorbed_not_caused(self):
        """A harness defect that did not damage the deliverable is still a real
        defect, but the run is not evidence that the harness broke the task.

        Collapsing this into "harness caused it" overstates harness impact; in
        our own data this case accounted for 8 of 74 runs.
        """
        result = classify(
            layer(failed=["h_compaction_summary_empty"], passed=["h2"]),
            layer(passed=["a1", "a2"]),
        )
        self.assertEqual(result.attribution, "harness_defect_absorbed")

    def test_both_failing_without_a_chain_is_not_harness_caused(self):
        """Co-occurrence is not causation.

        "The harness lost timestamps" and "the agent computed the wrong total"
        can both be true and unrelated.  Requiring an explicit mechanism chain
        is what keeps the harness from being blamed for everything that happens
        near it.
        """
        result = classify(
            layer(failed=["h_log_timestamps_missing"]),
            layer(failed=["a_totals_wrong"]),
        )
        self.assertEqual(result.attribution, "unrelated_defects")

    def test_both_failing_with_a_chain_is_harness_caused(self):
        result = classify(
            layer(failed=["h_secret_not_redacted"]),
            layer(failed=["a_secret_in_report"]),
            causal_links=[
                CausalLink(
                    harness_criterion="h_secret_not_redacted",
                    agent_criterion="a_secret_in_report",
                    chain=(
                        "the harness surfaced the credential in plaintext to the "
                        "model, the model copied it into the deliverable"
                    ),
                    confidence="high",
                )
            ],
        )
        self.assertEqual(result.attribution, "harness_caused")
        self.assertEqual(len(result.causal_links), 1)

    def test_link_without_a_chain_is_discarded(self):
        """A link asserted with an empty chain is an assertion, not evidence."""
        result = classify(
            layer(failed=["h1"]),
            layer(failed=["a1"]),
            causal_links=[CausalLink("h1", "a1", chain="   ")],
        )
        self.assertEqual(result.attribution, "unrelated_defects")

    def test_broken_apparatus_yields_no_attribution(self):
        """If the apparatus did not work, the run says nothing about the subject.

        Scoring it anyway is how "our trace capture failed" becomes "the harness
        is broken".
        """
        result = classify(
            layer(failed=["h1"]),
            layer(failed=["a1"]),
            device=DeviceHealth(ok=False, failed_checks=("native_trace_complete",)),
        )
        self.assertEqual(result.attribution, "inconclusive_device")
        self.assertIn("native_trace_complete", result.reason)

    def test_apparatus_gate_precedes_everything(self):
        """Even a fully clean run is inconclusive if the apparatus failed."""
        result = classify(
            layer(passed=["h1"]),
            layer(passed=["a1"]),
            device=DeviceHealth(ok=False, failed_checks=("runner_meta_present",)),
        )
        self.assertEqual(result.attribution, "inconclusive_device")

    def test_unmeasured_layer_is_inconclusive_not_clean(self):
        """All-N/A is not a pass.

        A layer where every criterion was unmeasurable has demonstrated nothing.
        Reporting it as clean manufactures a passing result out of a blind spot.
        """
        result = classify(layer(na=["h1", "h2"]), layer(passed=["a1"]))
        self.assertEqual(result.attribution, "inconclusive_unmeasured")

    def test_bypassed_measurement_is_recorded_as_a_note(self):
        """A bypassed channel is a caveat on the finding, not a harness defect."""
        result = classify(
            layer(passed=["h1"]),
            layer(failed=["a1"]),
            device=DeviceHealth(measurement_bypassed=True),
        )
        self.assertEqual(result.attribution, "agent_caused")
        self.assertTrue(any("bypass" in note for note in result.notes))

    def test_binary_harness_zeros_caused_by_agent_bypass_are_agent_caused(self):
        result = classify(
            layer(failed=["h_pressure", "h_notice"], passed=["h_lifecycle"]),
            layer(failed=["a_standalone_export"], passed=["a_output"]),
            device=DeviceHealth(
                measurement_bypassed=True,
                bypass_affected_checks=("h_pressure", "h_notice"),
            ),
        )

        self.assertEqual(result.attribution, "agent_caused")
        self.assertEqual(result.harness.failed, ("h_pressure", "h_notice"))
        self.assertEqual(result.harness.not_applicable, ())
        self.assertIn(
            "h_pressure",
            result.to_dict()["device"]["bypass_affected_checks"],
        )

    def test_na_verdicts_do_not_count_as_failures(self):
        verdicts = LayerVerdicts.from_rules(
            [
                {"id": "h1", "verdict": "pass"},
                {"id": "h2", "verdict": "N/A"},
                {"id": "h3", "verdict": "fail"},
            ]
        )
        self.assertEqual(verdicts.failed, ("h3",))
        self.assertEqual(verdicts.not_applicable, ("h2",))
        self.assertTrue(verdicts.measured)

    def test_serialization_round_trips_every_field(self):
        result = classify(
            layer(failed=["h1"], na=["h2"]),
            layer(failed=["a1"]),
            causal_links=[CausalLink("h1", "a1", chain="h1 caused a1")],
        )
        payload = result.to_dict()
        self.assertEqual(payload["attribution"], "harness_caused")
        self.assertEqual(payload["harness_layer"]["not_applicable"], ["h2"])
        self.assertEqual(payload["causal_links"][0]["chain"], "h1 caused a1")
        self.assertIn("device", payload)


if __name__ == "__main__":
    unittest.main()

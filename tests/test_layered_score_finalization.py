from __future__ import annotations

import unittest

from tracetrial_bench.llm_judge import _finalize_layer
from tracetrial_bench.score_config import ScoreItem, ScoreSheet


def _sheet(*ids: str) -> ScoreSheet:
    return ScoreSheet(
        version="score_v1",
        layer="harness",
        items=tuple(
            ScoreItem(id=item_id, type="rule", criterion=item_id)
            for item_id in ids
        ),
    )


class LayerFinalizationTests(unittest.TestCase):
    def test_every_item_participates_in_binary_layer_points(self) -> None:
        result = _finalize_layer(
            _sheet("passed", "bypassed"),
            [
                {"id": "passed", "score": 1, "reason": "observed"},
                {
                    "id": "bypassed",
                    "score": 0,
                    "reason": "[AGENT BYPASS] Agent bypassed the measurement channel.",
                },
            ],
        )

        self.assertEqual(result["points"], 25.0)
        self.assertEqual(result["passed_items"], 1)
        self.assertEqual(result["total_items"], 2)
        self.assertNotIn("applicable_items", result)
        self.assertNotIn("not_applicable_items", result)

    def test_all_failed_layer_scores_zero(self) -> None:
        result = _finalize_layer(
            _sheet("unmeasured"),
            [
                {
                    "id": "unmeasured",
                    "score": 0,
                    "reason": "Required measurement was not exercised.",
                }
            ],
        )

        self.assertEqual(result["points"], 0.0)
        self.assertEqual(result["passed_items"], 0)
        self.assertEqual(result["total_items"], 1)


if __name__ == "__main__":
    unittest.main()

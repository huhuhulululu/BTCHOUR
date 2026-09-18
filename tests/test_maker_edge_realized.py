"""017: scoring a fill by the model's own p is circular once the model is known
to be worse than the book. The realized metric asks the outcome instead."""

from __future__ import annotations

import unittest

from btchour.fees import fill_cost
from btchour.replay import _stamp_settlement
from btchour.research.evaluate import maker_edge_at_fill, maker_edge_realized


class StampSettlementTest(unittest.TestCase):
    def test_yes_side_in_the_money(self):
        take = _stamp_settlement({"strike": 100.0, "side": "yes"}, {100.0: "yes"})
        self.assertEqual(take["settles"], "yes")
        self.assertEqual(take["settle_value"], 1.0)

    def test_yes_side_out_of_the_money(self):
        take = _stamp_settlement({"strike": 100.0, "side": "yes"}, {100.0: "no"})
        self.assertEqual(take["settle_value"], 0.0)

    def test_no_side_in_the_money(self):
        take = _stamp_settlement({"strike": 100.0, "side": "no"}, {100.0: "no"})
        self.assertEqual(take["settle_value"], 1.0)

    def test_unsettled_strike_is_left_alone(self):
        take = _stamp_settlement({"strike": 100.0, "side": "yes"}, {100.0: ""})
        self.assertNotIn("settle_value", take)
        self.assertNotIn("settles", take)

    def test_missing_strike_is_left_alone(self):
        self.assertNotIn("settle_value", _stamp_settlement({"side": "yes"}, {100.0: "yes"}))


class MakerEdgeRealizedTest(unittest.TestCase):
    def test_no_fills(self):
        self.assertEqual(maker_edge_realized([], 0.25), {"fills": 0})
        self.assertEqual(maker_edge_realized([{"fill_model_p": 0.4}], 0.25), {"fills": 0})

    def test_edge_is_settlement_minus_cost(self):
        takes = [{"settle_value": 1.0}, {"settle_value": 0.0}, {"settle_value": 0.0}, {"settle_value": 0.0}]
        paid = fill_cost(0.25, 1.0, taker=False).cost
        out = maker_edge_realized(takes, 0.25)
        self.assertEqual(out["fills"], 4)
        self.assertAlmostEqual(out["settle_rate"], 0.25)
        self.assertAlmostEqual(out["edge"], 0.25 - paid)

    def test_a_rest_that_settles_below_its_cost_is_negative(self):
        takes = [{"settle_value": 0.0}] * 9 + [{"settle_value": 1.0}]
        out = maker_edge_realized(takes, 0.25)
        self.assertLess(out["edge"], 0.0)

    def test_model_metric_can_pass_where_the_outcome_fails(self):
        """The 017 failure mode, in one assertion.

        Ten fills the model reads at 0.35 that settle in the money only twice.
        `maker_edge_at_fill` sees +0.10 and passes; the outcome is -0.05.
        """
        takes = [
            {"fill_model_p": 0.35, "settle_value": 1.0 if i < 2 else 0.0, "ask": 0.30, "model_p": 0.35}
            for i in range(10)
        ]
        by_model = maker_edge_at_fill(takes, 0.25)
        by_outcome = maker_edge_realized(takes, 0.25)
        self.assertGreater(by_model["mean_edge_at_fill"], 0.0)
        self.assertLess(by_outcome["edge"], 0.0)


if __name__ == "__main__":
    unittest.main()

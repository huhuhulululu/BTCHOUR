from __future__ import annotations

import unittest

from btchour.fees import betting_ev, fill_cost
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.score import score_market


class ScoreTests(unittest.TestCase):
    def test_ev_matches_formula(self):
        cost = fill_cost(0.80, taker=True)
        self.assertAlmostEqual(cost.betting_ev(0.99), betting_ev(0.99, cost.if_win_roi))

    def test_aug13_late_cheap_ask_is_rejected(self):
        # Live replay: 16:54 UTC T79099.99 ask 0.83, spot 79194, ~6m left, p~0.85.
        market = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2513-T79099.99",
                "event_ticker": "KXBTCD-26AUG2513",
                "floor_strike": 79099.99,
                "strike_type": "greater",
                "yes_ask_dollars": "0.83",
                "yes_bid_dollars": "0.82",
                "no_ask_dollars": "0.18",
                "no_bid_dollars": "0.17",
                "open_time": "2026-08-25T16:00:00Z",
                "close_time": "2026-08-25T17:00:00Z",
            }
        )
        scores = score_market(
            market,
            SpotQuote(79194.2, "test", annual_vol=0.34),
            360,
            0.34,
            0.20,
            0.95,
            0.20,
        )
        yes = next(row for row in scores if row.side == "yes")
        self.assertFalse(yes.passes)
        self.assertIn("p ", yes.reject)

    def test_locked_twenty_percent_passes(self):
        market = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2515-T78000",
                "event_ticker": "KXBTCD-26AUG2515",
                "floor_strike": 78000,
                "strike_type": "greater",
                "yes_ask_dollars": "0.81",
                "yes_bid_dollars": "0.80",
                "no_ask_dollars": "0.20",
                "no_bid_dollars": "0.19",
                "open_time": "2026-08-25T18:00:00Z",
                "close_time": "2026-08-25T19:00:00Z",
            }
        )
        scores = score_market(
            market,
            SpotQuote(79200, "test", annual_vol=0.55),
            1800,
            0.55,
            0.20,
            0.95,
            0.20,
        )
        yes = next(row for row in scores if row.side == "yes")
        self.assertTrue(yes.passes)
        self.assertGreaterEqual(yes.ev, 0.20)
        self.assertGreaterEqual(yes.b, 0.20)
        self.assertAlmostEqual(yes.ev, yes.model_p * yes.b - (1 - yes.model_p))

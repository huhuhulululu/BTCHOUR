from __future__ import annotations

import unittest

from btchour.config import Settings
from btchour.exits import OpenPosition, evaluate_exit
from btchour.fees import fill_cost, lock_exit_price
from btchour.kalshi import market_from_api


def _market(yes_bid="0.70", yes_ask="0.71", no_bid="0.29", no_ask="0.30"):
    return market_from_api(
        {
            "ticker": "KXBTCD-26AUG2514-T78000",
            "event_ticker": "KXBTCD-26AUG2514",
            "floor_strike": 78000,
            "strike_type": "greater",
            "yes_bid_dollars": yes_bid,
            "yes_ask_dollars": yes_ask,
            "no_bid_dollars": no_bid,
            "no_ask_dollars": no_ask,
            "open_time": "2026-08-25T17:00:00Z",
            "close_time": "2026-08-25T18:00:00Z",
        }
    )


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(allow_early_exit=True)
        self.cost = fill_cost(0.50, taker=True)
        self.position = OpenPosition(
            ticker="KXBTCD-26AUG2514-T78000",
            event_ticker="KXBTCD-26AUG2514",
            side="yes",
            cost=self.cost.cost,
            count=1.0,
        )

    def test_lock_on_book_when_bid_clears_target(self):
        lock = lock_exit_price(self.cost.cost, 1.0, 0.20)
        action = evaluate_exit(self.position, _market(yes_bid=f"{lock:.2f}"), 0.80, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "lock_on_book")
        self.assertAlmostEqual(action.price, lock)

    def test_invalidate_when_p_collapses(self):
        action = evaluate_exit(self.position, _market(yes_bid="0.20", yes_ask="0.21"), 0.25, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "invalidate")
        self.assertAlmostEqual(action.price, 0.20)

    def test_flatten_into_twap_window(self):
        action = evaluate_exit(self.position, _market(yes_bid="0.55"), 0.70, 30, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "flatten_time")

    def test_hold_when_book_and_model_are_fine(self):
        action = evaluate_exit(self.position, _market(yes_bid="0.55"), 0.70, 1200, self.settings)
        self.assertIsNone(action)

    def test_disabled_early_exit(self):
        settings = Settings(allow_early_exit=False)
        lock = lock_exit_price(self.cost.cost, 1.0, 0.20)
        action = evaluate_exit(self.position, _market(yes_bid=f"{lock:.2f}"), 0.20, 10, settings)
        self.assertIsNone(action)

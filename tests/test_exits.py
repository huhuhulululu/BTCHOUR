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
            play="swing_t",
            entry_p=0.70,
        )

    def _act(self, *args, **kwargs):
        return evaluate_exit(*args, **kwargs).action

    def test_lock_on_book_when_bid_clears_target(self):
        lock = lock_exit_price(self.cost.cost, 1.0, 0.20)
        action = self._act(self.position, _market(yes_bid=f"{lock:.2f}"), 0.80, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "lock_on_book")
        self.assertAlmostEqual(action.price, lock)

    def test_t_clip_when_twelve_percent_prints_and_gap_is_gone(self):
        clip = lock_exit_price(self.cost.cost, 1.0, 0.12)
        action = self._act(self.position, _market(yes_bid=f"{clip:.2f}"), 0.62, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_clip")

    def test_runner_holds_twelve_percent_when_gap_is_still_wide(self):
        clip = lock_exit_price(self.cost.cost, 1.0, 0.12)
        action = self._act(self.position, _market(yes_bid=f"{clip:.2f}"), clip + 0.20, 1200, self.settings)
        self.assertIsNone(action)

    def test_trail_gives_back_from_peak(self):
        clip = lock_exit_price(self.cost.cost, 1.0, 0.12)
        peaked = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="yes",
            cost=self.cost.cost,
            count=1.0,
            peak_bid=round(clip + 0.06, 2),
            play="swing_t",
            entry_p=0.80,
        )
        action = self._act(peaked, _market(yes_bid=f"{clip:.2f}"), 0.70, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_trail")

    def test_fade_when_p_drops_from_entry(self):
        action = self._act(self.position, _market(yes_bid="0.48"), 0.45, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_fade")

    def test_invalidate_when_p_collapses(self):
        dead = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="yes",
            cost=self.cost.cost,
            count=1.0,
            play="hold_edge",
            entry_p=0.99,
        )
        settings = Settings(playbook="hold", allow_early_exit=True)
        action = self._act(dead, _market(yes_bid="0.20", yes_ask="0.21"), 0.25, 1200, settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "invalidate")
        self.assertAlmostEqual(action.price, 0.20)

    def test_lock_hold_is_not_t_clipped_or_flattened(self):
        cost = fill_cost(0.81, taker=True)
        locked = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="yes",
            cost=cost.cost,
            count=1.0,
            play="lock_hold",
            entry_p=0.999,
        )
        settings = Settings(playbook="flex", allow_early_exit=True)
        clip = lock_exit_price(cost.cost, 1.0, 0.12)
        action = self._act(locked, _market(yes_bid=f"{clip:.2f}"), 0.999, 20, settings)
        self.assertIsNone(action)

    def test_flatten_into_twap_window(self):
        action = self._act(self.position, _market(yes_bid="0.55"), 0.70, 30, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "flatten_time")

    def test_hold_when_book_and_model_are_fine(self):
        action = self._act(self.position, _market(yes_bid="0.55"), 0.70, 1200, self.settings)
        self.assertIsNone(action)

    def test_disabled_early_exit(self):
        settings = Settings(allow_early_exit=False)
        lock = lock_exit_price(self.cost.cost, 1.0, 0.20)
        action = self._act(self.position, _market(yes_bid=f"{lock:.2f}"), 0.20, 10, settings)
        self.assertIsNone(action)

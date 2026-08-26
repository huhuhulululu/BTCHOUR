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

    def test_lock_hold_still_locks_twenty_percent(self):
        locked = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="yes",
            cost=self.cost.cost,
            count=1.0,
            play="lock_hold",
            entry_p=0.999,
        )
        lock = lock_exit_price(self.cost.cost, 1.0, 0.20)
        action = self._act(locked, _market(yes_bid=f"{lock:.2f}"), 0.999, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "lock_on_book")
        self.assertAlmostEqual(action.price, lock)

    def test_t_does_not_force_twenty_percent_if_band_is_still_running(self):
        floor = lock_exit_price(self.cost.cost, 1.0, 0.10)
        action = self._act(self.position, _market(yes_bid=f"{floor:.2f}"), floor + 0.20, 1200, self.settings)
        self.assertIsNone(action)

    def test_t_clips_ten_percent_when_gap_is_gone(self):
        floor = lock_exit_price(self.cost.cost, 1.0, 0.10)
        action = self._act(self.position, _market(yes_bid=f"{floor:.2f}"), floor + 0.05, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_clip")

    def test_t_caps_at_fifty_percent_even_if_gap_is_wide(self):
        cap = lock_exit_price(self.cost.cost, 1.0, 0.50)
        action = self._act(self.position, _market(yes_bid=f"{cap:.2f}"), cap + 0.20, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_clip")
        self.assertGreaterEqual(action.price, cap - 1e-12)

    def test_trail_gives_back_from_peak(self):
        clip = lock_exit_price(self.cost.cost, 1.0, 0.10)
        pulled = round(clip - 0.03, 2)
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
        action = self._act(peaked, _market(yes_bid=f"{pulled:.2f}"), 0.70, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_trail")

    def test_impulse_wait_holds_a_twelve_percent_bounce(self):
        cost = fill_cost(0.25, taker=False)
        waiting = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="no",
            cost=cost.cost,
            count=1.0,
            play="impulse_wait",
            entry_p=0.36,
        )
        action = self._act(waiting, _market(yes_bid="0.78", yes_ask="0.79", no_bid="0.21", no_ask="0.22"), 0.30, 1200, self.settings)
        self.assertIsNone(action)

    def test_impulse_wait_holds_a_fifty_percent_bounce_mark(self):
        cost = fill_cost(0.25, taker=False)
        waiting = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="no",
            cost=cost.cost,
            count=1.0,
            play="impulse_wait",
            entry_p=0.36,
        )
        action = self._act(waiting, _market(yes_bid="0.88", yes_ask="0.89", no_bid="0.11", no_ask="0.12"), 0.20, 1200, self.settings)
        self.assertIsNone(action)

    def test_impulse_wait_stops_an_eighty_percent_hole(self):
        cost = fill_cost(0.25, taker=False)
        waiting = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="no",
            cost=cost.cost,
            count=1.0,
            play="impulse_wait",
            entry_p=0.36,
        )
        action = self._act(waiting, _market(yes_bid="0.96", yes_ask="0.97", no_bid="0.03", no_ask="0.04"), 0.10, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_wait_stop")

    def test_impulse_wait_does_not_invalidate_on_bounce(self):
        cost = fill_cost(0.25, taker=False)
        waiting = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="no",
            cost=cost.cost,
            count=1.0,
            play="impulse_wait",
            entry_p=0.42,
        )
        action = self._act(waiting, _market(yes_bid="0.76", yes_ask="0.77", no_bid="0.23", no_ask="0.24"), 0.30, 1200, self.settings)
        self.assertIsNone(action)

    def test_impulse_wait_does_not_fade_on_bounce(self):
        cost = fill_cost(0.25, taker=False)
        waiting = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="no",
            cost=cost.cost,
            count=1.0,
            play="impulse_wait",
            entry_p=0.36,
        )
        action = self._act(waiting, _market(yes_bid="0.76", yes_ask="0.77", no_bid="0.23", no_ask="0.24"), 0.20, 1200, self.settings)
        self.assertIsNone(action)

    def test_hard_stop_cuts_a_twelve_percent_loss(self):
        action = self._act(self.position, _market(yes_bid="0.42"), 0.62, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_stop")

    def test_fade_when_p_drops_from_entry(self):
        action = self._act(self.position, _market(yes_bid="0.48"), 0.45, 1200, self.settings)
        self.assertIsNotNone(action)
        self.assertEqual(action.reason, "t_fade")

    def test_cheap_impulse_does_not_use_the_forty_percent_invalidate(self):
        cost = fill_cost(0.24, taker=True)
        cheap = OpenPosition(
            ticker=self.position.ticker,
            event_ticker=self.position.event_ticker,
            side="yes",
            cost=cost.cost,
            count=1.0,
            play="impulse_t",
            entry_p=0.34,
        )
        action = self._act(cheap, _market(yes_bid="0.26", yes_ask="0.27"), 0.33, 1200, self.settings)
        self.assertIsNone(action)

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

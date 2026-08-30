from __future__ import annotations

import unittest

from btchour.fees import betting_ev, fill_cost, lock_exit_price, max_entry_price, round_trip_roi, taker_fee


class FeeTests(unittest.TestCase):
    def test_published_taker_table(self):
        self.assertAlmostEqual(taker_fee(0.50, 100), 1.75, places=4)
        self.assertAlmostEqual(taker_fee(0.10, 100), 0.63, places=4)
        self.assertAlmostEqual(taker_fee(0.90, 100), 0.63, places=4)

    def test_twenty_percent_caps(self):
        self.assertEqual(max_entry_price(0.20, taker=True), 0.82)
        self.assertEqual(max_entry_price(0.20, taker=False), 0.83)
        self.assertGreaterEqual(fill_cost(0.82, taker=True).if_win_roi, 0.20)
        self.assertLess(fill_cost(0.83, taker=True).if_win_roi, 0.20)
        self.assertGreaterEqual(fill_cost(0.83, taker=False).if_win_roi, 0.20)

    def test_ev_is_p_times_b_minus_one_minus_p(self):
        p, b = 0.99, 0.20
        self.assertAlmostEqual(betting_ev(p, b), p * b - (1 - p))
        cost = fill_cost(0.82, taker=True)
        self.assertAlmostEqual(cost.betting_ev(0.99), cost.expected_roi(0.99), places=10)
        self.assertGreater(cost.betting_ev(0.997), 0.20)

    def test_lock_exit_price_is_lowest_tick_that_clears_target(self):
        cost = fill_cost(0.50, taker=True)
        lock = lock_exit_price(cost.cost, 1.0, 0.20)
        self.assertIsNotNone(lock)
        self.assertGreaterEqual(round_trip_roi(cost.cost, lock), 0.20)
        self.assertLess(round_trip_roi(cost.cost, round(lock - 0.01, 4)), 0.20)

    def test_expensive_entry_cannot_lock_twenty_percent(self):
        cost = fill_cost(0.90, taker=True)
        self.assertIsNone(lock_exit_price(cost.cost, 1.0, 0.20))

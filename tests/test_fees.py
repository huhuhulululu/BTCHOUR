from __future__ import annotations

import unittest

from btchour.fees import fill_cost, max_entry_price, taker_fee


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

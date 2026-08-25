from __future__ import annotations

import unittest

from btchour.model import digital_prob, realized_annual_vol


class ModelTests(unittest.TestCase):
    def test_deep_itm_near_expiry(self):
        p = digital_prob(spot=79200, strike=78000, seconds=120, annual_vol=0.55)
        self.assertGreater(p, 0.99)

    def test_deep_otm_near_expiry(self):
        p = digital_prob(spot=79200, strike=81000, seconds=120, annual_vol=0.55)
        self.assertLess(p, 0.01)

    def test_atm_is_near_half(self):
        p = digital_prob(spot=80000, strike=80000, seconds=3600, annual_vol=0.55)
        self.assertGreater(p, 0.40)
        self.assertLess(p, 0.55)

    def test_last_hour_settlement_boundary(self):
        # KXBTCD-26AUG2513 finalized yes at 79099.99 and no at 79199.99.
        self.assertGreater(digital_prob(79150, 79099.99, 30, 0.55), 0.75)
        self.assertLess(digital_prob(79150, 79199.99, 30, 0.55), 0.25)

    def test_realized_vol_clamp(self):
        prices = [80000 + i for i in range(20)]
        vol = realized_annual_vol(prices, 60)
        self.assertIsNotNone(vol)
        self.assertGreaterEqual(vol, 0.25)
        self.assertLessEqual(vol, 1.8)

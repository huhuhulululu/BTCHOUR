from __future__ import annotations

import unittest

from btchour.model import digital_prob, effective_vol, realized_annual_vol, required_p, sigma_cushion


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

    def test_effective_vol_never_below_floor(self):
        self.assertEqual(effective_vol(0.31, 0.55), 0.55)
        self.assertEqual(effective_vol(0.90, 0.55), 0.90)
        self.assertEqual(effective_vol(None, 0.55), 0.55)

    def test_realized_vol_clamp(self):
        prices = [80000 + i for i in range(20)]
        vol = realized_annual_vol(prices, 60)
        self.assertIsNotNone(vol)
        self.assertGreaterEqual(vol, 0.25)
        self.assertLessEqual(vol, 1.8)

    def test_sigma_cushion_grows_with_distance(self):
        near = sigma_cushion(79200, 79100, 1800, 0.55)
        far = sigma_cushion(79200, 78000, 1800, 0.55)
        self.assertGreater(far, near)
        self.assertGreater(far, 3.2)

    def test_required_p_at_twenty_percent_odds(self):
        self.assertAlmostEqual(required_p(0.20, 0.20), 1.0)
        self.assertLess(required_p(0.20, 0.25), 0.97)

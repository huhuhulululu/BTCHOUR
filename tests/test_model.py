from __future__ import annotations

import unittest

from btchour.model import (
    digital_prob,
    effective_vol,
    norm_cdf,
    realized_annual_vol,
    required_p,
    sigma_cushion,
    twap_variance_seconds,
)


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


class TwapVarianceTests(unittest.TestCase):
    """Settlement is the mean of the last 60 BRTI ticks, not the last print.

    Var[(1/T)*integral of the path over [tau-T, tau]] = tau - 2T/3 for tau >= T, and
    tau^3/(3T^2) once we are inside the averaging window. See model.twap_variance_seconds.
    """

    def test_far_from_close_is_almost_the_plain_time(self):
        self.assertAlmostEqual(twap_variance_seconds(3600.0), 3600.0 - 40.0)
        self.assertGreater(twap_variance_seconds(3600.0) / 3600.0, 0.98)

    def test_the_two_branches_agree_at_the_window(self):
        self.assertAlmostEqual(twap_variance_seconds(60.0), 20.0)
        self.assertAlmostEqual(twap_variance_seconds(60.0 - 1e-9), 20.0, places=6)

    def test_inside_the_window_is_the_cubic_branch(self):
        # tau^3 / (3 T^2); the old code claimed a full 60s of dispersion here.
        self.assertAlmostEqual(twap_variance_seconds(30.0), 30.0 ** 3 / (3 * 60.0 ** 2))
        self.assertAlmostEqual(twap_variance_seconds(10.0), 10.0 ** 3 / (3 * 60.0 ** 2))

    def test_monotone_and_never_negative(self):
        previous = -1.0
        for seconds in (0.0, 1.0, 5.0, 30.0, 60.0, 120.0, 600.0, 1800.0, 3600.0):
            value = twap_variance_seconds(seconds)
            self.assertGreaterEqual(value, 0.0)
            self.assertGreater(value, previous)
            previous = value

    def test_never_exceeds_the_point_price_variance(self):
        for seconds in (1.0, 10.0, 45.0, 60.0, 300.0, 3600.0):
            self.assertLess(twap_variance_seconds(seconds), seconds)

    def test_probability_is_sharper_near_the_close(self):
        """Same $200 cushion, less time: the TWAP correction pushes p further from 0.5."""
        far = digital_prob(78200.0, 78000.0, 600.0, 0.55)
        near = digital_prob(78200.0, 78000.0, 120.0, 0.55)
        self.assertGreater(near, far)
        self.assertGreater(near, 0.99)

    def test_cushion_and_probability_use_one_distribution(self):
        spot, strike, seconds, vol = 78200.0, 78000.0, 300.0, 0.55
        z = sigma_cushion(spot, strike, seconds, vol)
        p = digital_prob(spot, strike, seconds, vol)
        # p should be about N(z) for a small drift term; they must not disagree wildly.
        self.assertAlmostEqual(p, norm_cdf(z), places=2)

from __future__ import annotations

import unittest

from btchour.paper import paper_settle


class LastHourReplayTests(unittest.TestCase):
    """KXBTCD-26AUG2513 settled yes <= 79099.99 and no >= 79199.99."""

    def test_itm_yes_at_twenty_percent_would_have_won(self):
        cost = 0.82 + 0.0104
        pnl = paper_settle(cost, 1, "yes", "yes")
        self.assertGreaterEqual(pnl / cost, 0.20)

    def test_otm_yes_would_have_lost(self):
        cost = 0.82 + 0.0104
        pnl = paper_settle(cost, 1, "yes", "no")
        self.assertLess(pnl, 0)
        self.assertAlmostEqual(pnl, -cost)

    def test_otm_no_at_twenty_percent_would_have_won(self):
        cost = 0.82 + 0.0104
        pnl = paper_settle(cost, 1, "no", "no")
        self.assertGreaterEqual(pnl / cost, 0.20)

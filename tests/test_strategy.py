from __future__ import annotations

import unittest
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.strategy import evaluate_market


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2514-T78000",
        "event_ticker": "KXBTCD-26AUG2514",
        "title": "Bitcoin price",
        "subtitle": "$78,000 or above",
        "status": "active",
        "floor_strike": 78000,
        "strike_type": "greater",
        "yes_bid_dollars": "0.80",
        "yes_ask_dollars": "0.81",
        "no_bid_dollars": "0.19",
        "no_ask_dollars": "0.20",
        "open_time": "2026-08-25T17:00:00Z",
        "close_time": "2026-08-25T18:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(target_profit=0.20, min_win_prob=0.95, min_expected_roi=0.20)
        self.now = datetime(2026, 8, 25, 17, 30, tzinfo=timezone.utc)
        self.spot = SpotQuote(79200, "test", annual_vol=0.55)

    def test_cheap_itm_yes_is_taken(self):
        opps = evaluate_market(_market(), self.spot, self.settings, self.now)
        sides = {row.side for row in opps}
        self.assertIn("yes", sides)
        yes = next(row for row in opps if row.side == "yes")
        self.assertGreaterEqual(yes.if_win_roi, 0.20)
        self.assertGreaterEqual(yes.model_p, 0.95)
        self.assertTrue(yes.taker)

    def test_expensive_itm_yes_is_skipped(self):
        market = _market(yes_bid_dollars="0.98", yes_ask_dollars="0.99", no_bid_dollars="0.01", no_ask_dollars="0.02")
        opps = evaluate_market(market, self.spot, self.settings, self.now)
        self.assertFalse(any(row.side == "yes" for row in opps))

    def test_maker_can_rest_at_twenty_percent(self):
        market = _market(yes_bid_dollars="0.98", yes_ask_dollars="0.99", no_bid_dollars="0.01", no_ask_dollars="0.02")
        settings = Settings(target_profit=0.20, min_win_prob=0.95, min_expected_roi=0.20, allow_maker=True)
        opps = evaluate_market(market, self.spot, settings, self.now)
        yes = next(row for row in opps if row.side == "yes")
        self.assertFalse(yes.taker)
        self.assertEqual(yes.limit_price, 0.83)
        self.assertGreaterEqual(yes.if_win_roi, 0.20)

    def test_coin_flip_is_skipped(self):
        market = _market(
            ticker="KXBTCD-26AUG2514-T79200",
            floor_strike=79200,
            yes_bid_dollars="0.49",
            yes_ask_dollars="0.50",
            no_bid_dollars="0.50",
            no_ask_dollars="0.51",
        )
        opps = evaluate_market(market, self.spot, self.settings, self.now)
        self.assertEqual(opps, [])

    def test_cheap_otm_no_is_taken(self):
        market = _market(
            ticker="KXBTCD-26AUG2514-T81000",
            subtitle="$81,000 or above",
            floor_strike=81000,
            yes_bid_dollars="0.19",
            yes_ask_dollars="0.20",
            no_bid_dollars="0.80",
            no_ask_dollars="0.81",
        )
        opps = evaluate_market(market, self.spot, self.settings, self.now)
        self.assertTrue(any(row.side == "no" and row.if_win_roi >= 0.20 for row in opps))

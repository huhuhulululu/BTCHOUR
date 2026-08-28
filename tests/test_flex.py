from __future__ import annotations

import unittest
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.replay import ReplayBar, replay_bars
from btchour.strategy import evaluate_market, evaluate_scalp_market, scan_markets


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2514-T78000",
        "event_ticker": "KXBTCD-26AUG2514",
        "title": "Bitcoin price",
        "subtitle": "$78,000 or above",
        "status": "active",
        "floor_strike": 78000,
        "strike_type": "greater",
        "yes_bid_dollars": "0.49",
        "yes_ask_dollars": "0.50",
        "no_bid_dollars": "0.50",
        "no_ask_dollars": "0.51",
        "open_time": "2026-08-25T17:00:00Z",
        "close_time": "2026-08-25T18:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class FlexStrategyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 17, 30, tzinfo=timezone.utc)
        self.spot = SpotQuote(79200, "test", annual_vol=0.55)

    def test_hold_still_takes_cheap_itm(self):
        settings = Settings(playbook="hold", max_contracts=1)
        opps = evaluate_market(_market(yes_bid_dollars="0.80", yes_ask_dollars="0.81"), self.spot, settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "hold_edge")

    def test_scalp_takes_gap_that_fails_hold_p_gate(self):
        settings = Settings(playbook="scalp", max_contracts=1)
        market = _market(
            ticker="KXBTCD-26AUG2514-T79199.99",
            floor_strike=79199.99,
            yes_bid_dollars="0.61",
            yes_ask_dollars="0.62",
            no_bid_dollars="0.38",
            no_ask_dollars="0.39",
        )
        # Near the strike: hold p-gate fails, but gap can still qualify as a scalp.
        spot = SpotQuote(79600, "test", annual_vol=0.55)
        hold = evaluate_market(market, spot, Settings(playbook="hold", max_contracts=1), self.now)
        self.assertEqual(hold, [])
        scalps = evaluate_scalp_market(market, spot, settings, self.now)
        self.assertTrue(scalps)
        self.assertEqual(scalps[0].play, "markout_scalp")
        self.assertEqual(scalps[0].side, "yes")
        self.assertGreaterEqual(scalps[0].ev, 0.20)
        self.assertIsNotNone(scalps[0].lock_price)

    def test_flex_prefers_hold_over_scalp(self):
        settings = Settings(playbook="flex", max_contracts=1)
        market = _market(yes_bid_dollars="0.80", yes_ask_dollars="0.81")
        opps = scan_markets([market], self.spot, settings, self.now)
        self.assertGreaterEqual(len(opps), 1)
        self.assertIn(opps[0].play, {"hold_edge", "lock_hold"})

    def test_late_or_expensive_scalp_is_skipped(self):
        settings = Settings(playbook="scalp", max_contracts=1)
        late = datetime(2026, 8, 25, 17, 55, tzinfo=timezone.utc)
        market = _market(
            ticker="KXBTCD-26AUG2514-T79199.99",
            floor_strike=79199.99,
            yes_bid_dollars="0.61",
            yes_ask_dollars="0.62",
            no_bid_dollars="0.38",
            no_ask_dollars="0.39",
        )
        spot = SpotQuote(79600, "test", annual_vol=0.55)
        self.assertEqual(evaluate_scalp_market(market, spot, settings, late), [])
        expensive = _market(yes_bid_dollars="0.74", yes_ask_dollars="0.75")
        self.assertEqual(evaluate_scalp_market(expensive, self.spot, settings, self.now), [])

    def test_coin_flip_is_not_a_scalp(self):
        settings = Settings(playbook="flex", max_contracts=1)
        market = _market(
            ticker="KXBTCD-26AUG2514-T79200",
            floor_strike=79200,
            yes_bid_dollars="0.49",
            yes_ask_dollars="0.50",
            no_bid_dollars="0.50",
            no_ask_dollars="0.51",
        )
        opps = scan_markets([market], self.spot, settings, self.now)
        self.assertFalse(any(row.play in {"markout_scalp", "impulse_t", "swing_t"} for row in opps))
        self.assertFalse(any(row.play == "impulse_wait" for row in opps))


class FlexReplayTests(unittest.TestCase):
    def test_early_lock_beats_settlement(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc).timestamp()
        first = int(maturity - 1800)
        second = int(maturity - 1740)
        bars = [
            ReplayBar(
                end_ts=first,
                spot=79200,
                vol=0.55,
                quotes={78000.0: {"yes_ask": 0.50, "yes_bid": 0.49}},
            ),
            ReplayBar(
                end_ts=second,
                spot=79200,
                vol=0.55,
                quotes={78000.0: {"yes_ask": 0.71, "yes_bid": 0.70}},
            ),
        ]
        report = replay_bars("KXBTCD-26AUG2514", bars, {78000.0: "yes"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        take = report["takes"][0]
        self.assertEqual(take["exit_reason"], "lock_on_book")
        self.assertGreaterEqual(take["roi"], 0.20)
        self.assertGreater(take["pnl"], 0)

    def test_lock_clip_does_not_open_a_t_in_the_same_hour(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc).timestamp()
        bars = [
            ReplayBar(
                int(maturity - 1800),
                79200,
                0.55,
                {78000.0: {"yes_ask": 0.81, "yes_bid": 0.80}},
                impulse=0,
            ),
            ReplayBar(
                int(maturity - 1740),
                79380,
                0.55,
                {
                    78000.0: {"yes_ask": 0.96, "yes_bid": 0.95},
                    79199.99: {"yes_ask": 0.50, "yes_bid": 0.49},
                },
                impulse=180,
            ),
        ]
        report = replay_bars("KXBTCD-26AUG2514", bars, {78000.0: "yes", 79199.99: "yes"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertTrue(str(report["takes"][0]["play"]).startswith("lock"))
        self.assertNotEqual(report["takes"][0]["play"], "impulse_t")

    def test_invalidate_cuts_a_losing_scalp(self):
        settings = Settings(playbook="scalp", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc).timestamp()
        first = int(maturity - 1800)
        second = int(maturity - 1740)
        bars = [
            ReplayBar(
                end_ts=first,
                spot=79600,
                vol=0.55,
                quotes={79199.99: {"yes_ask": 0.62, "yes_bid": 0.61}},
            ),
            ReplayBar(
                end_ts=second,
                spot=78800,
                vol=0.55,
                quotes={79199.99: {"yes_ask": 0.21, "yes_bid": 0.20}},
            ),
        ]
        report = replay_bars("KXBTCD-26AUG2514", bars, {79199.99: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        take = report["takes"][0]
        self.assertEqual(take["exit_reason"], "invalidate")
        self.assertLess(take["pnl"], 0)

    def test_can_flip_side_after_invalidate(self):
        settings = Settings(playbook="scalp", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc).timestamp()
        first = int(maturity - 1800)
        second = int(maturity - 1740)
        third = int(maturity - 1680)
        bars = [
            ReplayBar(
                end_ts=first,
                spot=79600,
                vol=0.55,
                quotes={79199.99: {"yes_ask": 0.62, "yes_bid": 0.61}},
            ),
            ReplayBar(
                end_ts=second,
                spot=78800,
                vol=0.55,
                quotes={79199.99: {"yes_ask": 0.52, "yes_bid": 0.51}},
            ),
            ReplayBar(
                end_ts=third,
                spot=78750,
                vol=0.55,
                quotes={79199.99: {"yes_ask": 0.32, "yes_bid": 0.31}},
            ),
        ]
        report = replay_bars("KXBTCD-26AUG2514", bars, {79199.99: "no"}, maturity, settings)
        reasons = [take["exit_reason"] for take in report["takes"]]
        sides = [take["side"] for take in report["takes"]]
        self.assertGreaterEqual(len(report["takes"]), 2)
        self.assertEqual(sides[0], "yes")
        self.assertEqual(reasons[0], "invalidate")
        self.assertEqual(sides[1], "no")

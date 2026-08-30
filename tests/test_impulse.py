from __future__ import annotations

import unittest
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.replay import ReplayBar, replay_bars
from btchour.strategy import evaluate_impulse_market, evaluate_swing_market, scan_markets


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2517-T78799.99",
        "event_ticker": "KXBTCD-26AUG2517",
        "title": "Bitcoin price",
        "subtitle": "$78,799.99 or above",
        "status": "active",
        "floor_strike": 78799.99,
        "strike_type": "greater",
        "yes_bid_dollars": "0.50",
        "yes_ask_dollars": "0.51",
        "no_bid_dollars": "0.45",
        "no_ask_dollars": "0.46",
        "open_time": "2026-08-25T20:00:00Z",
        "close_time": "2026-08-25T21:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class ImpulseTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 20, 30, tzinfo=timezone.utc)
        self.settings = Settings(playbook="swing", max_contracts=1)

    def test_rally_takes_yes_with_the_move(self):
        spot = SpotQuote(78880, "test", annual_vol=0.55, impulse=140)
        opps = evaluate_impulse_market(_market(), spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_t")
        self.assertEqual(opps[0].side, "yes")

    def test_dump_takes_no_with_the_move(self):
        spot = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        opps = evaluate_impulse_market(_market(), spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_t")
        self.assertEqual(opps[0].side, "no")

    def test_flat_tape_is_not_an_impulse(self):
        spot = SpotQuote(78880, "test", annual_vol=0.55, impulse=20)
        self.assertEqual(evaluate_impulse_market(_market(), spot, self.settings, self.now), [])

    def test_impulse_does_not_need_a_value_gap(self):
        spot = SpotQuote(78880, "test", annual_vol=0.55, impulse=140)
        market = _market(yes_bid_dollars="0.51", yes_ask_dollars="0.52")
        self.assertEqual(evaluate_swing_market(market, spot, self.settings, self.now), [])
        opps = evaluate_impulse_market(market, spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_t")

    def test_flex_still_prefers_lock_over_impulse(self):
        lock_mkt = _market(
            ticker="KXBTCD-26AUG2517-T78000",
            floor_strike=78000,
            yes_bid_dollars="0.80",
            yes_ask_dollars="0.81",
        )
        t_mkt = _market()
        spot = SpotQuote(79200, "test", annual_vol=0.55, impulse=140)
        opps = scan_markets([t_mkt, lock_mkt], spot, Settings(playbook="flex", max_contracts=1), self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "lock_hold")


class ImpulseReplayTests(unittest.TestCase):
    def test_impulse_clips_and_does_not_revenge_flip(self):
        settings = Settings(playbook="swing", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc).timestamp()
        bars = [
            ReplayBar(int(maturity - 1800), 78700, 0.55, {78799.99: {"yes_ask": 0.45, "yes_bid": 0.44}}, impulse=20),
            ReplayBar(int(maturity - 1740), 78880, 0.55, {78799.99: {"yes_ask": 0.51, "yes_bid": 0.50}}, impulse=180),
            ReplayBar(int(maturity - 1680), 78920, 0.55, {78799.99: {"yes_ask": 0.74, "yes_bid": 0.73}}, impulse=40),
            ReplayBar(int(maturity - 1620), 78600, 0.55, {78799.99: {"yes_ask": 0.32, "yes_bid": 0.31}}, impulse=-280),
        ]
        report = replay_bars("KXBTCD-26AUG2517", bars, {78799.99: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["play"], "impulse_t")
        self.assertEqual(report["takes"][0]["side"], "yes")
        self.assertIn(report["takes"][0]["exit_reason"], {"t_clip", "lock_on_book"})
        self.assertGreater(report["takes"][0]["pnl"], 0)

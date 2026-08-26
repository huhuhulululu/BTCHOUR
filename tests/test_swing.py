from __future__ import annotations

import unittest
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.replay import ReplayBar, replay_bars
from btchour.strategy import (
    SessionMemory,
    SwingMemory,
    apply_swing_memory,
    evaluate_lock_market,
    evaluate_swing_market,
    remember_session_exit,
    remember_swing_exit,
    refresh_session,
    scan_markets,
)


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2516-T79199.99",
        "event_ticker": "KXBTCD-26AUG2516",
        "title": "Bitcoin price",
        "subtitle": "$79,199.99 or above",
        "status": "active",
        "floor_strike": 79199.99,
        "strike_type": "greater",
        "yes_bid_dollars": "0.49",
        "yes_ask_dollars": "0.50",
        "no_bid_dollars": "0.50",
        "no_ask_dollars": "0.51",
        "open_time": "2026-08-25T19:00:00Z",
        "close_time": "2026-08-25T20:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class SwingStrategyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 19, 30, tzinfo=timezone.utc)
        self.settings = Settings(playbook="swing", max_contracts=1)

    def test_atm_gap_is_a_t_entry(self):
        spot = SpotQuote(79600, "test", annual_vol=0.55)
        market = _market(yes_bid_dollars="0.61", yes_ask_dollars="0.62")
        opps = evaluate_swing_market(market, spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "swing_t")
        self.assertEqual(opps[0].side, "yes")

    def test_coin_flip_is_not_a_t(self):
        spot = SpotQuote(79200, "test", annual_vol=0.55)
        opps = evaluate_swing_market(_market(), spot, self.settings, self.now)
        self.assertEqual(opps, [])

    def test_deep_itm_is_left_to_lock_not_t(self):
        spot = SpotQuote(79200, "test", annual_vol=0.55)
        market = _market(
            ticker="KXBTCD-26AUG2516-T78000",
            floor_strike=78000,
            yes_bid_dollars="0.80",
            yes_ask_dollars="0.81",
        )
        swings = evaluate_swing_market(market, spot, self.settings, self.now)
        locks = evaluate_lock_market(market, spot, Settings(playbook="lock", max_contracts=1), self.now)
        self.assertEqual(swings, [])
        self.assertTrue(any(row.play == "lock_hold" for row in locks))

    def test_flex_prefers_lock_take_over_t(self):
        spot = SpotQuote(79200, "test", annual_vol=0.55)
        lock_mkt = _market(
            ticker="KXBTCD-26AUG2516-T78000",
            floor_strike=78000,
            yes_bid_dollars="0.80",
            yes_ask_dollars="0.81",
        )
        t_mkt = _market(yes_bid_dollars="0.61", yes_ask_dollars="0.62")
        opps = scan_markets([t_mkt, lock_mkt], spot, Settings(playbook="flex", max_contracts=1), self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "lock_hold")

    def test_flex_takes_t_when_lock_is_absent(self):
        spot = SpotQuote(79600, "test", annual_vol=0.55, impulse=140)
        t_mkt = _market(yes_bid_dollars="0.49", yes_ask_dollars="0.50")
        opps = scan_markets([t_mkt], spot, Settings(playbook="flex", max_contracts=1), self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_t")
        self.assertEqual(opps[0].side, "yes")


class SwingReplayTests(unittest.TestCase):
    def test_fast_t_clips_then_stops(self):
        settings = Settings(playbook="swing", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc).timestamp()
        bars = [
            ReplayBar(int(maturity - 1800), 79600, 0.55, {79199.99: {"yes_ask": 0.62, "yes_bid": 0.61}}),
            ReplayBar(int(maturity - 1740), 79400, 0.55, {79199.99: {"yes_ask": 0.74, "yes_bid": 0.73}}),
            ReplayBar(int(maturity - 1680), 78800, 0.55, {79199.99: {"yes_ask": 0.32, "yes_bid": 0.31}}),
        ]
        report = replay_bars("KXBTCD-26AUG2516", bars, {79199.99: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["side"], "yes")
        self.assertEqual(report["takes"][0]["exit_reason"], "t_clip")
        self.assertGreater(report["takes"][0]["pnl"], 0)

    def test_fade_stops_more_t_in_the_same_hour(self):
        settings = Settings(playbook="swing", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc).timestamp()
        bars = [
            ReplayBar(int(maturity - 1800), 79600, 0.55, {79199.99: {"yes_ask": 0.62, "yes_bid": 0.61}}),
            ReplayBar(int(maturity - 1740), 78800, 0.55, {79199.99: {"yes_ask": 0.21, "yes_bid": 0.20}}),
            ReplayBar(int(maturity - 1680), 78750, 0.55, {79199.99: {"yes_ask": 0.32, "yes_bid": 0.31}}),
        ]
        report = replay_bars("KXBTCD-26AUG2516", bars, {79199.99: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertIn(report["takes"][0]["exit_reason"], {"t_fade", "t_stop"})

    def test_memory_blocks_flip_after_clip(self):
        now = datetime(2026, 8, 25, 19, 30, tzinfo=timezone.utc)
        memory = remember_swing_exit(SwingMemory(), "KXBTCD-26AUG2516-T79199.99", "yes", "t_clip")
        settings = Settings(playbook="swing", max_contracts=1)
        spot = SpotQuote(78800, "test", annual_vol=0.55)
        same = evaluate_swing_market(
            _market(yes_bid_dollars="0.31", yes_ask_dollars="0.32"),
            spot,
            settings,
            now,
        )
        other = evaluate_swing_market(
            _market(
                ticker="KXBTCD-26AUG2516-T78999.99",
                floor_strike=78999.99,
                yes_bid_dollars="0.61",
                yes_ask_dollars="0.62",
            ),
            SpotQuote(79600, "test", annual_vol=0.55),
            settings,
            now,
        )
        kept = apply_swing_memory(same + other, memory)
        self.assertEqual(kept, [])
        self.assertTrue(memory.dead)

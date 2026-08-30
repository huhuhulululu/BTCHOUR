from __future__ import annotations

import unittest
from datetime import datetime, timezone

from btchour.account import _same_side_clips
from btchour.config import Settings
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.replay import ReplayBar, replay_bars
from btchour.strategy import (
    SessionMemory,
    apply_swing_memory,
    evaluate_impulse_market,
    remember_session_exit,
    refresh_session,
    scan_markets,
)


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2520-T78599.99",
        "event_ticker": "KXBTCD-26AUG2520",
        "title": "Bitcoin price",
        "subtitle": "$78,599.99 or above",
        "status": "active",
        "floor_strike": 78599.99,
        "strike_type": "greater",
        "yes_bid_dollars": "0.74",
        "yes_ask_dollars": "0.75",
        "no_bid_dollars": "0.25",
        "no_ask_dollars": "0.26",
        "open_time": "2026-08-25T23:00:00Z",
        "close_time": "2026-08-26T00:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class ManualDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        self.settings = Settings(playbook="flex", max_contracts=1, impulse_taker=True)

    def test_dump_can_buy_cheap_maker_style_no(self):
        spot = SpotQuote(78480, "test", annual_vol=0.55, impulse=-160)
        opps = evaluate_impulse_market(_market(), spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_t")
        self.assertEqual(opps[0].side, "no")
        self.assertLessEqual(opps[0].ask, 0.26)

    def test_tired_yes_chase_is_rejected(self):
        spot = SpotQuote(78680, "test", annual_vol=0.55, impulse=40)
        market = _market(
            ticker="KXBTCD-26AUG2520-T78499.99",
            floor_strike=78499.99,
            yes_bid_dollars="0.60",
            yes_ask_dollars="0.61",
            no_bid_dollars="0.39",
            no_ask_dollars="0.40",
        )
        self.assertEqual(evaluate_impulse_market(market, spot, self.settings, self.now), [])

    def test_loss_skips_the_opposite_side_next_hour(self):
        session = remember_session_exit(SessionMemory(), "KXBTCD-26AUG2520", "t_stop", -1.2, "no")
        session = refresh_session(session, "KXBTCD-26AUG2521")
        now = datetime(2026, 8, 26, 0, 20, tzinfo=timezone.utc)
        dump = _market(
            ticker="KXBTCD-26AUG2521-T78599.99",
            event_ticker="KXBTCD-26AUG2521",
            floor_strike=78599.99,
            open_time="2026-08-26T00:00:00Z",
            close_time="2026-08-26T01:00:00Z",
        )
        chase = _market(
            ticker="KXBTCD-26AUG2521-T78499.99",
            event_ticker="KXBTCD-26AUG2521",
            floor_strike=78499.99,
            yes_bid_dollars="0.48",
            yes_ask_dollars="0.49",
            no_bid_dollars="0.51",
            no_ask_dollars="0.52",
            open_time="2026-08-26T00:00:00Z",
            close_time="2026-08-26T01:00:00Z",
        )
        dump_opps = apply_swing_memory(
            scan_markets([dump], SpotQuote(78480, "test", annual_vol=0.55, impulse=-160), self.settings, now),
            None,
            session,
        )
        chase_opps = apply_swing_memory(
            scan_markets([chase], SpotQuote(78680, "test", annual_vol=0.55, impulse=160), self.settings, now),
            None,
            session,
        )
        self.assertEqual(dump_opps, [])
        self.assertEqual(chase_opps, [])
        cleared = refresh_session(session, "KXBTCD-26AUG2522")
        self.assertFalse(cleared.skip_next)

    def test_rebuilt_loss_skips_only_the_next_hour(self):
        # Paper rebuilds SessionMemory from trades each scan. skipped_event is
        # missing, so the skip hour must be inferred from last_loss_event —
        # otherwise every later hour looks like the first skip hour.
        rebuilt = remember_session_exit(SessionMemory(), "KXBTCD-26AUG2602", "t_wait_stop", -2.22, "no")
        self.assertIsNone(rebuilt.skipped_event)
        skip_hour = refresh_session(rebuilt, "KXBTCD-26AUG2603")
        self.assertTrue(skip_hour.skip_next)
        self.assertEqual(skip_hour.skipped_event, "KXBTCD-26AUG2603")
        live_hour = refresh_session(rebuilt, "KXBTCD-26AUG2604")
        self.assertFalse(live_hour.skip_next)

    def test_replay_clips_ten_percent_and_does_not_flip(self):
        settings = Settings(
            playbook="flex",
            max_contracts=1,
            max_notional=10,
            allow_early_exit=True,
            impulse_taker=True,
        )
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        bars = [
            ReplayBar(int(maturity - 1800), 78480, 0.55, {78599.99: {"yes_ask": 0.75, "yes_bid": 0.74}}, impulse=-180),
            ReplayBar(int(maturity - 1740), 78380, 0.55, {78599.99: {"yes_ask": 0.62, "yes_bid": 0.61}}, impulse=-40),
            ReplayBar(int(maturity - 1680), 78650, 0.55, {78599.99: {"yes_ask": 0.40, "yes_bid": 0.39}}, impulse=180),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {78599.99: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["side"], "no")
        self.assertIn(report["takes"][0]["exit_reason"], {"t_clip", "t_trail", "t_fade", "t_stop"})
        self.assertGreater(report["takes"][0]["pnl"], 0)


class AccountClipTests(unittest.TestCase):
    def test_same_side_maker_clip(self):
        fills = [
            {
                "created_time": "2026-08-25T23:29:39Z",
                "ticker": "KXBTCD-26AUG2520-T78599.99",
                "action": "buy",
                "side": "no",
                "count_fp": "50",
                "yes_price_dollars": "0.80",
                "no_price_dollars": "0.20",
                "fee_cost": "0",
                "is_taker": False,
            },
            {
                "created_time": "2026-08-25T23:31:44Z",
                "ticker": "KXBTCD-26AUG2520-T78599.99",
                "action": "sell",
                "side": "no",
                "count_fp": "50",
                "yes_price_dollars": "0.63",
                "no_price_dollars": "0.37",
                "fee_cost": "0",
                "is_taker": False,
            },
        ]
        clips = _same_side_clips(fills)
        self.assertEqual(len(clips), 1)
        self.assertGreaterEqual(clips[0]["roi"], 0.10)
        self.assertTrue(clips[0]["maker_round"])
        self.assertLess(clips[0]["hold_s"], 180)

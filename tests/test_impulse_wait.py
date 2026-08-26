from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from btchour import store as store_mod
from btchour.config import Settings
from btchour.engine import refresh_working
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.paper import paper_fill
from btchour.replay import ReplayBar, replay_bars
from btchour.strategy import (
    SessionMemory,
    apply_swing_memory,
    evaluate_impulse_market,
    evaluate_impulse_wait_market,
    impulse_wait_flipped,
    remember_session_exit,
    refresh_session,
    scan_markets,
    wait_book_crossed,
)


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2520-T78699.99",
        "event_ticker": "KXBTCD-26AUG2520",
        "title": "Bitcoin price",
        "subtitle": "$78,699.99 or above",
        "status": "active",
        "floor_strike": 78699.99,
        "strike_type": "greater",
        "yes_bid_dollars": "0.59",
        "yes_ask_dollars": "0.60",
        "no_bid_dollars": "0.40",
        "no_ask_dollars": "0.41",
        "open_time": "2026-08-25T23:00:00Z",
        "close_time": "2026-08-26T00:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class ImpulseWaitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        self.settings = Settings(playbook="flex", max_contracts=1, allow_maker=True)
        self.spot = SpotQuote(78800, "test", annual_vol=0.55, impulse=-160)

    def test_scan_rests_only_one_dump_wait(self):
        other = _market(
            ticker="KXBTCD-26AUG2520-T78599.99",
            floor_strike=78599.99,
            yes_bid_dollars="0.62",
            yes_ask_dollars="0.63",
            no_bid_dollars="0.37",
            no_ask_dollars="0.38",
        )
        opps = scan_markets([_market(), other], self.spot, self.settings, self.now)
        waits = [row for row in opps if row.play == "impulse_wait"]
        self.assertEqual(len(waits), 1)

    def test_dump_rests_under_a_forty_cent_no(self):
        opps = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_wait")
        self.assertEqual(opps[0].side, "no")
        self.assertFalse(opps[0].taker)
        self.assertAlmostEqual(opps[0].limit_price, 0.25)
        self.assertGreater(opps[0].ask, 0.25)

    def test_already_cheap_ask_is_not_a_wait_or_a_taker(self):
        market = _market(
            yes_bid_dollars="0.77",
            yes_ask_dollars="0.78",
            no_bid_dollars="0.22",
            no_ask_dollars="0.23",
        )
        self.assertEqual(evaluate_impulse_wait_market(market, self.spot, self.settings, self.now), [])
        self.assertEqual(evaluate_impulse_market(market, self.spot, self.settings, self.now), [])

    def test_atm_impulse_still_takes_before_waiting(self):
        atm = _market(
            ticker="KXBTCD-26AUG2520-T78799.99",
            floor_strike=78799.99,
            yes_bid_dollars="0.50",
            yes_ask_dollars="0.51",
            no_bid_dollars="0.49",
            no_ask_dollars="0.50",
        )
        dump = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        opps = scan_markets([_market(), atm], dump, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_t")
        self.assertEqual(opps[0].side, "no")

    def test_flag_off_disables_the_rest(self):
        settings = Settings(playbook="flex", max_contracts=1, allow_maker=True, impulse_wait=False)
        self.assertEqual(evaluate_impulse_wait_market(_market(), self.spot, settings, self.now), [])

    def test_after_a_loss_same_direction_taker_is_ok_but_wait_is_not(self):
        session = remember_session_exit(SessionMemory(), "KXBTCD-26AUG2518", "t_stop", -1.0, "no")
        session = refresh_session(session, "KXBTCD-26AUG2519")
        wait_mkt = _market(
            ticker="KXBTCD-26AUG2519-T78699.99",
            event_ticker="KXBTCD-26AUG2519",
            open_time="2026-08-25T22:00:00Z",
            close_time="2026-08-25T23:00:00Z",
        )
        atm = _market(
            ticker="KXBTCD-26AUG2519-T78799.99",
            event_ticker="KXBTCD-26AUG2519",
            open_time="2026-08-25T22:00:00Z",
            close_time="2026-08-25T23:00:00Z",
            floor_strike=78799.99,
            yes_bid_dollars="0.50",
            yes_ask_dollars="0.51",
            no_bid_dollars="0.49",
            no_ask_dollars="0.50",
        )
        dump = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        now = datetime(2026, 8, 25, 22, 30, tzinfo=timezone.utc)
        wait_only = apply_swing_memory(
            scan_markets([wait_mkt], self.spot, self.settings, now),
            None,
            session,
        )
        self.assertEqual(wait_only, [])
        taker = apply_swing_memory(
            scan_markets([atm], dump, self.settings, now),
            None,
            session,
        )
        self.assertTrue(taker)
        self.assertEqual(taker[0].play, "impulse_t")
        self.assertEqual(taker[0].side, "no")

    def test_rally_does_not_rest_yes(self):
        rally = SpotQuote(78800, "test", annual_vol=0.55, impulse=160)
        market = _market(
            yes_bid_dollars="0.40",
            yes_ask_dollars="0.41",
            no_bid_dollars="0.59",
            no_ask_dollars="0.60",
        )
        self.assertEqual(evaluate_impulse_wait_market(market, rally, self.settings, self.now), [])

    def test_fade_is_not_a_flip_but_opposite_impulse_is(self):
        self.assertFalse(impulse_wait_flipped("no", -20, self.settings))
        self.assertFalse(impulse_wait_flipped("no", -160, self.settings))
        self.assertTrue(impulse_wait_flipped("no", 160, self.settings))
        self.assertFalse(impulse_wait_flipped("yes", 20, self.settings))
        self.assertTrue(impulse_wait_flipped("yes", -160, self.settings))

    def test_minute_high_counts_as_a_maker_fill(self):
        self.assertFalse(wait_book_crossed("no", 0.25, 0.32))
        self.assertTrue(wait_book_crossed("no", 0.25, 0.32, yes_bid_high=0.76, impulse=-40))
        self.assertTrue(wait_book_crossed("no", 0.25, 0.24, impulse=-80))
        self.assertTrue(wait_book_crossed("yes", 0.25, 0.32, yes_ask_low=0.24, impulse=80))

    def test_bounce_does_not_fill_a_dump_rest(self):
        self.assertFalse(wait_book_crossed("no", 0.25, 0.14, yes_bid_high=0.86, impulse=95))
        self.assertFalse(wait_book_crossed("no", 0.25, 0.24, impulse=80))


class ImpulseWaitEngineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        self.settings = Settings(playbook="flex", max_contracts=1, allow_maker=True)
        self.spot = SpotQuote(78800, "test", annual_vol=0.55, impulse=-160)

    def test_promotes_at_rest_as_maker_not_the_crossed_ask(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        self.assertEqual(fill["status"], "working")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = db.record_trade(fill)
                still = refresh_working(db, self.settings, [_market()], self.spot, self.now)
                self.assertEqual(db.open_trades(), [])
                self.assertEqual(len(db.working_trades()), 1)
                crossed = _market(
                    yes_bid_dollars="0.77",
                    yes_ask_dollars="0.78",
                    no_bid_dollars="0.22",
                    no_ask_dollars="0.23",
                )
                promoted = refresh_working(db, self.settings, [crossed], self.spot, self.now)
                self.assertEqual(promoted[0]["status"], "open")
                row = db.open_trades()[0]
                self.assertEqual(row["id"], trade_id)
                self.assertAlmostEqual(row["price"], 0.25)
                self.assertEqual(row["taker"], 0)
                self.assertAlmostEqual(row["fee"], 0.0)
                self.assertEqual(still, [])

    def test_keeps_the_rest_when_the_dump_impulse_fades(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                faded = SpotQuote(78800, "test", annual_vol=0.55, impulse=-20)
                updates = refresh_working(db, self.settings, [_market()], faded, self.now)
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)

    def test_does_not_promote_on_a_bounce_print(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                bounce = SpotQuote(78910, "test", annual_vol=0.55, impulse=80)
                crossed = _market(
                    yes_bid_dollars="0.86",
                    yes_ask_dollars="0.87",
                    no_bid_dollars="0.13",
                    no_ask_dollars="0.14",
                )
                updates = refresh_working(db, self.settings, [crossed], bounce, self.now)
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)

    def test_cancels_when_the_tape_flips_to_a_rally(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                rally = SpotQuote(78800, "test", annual_vol=0.55, impulse=160)
                updates = refresh_working(db, self.settings, [_market()], rally, self.now)
                self.assertEqual(updates[0]["status"], "cancelled")
                self.assertEqual(db.working_trades(), [])


class ImpulseWaitReplayTests(unittest.TestCase):
    def test_rests_then_holds_the_bounce_then_clips(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        strike = 78699.99
        bars = [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.61, "yes_bid": 0.60}}, impulse=-160),
            ReplayBar(int(maturity - 1740), 78800, 0.55, {strike: {"yes_ask": 0.76, "yes_bid": 0.75}}, impulse=-180),
            ReplayBar(int(maturity - 1680), 78800, 0.55, {strike: {"yes_ask": 0.86, "yes_bid": 0.85}}, impulse=80),
            ReplayBar(int(maturity - 1620), 78800, 0.55, {strike: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-120),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {strike: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        take = report["takes"][0]
        self.assertEqual(take["play"], "impulse_wait")
        self.assertEqual(take["side"], "no")
        self.assertAlmostEqual(take["ask"], 0.25)
        self.assertEqual(take["exit_reason"], "t_clip")
        self.assertGreater(take["pnl"], 0)
        self.assertGreaterEqual(take["roi"], 0.50)

    def test_fade_keeps_the_bid_then_minute_high_fills(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        strike = 78699.99
        bars = [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.74, "yes_bid": 0.73}}, impulse=-112),
            ReplayBar(int(maturity - 1740), 78800, 0.55, {strike: {"yes_ask": 0.73, "yes_bid": 0.71}}, impulse=-87),
            ReplayBar(
                int(maturity - 1680),
                78800,
                0.55,
                {strike: {"yes_ask": 0.66, "yes_bid": 0.65, "yes_bid_high": 0.76}},
                impulse=-40,
            ),
            ReplayBar(int(maturity - 1620), 78800, 0.55, {strike: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-80),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {strike: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["play"], "impulse_wait")
        self.assertAlmostEqual(report["takes"][0]["ask"], 0.25)
        self.assertEqual(report["takes"][0]["exit_reason"], "t_clip")
        self.assertGreater(report["takes"][0]["pnl"], 0)

    def test_bounce_rip_does_not_fill_then_second_dump_does(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        strike = 78699.99
        bars = [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.74, "yes_bid": 0.73}}, impulse=-112),
            ReplayBar(
                int(maturity - 1740),
                78910,
                0.55,
                {strike: {"yes_ask": 0.86, "yes_bid": 0.85, "yes_bid_high": 0.86}},
                impulse=95,
            ),
            ReplayBar(int(maturity - 1680), 78940, 0.55, {strike: {"yes_ask": 0.86, "yes_bid": 0.85}}, impulse=112),
            ReplayBar(
                int(maturity - 1620),
                78790,
                0.55,
                {strike: {"yes_ask": 0.65, "yes_bid": 0.64, "yes_bid_high": 0.75}},
                impulse=-119,
            ),
            ReplayBar(int(maturity - 1560), 78640, 0.55, {strike: {"yes_ask": 0.40, "yes_bid": 0.39}}, impulse=-80),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {strike: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        take = report["takes"][0]
        self.assertEqual(take["play"], "impulse_wait")
        self.assertAlmostEqual(take["ask"], 0.25)
        self.assertEqual(take["exit_reason"], "t_clip")
        self.assertGreater(take["pnl"], 0)

    def test_lock_close_still_deads_the_wait(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        bars = [
            ReplayBar(
                int(maturity - 1800),
                79200,
                0.55,
                {
                    78000.0: {"yes_ask": 0.81, "yes_bid": 0.80},
                    78699.99: {"yes_ask": 0.61, "yes_bid": 0.60},
                },
                impulse=0,
            ),
            ReplayBar(
                int(maturity - 1740),
                78800,
                0.55,
                {
                    78000.0: {"yes_ask": 0.96, "yes_bid": 0.95},
                    78699.99: {"yes_ask": 0.61, "yes_bid": 0.60},
                },
                impulse=-160,
            ),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {78000.0: "yes", 78699.99: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertTrue(str(report["takes"][0]["play"]).startswith("lock"))
        self.assertNotEqual(report["takes"][0]["play"], "impulse_wait")

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from btchour import store as store_mod
from btchour.config import Settings
from btchour.engine import refresh_working
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote, digital_prob
from btchour.paper import paper_fill
from btchour.replay import ReplayBar, replay_bars
from btchour.tickers import parse_event_ticker
from btchour.strategy import (
    SessionMemory,
    SwingMemory,
    apply_swing_memory,
    evaluate_impulse_market,
    dump_wait_rest_ready,
    evaluate_impulse_wait_market,
    coupon_sides,
    impulse_wait_flipped,
    impulse_wait_wrong_side,
    is_coupon_window,
    is_fast_window,
    pick_flex_entries,
    remember_session_exit,
    remember_swing_exit,
    refresh_session,
    scan_markets,
    tape_at_rest,
    wait_book_crossed,
    _taker_impulse_qualifies,
)


def _print(side: str = "no", rest: float = 0.25, count: float = 3, book: str | None = None, **extra):
    book = book or ("ask" if side == "yes" else "bid")
    yes = rest if side == "yes" else round(1.0 - rest, 4)
    row = {
        "yes_price_dollars": f"{yes:.4f}",
        "no_price_dollars": f"{1.0 - yes:.4f}",
        "taker_book_side": book,
        "count_fp": f"{count:.2f}",
        "is_block_trade": False,
    }
    row.update(extra)
    return row


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


def _close_ts(event_ticker: str) -> float:
    """Replay maturity must be that hour's close, or next-session gating misses."""
    return parse_event_ticker(event_ticker)["close_utc"].timestamp()


class ImpulseWaitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        self.settings = Settings(playbook="flex", max_contracts=1, allow_maker=True)
        self.spot = SpotQuote(78800, "test", annual_vol=0.55, impulse=-160)

    def test_scan_rests_two_in_band_dump_waits_nearest_first(self):
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
        self.assertEqual(len(waits), 2)
        self.assertEqual(
            [row.ticker for row in waits],
            ["KXBTCD-26AUG2520-T78699.99", "KXBTCD-26AUG2520-T78599.99"],
        )

    def test_scan_rests_three_aug2520_in_band_dump_waits(self):
        # Human AUG2520: T78699 / T78599 / T78799 in one hour.
        t78599 = _market(
            ticker="KXBTCD-26AUG2520-T78599.99",
            floor_strike=78599.99,
            yes_bid_dollars="0.62",
            yes_ask_dollars="0.63",
            no_bid_dollars="0.37",
            no_ask_dollars="0.38",
        )
        t78799 = _market(
            ticker="KXBTCD-26AUG2520-T78799.99",
            floor_strike=78799.99,
            yes_bid_dollars="0.59",
            yes_ask_dollars="0.60",
            no_bid_dollars="0.39",
            no_ask_dollars="0.40",
        )
        opps = scan_markets([_market(), t78599, t78799], self.spot, self.settings, self.now)
        waits = [row for row in opps if row.play == "impulse_wait"]
        self.assertEqual(len(waits), 3)
        self.assertEqual(
            [row.ticker for row in waits],
            [
                "KXBTCD-26AUG2520-T78799.99",
                "KXBTCD-26AUG2520-T78699.99",
                "KXBTCD-26AUG2520-T78599.99",
            ],
        )
        chosen = pick_flex_entries(opps)
        self.assertEqual(len(chosen), 3)
        self.assertEqual([row.play for row in chosen], ["impulse_wait"] * 3)
        self.assertEqual([row.ticker for row in chosen], [row.ticker for row in waits])

    def test_scan_rests_the_near_atm_strike_not_the_cheapest_ask(self):
        # Live AUG2602 05:06Z: T78499 ask 0.29 is the 29¢ knife. Human rests
        # the 32–42¢ coupon (T78599), not the 0.60 ATM mid that never prints
        # rest, and not the far OTM penny that bounce-crushed to 0.03.
        now = datetime(2026, 8, 26, 5, 6, tzinfo=timezone.utc)
        far = _market(
            ticker="KXBTCD-26AUG2602-T78499.99",
            event_ticker="KXBTCD-26AUG2602",
            floor_strike=78499.99,
            yes_bid_dollars="0.70",
            yes_ask_dollars="0.71",
            no_bid_dollars="0.28",
            no_ask_dollars="0.29",
            open_time="2026-08-26T05:00:00Z",
            close_time="2026-08-26T06:00:00Z",
        )
        near = _market(
            ticker="KXBTCD-26AUG2602-T78599.99",
            event_ticker="KXBTCD-26AUG2602",
            floor_strike=78599.99,
            yes_bid_dollars="0.57",
            yes_ask_dollars="0.58",
            no_bid_dollars="0.41",
            no_ask_dollars="0.42",
            open_time="2026-08-26T05:00:00Z",
            close_time="2026-08-26T06:00:00Z",
        )
        atm = _market(
            ticker="KXBTCD-26AUG2602-T78699.99",
            event_ticker="KXBTCD-26AUG2602",
            floor_strike=78699.99,
            yes_bid_dollars="0.39",
            yes_ask_dollars="0.40",
            no_bid_dollars="0.59",
            no_ask_dollars="0.60",
            open_time="2026-08-26T05:00:00Z",
            close_time="2026-08-26T06:00:00Z",
        )
        spot = SpotQuote(78689.70, "test", annual_vol=0.55, impulse=-104)
        opps = scan_markets([far, near, atm], spot, self.settings, now)
        waits = [row for row in opps if row.play == "impulse_wait"]
        self.assertEqual(
            [row.ticker for row in waits],
            ["KXBTCD-26AUG2602-T78599.99"],
        )
        self.assertAlmostEqual(waits[0].ask, 0.42)
        self.assertAlmostEqual(waits[0].limit_price, 0.25)

    def test_scan_prefers_in_band_coupon_over_atm_mid_pad(self):
        now = datetime(2026, 8, 28, 22, 10, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2819-T77499.99",
            event_ticker="KXBTCD-26AUG2819",
            floor_strike=77499.99,
            yes_bid_dollars="0.67",
            yes_ask_dollars="0.68",
            no_bid_dollars="0.31",
            no_ask_dollars="0.32",
            open_time="2026-08-28T22:00:00Z",
            close_time="2026-08-28T23:00:00Z",
        )
        pad = _market(
            ticker="KXBTCD-26AUG2819-T77399.99",
            event_ticker="KXBTCD-26AUG2819",
            floor_strike=77399.99,
            yes_bid_dollars="0.38",
            yes_ask_dollars="0.39",
            no_bid_dollars="0.60",
            no_ask_dollars="0.61",
            open_time="2026-08-28T22:00:00Z",
            close_time="2026-08-28T23:00:00Z",
        )
        spot = SpotQuote(77360, "test", annual_vol=0.55, impulse=-160)
        opps = scan_markets([pad, coupon], spot, self.settings, now)
        waits = [row for row in opps if row.play == "impulse_wait"]
        self.assertEqual(
            [row.ticker for row in waits],
            ["KXBTCD-26AUG2819-T77499.99"],
        )
        self.assertAlmostEqual(waits[0].ask, 0.32)
        self.assertAlmostEqual(waits[0].limit_price, 0.25)
        self.assertFalse(waits[0].taker)

    def test_does_not_park_a_lonely_quarter_under_a_dead_book(self):
        now = datetime(2026, 8, 28, 23, 46, tzinfo=timezone.utc)
        pad = _market(
            ticker="KXBTCD-26AUG2820-T77699.99",
            event_ticker="KXBTCD-26AUG2820",
            floor_strike=77699.99,
            yes_bid_dollars="0.29",
            yes_ask_dollars="0.30",
            no_bid_dollars="0.69",
            no_ask_dollars="0.70",
            open_time="2026-08-28T23:00:00Z",
            close_time="2026-08-29T00:00:00Z",
        )
        coupon = _market(
            ticker="KXBTCD-26AUG2820-T77599.99",
            event_ticker="KXBTCD-26AUG2820",
            floor_strike=77599.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-28T23:00:00Z",
            close_time="2026-08-29T00:00:00Z",
        )
        quiet = SpotQuote(77671, "test", annual_vol=0.55, impulse=-12)
        self.assertEqual(evaluate_impulse_wait_market(pad, quiet, self.settings, now), [])
        self.assertEqual(evaluate_impulse_wait_market(coupon, quiet, self.settings, now), [])
        dump = SpotQuote(77671, "test", annual_vol=0.55, impulse=-160)
        self.assertEqual(evaluate_impulse_wait_market(pad, dump, self.settings, now), [])
        hung = evaluate_impulse_wait_market(coupon, dump, self.settings, now)
        self.assertTrue(hung)
        self.assertEqual(hung[0].side, "no")
        self.assertAlmostEqual(hung[0].limit_price, 0.25)

    def test_dump_rests_under_a_forty_cent_no(self):
        opps = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_wait")
        self.assertEqual(opps[0].side, "no")
        self.assertFalse(opps[0].taker)
        self.assertAlmostEqual(opps[0].limit_price, 0.25)
        self.assertGreater(opps[0].ask, 0.25)

    def test_sees_the_coupon_before_the_dump(self):
        # Human + public maker: hang when 32–42¢ is visible. Dump fills; flip cancels.
        # Cached 42/42 hours show a nearby 32–42¢ NO; most minutes are not yet −$40.
        now = datetime(2026, 8, 26, 19, 21, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2616-T78299.99",
            event_ticker="KXBTCD-26AUG2616",
            floor_strike=78299.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-26T19:00:00Z",
            close_time="2026-08-26T20:00:00Z",
        )
        self.assertFalse(dump_wait_rest_ready(0, self.settings))
        self.assertFalse(dump_wait_rest_ready(-20, self.settings))
        self.assertFalse(dump_wait_rest_ready(80, self.settings))
        self.assertTrue(dump_wait_rest_ready(-100, self.settings))
        self.assertTrue(dump_wait_rest_ready(-160, self.settings))
        self.assertFalse(dump_wait_rest_ready(100, self.settings))
        self.assertFalse(dump_wait_rest_ready(160, self.settings))
        quiet = SpotQuote(78340, "test", annual_vol=0.55, impulse=0)
        self.assertEqual(evaluate_impulse_wait_market(coupon, quiet, self.settings, now), [])
        shallow = SpotQuote(78340, "test", annual_vol=0.55, impulse=-20)
        self.assertEqual(evaluate_impulse_wait_market(coupon, shallow, self.settings, now), [])
        mild_rally = SpotQuote(78340, "test", annual_vol=0.55, impulse=80)
        self.assertEqual(evaluate_impulse_wait_market(coupon, mild_rally, self.settings, now), [])
        dump = SpotQuote(78340, "test", annual_vol=0.55, impulse=-160)
        no_rest = evaluate_impulse_wait_market(coupon, dump, self.settings, now)
        self.assertTrue(no_rest)
        self.assertEqual(no_rest[0].side, "no")
        self.assertAlmostEqual(no_rest[0].ask, 0.36)
        self.assertAlmostEqual(no_rest[0].limit_price, 0.25)
        self.assertFalse(no_rest[0].taker)
        flipped = SpotQuote(78340, "test", annual_vol=0.55, impulse=160)
        self.assertEqual(evaluate_impulse_wait_market(coupon, flipped, self.settings, now), [])
        yes_book = _market(
            ticker="KXBTCD-26AUG2616-T78399.99",
            event_ticker="KXBTCD-26AUG2616",
            floor_strike=78399.99,
            yes_bid_dollars="0.35",
            yes_ask_dollars="0.36",
            no_bid_dollars="0.64",
            no_ask_dollars="0.65",
            open_time="2026-08-26T19:00:00Z",
            close_time="2026-08-26T20:00:00Z",
        )
        yes_flip = evaluate_impulse_wait_market(yes_book, flipped, self.settings, now)
        self.assertTrue(yes_flip)
        self.assertEqual(yes_flip[0].side, "yes")
        self.assertAlmostEqual(yes_flip[0].ask, 0.36)
        self.assertAlmostEqual(yes_flip[0].limit_price, 0.25)
        self.assertFalse(yes_flip[0].taker)

    def test_high_p_coupon_is_not_swallowed_by_taker_qualify(self):
        # Taker-off + coupon-first: a 32–42¢ NO with p≥52% used to return []
        # because _taker_impulse_qualifies skipped the rest, then impulse_t
        # also returned []. That ate the best coupons.
        now = datetime(2026, 8, 26, 19, 21, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2616-T78399.99",
            event_ticker="KXBTCD-26AUG2616",
            floor_strike=78399.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-26T19:00:00Z",
            close_time="2026-08-26T20:00:00Z",
        )
        spot = SpotQuote(78280, "test", annual_vol=0.55, impulse=-160)
        model_p = 1.0 - digital_prob(78280, 78399.99, 39 * 60, 0.55)
        self.assertGreaterEqual(model_p, 0.52)
        self.assertTrue(_taker_impulse_qualifies(0.36, model_p, self.settings))
        opps = evaluate_impulse_wait_market(coupon, spot, self.settings, now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_wait")
        self.assertAlmostEqual(opps[0].limit_price, 0.25)

    def test_already_dumped_twenty_nine_cent_ask_is_not_a_gap(self):
        # Paper AUG2602 T78499: rest 0.25 under ask 0.29 filled immediately, then 0.03.
        market = _market(
            ticker="KXBTCD-26AUG2602-T78599.99",
            event_ticker="KXBTCD-26AUG2602",
            floor_strike=78599.99,
            yes_bid_dollars="0.70",
            yes_ask_dollars="0.71",
            no_bid_dollars="0.28",
            no_ask_dollars="0.29",
            open_time="2026-08-26T05:00:00Z",
            close_time="2026-08-26T06:00:00Z",
        )
        spot = SpotQuote(78689.70, "test", annual_vol=0.55, impulse=-104)
        now = datetime(2026, 8, 26, 5, 6, tzinfo=timezone.utc)
        self.assertEqual(evaluate_impulse_wait_market(market, spot, self.settings, now), [])

    def test_already_cheap_ask_is_not_a_wait_or_a_taker(self):
        market = _market(
            yes_bid_dollars="0.77",
            yes_ask_dollars="0.78",
            no_bid_dollars="0.22",
            no_ask_dollars="0.23",
        )
        self.assertEqual(evaluate_impulse_wait_market(market, self.spot, self.settings, self.now), [])
        self.assertEqual(evaluate_impulse_market(market, self.spot, self.settings, self.now), [])

    def test_dump_coupon_beats_the_fifty_one_taker(self):
        # Paper AUG2609 12:39: T78099 ask 0.36 was the human rest; takers[:1]
        # ate T78299 @ 0.51 and t_stop. Coupon first.
        coupon = _market(
            ticker="KXBTCD-26AUG2520-T78599.99",
            floor_strike=78599.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
        )
        atm = _market(
            ticker="KXBTCD-26AUG2520-T78799.99",
            floor_strike=78799.99,
            yes_bid_dollars="0.50",
            yes_ask_dollars="0.51",
            no_bid_dollars="0.49",
            no_ask_dollars="0.50",
        )
        dump = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        opps = scan_markets([coupon, atm], dump, self.settings, self.now)
        chosen = pick_flex_entries(opps)
        self.assertTrue(chosen)
        self.assertEqual(chosen[0].play, "impulse_wait")
        self.assertEqual(chosen[0].ticker, coupon.ticker)
        self.assertAlmostEqual(chosen[0].ask, 0.36)

    def test_flex_default_does_not_take_when_no_coupon_book(self):
        locked = _market(
            ticker="KXBTCD-26AUG2520-T78799.99",
            floor_strike=78799.99,
            yes_bid_dollars="0.18",
            yes_ask_dollars="0.19",
            no_bid_dollars="0.81",
            no_ask_dollars="0.82",
        )
        dump = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        opps = scan_markets([locked], dump, self.settings, self.now)
        self.assertEqual([row.play for row in opps if row.play == "impulse_t"], [])
        self.assertEqual(pick_flex_entries(opps), [])

    def test_impulse_taker_flag_still_takes_when_no_coupon_book(self):
        knife = _market(
            ticker="KXBTCD-26AUG2520-T78799.99",
            floor_strike=78799.99,
            yes_bid_dollars="0.70",
            yes_ask_dollars="0.71",
            no_bid_dollars="0.29",
            no_ask_dollars="0.30",
        )
        dump = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        settings = Settings(playbook="flex", max_contracts=1, allow_maker=True, impulse_taker=True)
        opps = scan_markets([knife], dump, settings, self.now)
        chosen = pick_flex_entries(opps)
        self.assertTrue(chosen)
        self.assertEqual(chosen[0].play, "impulse_t")
        self.assertEqual(chosen[0].side, "no")

    def test_dump_coupon_still_rests_after_a_taker_clip(self):
        # Paper AUG2611: 14:00 YES impulse_t clipped, 14:14 dump T78399 ask 0.41
        # journaled and swing.dead blocked the rest. Human rests the coupon.
        memory = remember_swing_exit(
            SwingMemory(),
            "KXBTCD-26AUG2611-T78299.99",
            "yes",
            "t_clip",
            play="impulse_t",
        )
        now = datetime(2026, 8, 26, 14, 15, tzinfo=timezone.utc)
        dump = SpotQuote(78480, "test", annual_vol=0.55, impulse=-112)
        coupon = _market(
            ticker="KXBTCD-26AUG2611-T78399.99",
            event_ticker="KXBTCD-26AUG2611",
            floor_strike=78399.99,
            yes_bid_dollars="0.58",
            yes_ask_dollars="0.59",
            no_bid_dollars="0.40",
            no_ask_dollars="0.41",
            open_time="2026-08-26T14:00:00Z",
            close_time="2026-08-26T15:00:00Z",
        )
        atm = _market(
            ticker="KXBTCD-26AUG2611-T78499.99",
            event_ticker="KXBTCD-26AUG2611",
            floor_strike=78499.99,
            yes_bid_dollars="0.43",
            yes_ask_dollars="0.44",
            no_bid_dollars="0.55",
            no_ask_dollars="0.56",
            open_time="2026-08-26T14:00:00Z",
            close_time="2026-08-26T15:00:00Z",
        )
        waits = evaluate_impulse_wait_market(coupon, dump, self.settings, now)
        takers = evaluate_impulse_market(atm, dump, self.settings, now)
        self.assertTrue(waits)
        kept = apply_swing_memory(waits + takers, memory)
        self.assertEqual([row.play for row in kept], ["impulse_wait"])
        self.assertEqual(kept[0].ticker, coupon.ticker)

    def test_dump_coupon_still_rests_after_a_taker_stop(self):
        # Paper AUG2614: 17:30 YES impulse_t t_stop, 17:47 dump T78399 ask 0.40
        # journaled wait. allow_swing would keep it; same-hour last_loss_event
        # ate the rest. Skip-wait is the next ticker, not this one.
        memory = remember_swing_exit(
            SwingMemory(),
            "KXBTCD-26AUG2614-T78499.99",
            "yes",
            "t_stop",
            play="impulse_t",
        )
        session = remember_session_exit(
            SessionMemory(), "KXBTCD-26AUG2614", "t_stop", -0.6494, "yes", play="impulse_t"
        )
        session = refresh_session(session, "KXBTCD-26AUG2614")
        now = datetime(2026, 8, 26, 17, 47, tzinfo=timezone.utc)
        dump = SpotQuote(78462, "test", annual_vol=0.55, impulse=-103)
        coupon = _market(
            ticker="KXBTCD-26AUG2614-T78399.99",
            event_ticker="KXBTCD-26AUG2614",
            floor_strike=78399.99,
            yes_bid_dollars="0.59",
            yes_ask_dollars="0.60",
            no_bid_dollars="0.39",
            no_ask_dollars="0.40",
            open_time="2026-08-26T17:00:00Z",
            close_time="2026-08-26T18:00:00Z",
        )
        waits = evaluate_impulse_wait_market(coupon, dump, self.settings, now)
        self.assertTrue(waits)
        kept = apply_swing_memory(waits, memory, session)
        self.assertEqual([row.play for row in kept], ["impulse_wait"])
        self.assertEqual(kept[0].ticker, coupon.ticker)

    def test_skip_hour_still_blocks_wait_after_isolated_taker_stop(self):
        session = remember_session_exit(
            SessionMemory(), "KXBTCD-26AUG2614", "t_stop", -0.6494, "yes", play="impulse_t"
        )
        session = refresh_session(session, "KXBTCD-26AUG2615")
        now = datetime(2026, 8, 26, 18, 20, tzinfo=timezone.utc)
        dump = SpotQuote(78462, "test", annual_vol=0.55, impulse=-103)
        coupon = _market(
            ticker="KXBTCD-26AUG2615-T78399.99",
            event_ticker="KXBTCD-26AUG2615",
            floor_strike=78399.99,
            yes_bid_dollars="0.59",
            yes_ask_dollars="0.60",
            no_bid_dollars="0.39",
            no_ask_dollars="0.40",
            open_time="2026-08-26T18:00:00Z",
            close_time="2026-08-26T19:00:00Z",
        )
        waits = evaluate_impulse_wait_market(coupon, dump, self.settings, now)
        self.assertTrue(waits)
        self.assertEqual(apply_swing_memory(waits, None, session), [])

    def test_coupon_scratch_still_blocks_same_hour_second_wait(self):
        memory = remember_swing_exit(
            SwingMemory(),
            "KXBTCD-26AUG2604-T78899.99",
            "no",
            "t_scratch",
            play="impulse_wait",
        )
        session = remember_session_exit(
            SessionMemory(), "KXBTCD-26AUG2604", "t_scratch", -1.374, "no", play="impulse_wait"
        )
        session = refresh_session(session, "KXBTCD-26AUG2604")
        now = datetime(2026, 8, 26, 7, 40, tzinfo=timezone.utc)
        dump = SpotQuote(78462, "test", annual_vol=0.55, impulse=-112)
        later = _market(
            ticker="KXBTCD-26AUG2604-T78399.99",
            event_ticker="KXBTCD-26AUG2604",
            floor_strike=78399.99,
            yes_bid_dollars="0.59",
            yes_ask_dollars="0.60",
            no_bid_dollars="0.39",
            no_ask_dollars="0.40",
            open_time="2026-08-26T07:00:00Z",
            close_time="2026-08-26T08:00:00Z",
        )
        waits = evaluate_impulse_wait_market(later, dump, self.settings, now)
        self.assertTrue(waits)
        self.assertEqual(apply_swing_memory(waits, memory, session), [])

    def test_second_coupon_stays_blocked_after_a_coupon_clip(self):
        memory = remember_swing_exit(
            SwingMemory(),
            "KXBTCD-26AUG2611-T78399.99",
            "no",
            "t_clip",
            play="impulse_wait",
        )
        now = datetime(2026, 8, 26, 14, 15, tzinfo=timezone.utc)
        dump = SpotQuote(78580, "test", annual_vol=0.55, impulse=-112)
        later = _market(
            ticker="KXBTCD-26AUG2611-T78499.99",
            event_ticker="KXBTCD-26AUG2611",
            floor_strike=78499.99,
            yes_bid_dollars="0.58",
            yes_ask_dollars="0.59",
            no_bid_dollars="0.40",
            no_ask_dollars="0.41",
            open_time="2026-08-26T14:00:00Z",
            close_time="2026-08-26T15:00:00Z",
        )
        waits = evaluate_impulse_wait_market(later, dump, self.settings, now)
        self.assertTrue(waits)
        self.assertEqual(apply_swing_memory(waits, memory), [])

    def test_store_rebuild_allows_dump_wait_after_a_taker_clip(self):
        now = datetime(2026, 8, 26, 14, 15, tzinfo=timezone.utc)
        dump = SpotQuote(78480, "test", annual_vol=0.55, impulse=-112)
        coupon = _market(
            ticker="KXBTCD-26AUG2611-T78399.99",
            event_ticker="KXBTCD-26AUG2611",
            floor_strike=78399.99,
            yes_bid_dollars="0.58",
            yes_ask_dollars="0.59",
            no_bid_dollars="0.40",
            no_ask_dollars="0.41",
            open_time="2026-08-26T14:00:00Z",
            close_time="2026-08-26T15:00:00Z",
        )
        fill = {
            "ticker": "KXBTCD-26AUG2611-T78299.99",
            "event_ticker": "KXBTCD-26AUG2611",
            "side": "yes",
            "price": 0.50,
            "count": 10,
            "fee": 0.175,
            "cost": 5.175,
            "mode": "paper",
            "taker": True,
            "model_p": 0.597,
            "if_win_roi": 0.93,
            "expected_roi": 0.12,
            "status": "closed",
            "result": "t_clip",
            "pnl": 0.657,
            "raw": {"play": "impulse_t"},
        }
        leftover = {
            "ticker": "KXBTCD-26AUG2611-T79999.99",
            "event_ticker": "KXBTCD-26AUG2611",
            "side": "no",
            "price": 0.83,
            "count": 1,
            "fee": 0.0,
            "cost": 0.83,
            "mode": "paper",
            "taker": False,
            "model_p": 0.90,
            "if_win_roi": 0.20,
            "expected_roi": 0.08,
            "status": "working",
            "result": None,
            "pnl": None,
            "raw": {"play": "lock_wait"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = store_mod.Store(Path(tmp) / "t.sqlite")
            db.record_trade(fill)
            db.record_trade(leftover)
            memory = db.swing_memories()["KXBTCD-26AUG2611"]
        self.assertTrue(memory.dead)
        self.assertEqual(memory.play, "impulse_t")
        waits = apply_swing_memory(
            scan_markets([coupon], dump, self.settings, now),
            memory,
        )
        self.assertTrue(waits)
        self.assertEqual(waits[0].play, "impulse_wait")
        self.assertEqual(waits[0].ticker, coupon.ticker)

    def test_store_rebuild_allows_dump_wait_after_a_taker_stop(self):
        now = datetime(2026, 8, 26, 17, 47, tzinfo=timezone.utc)
        dump = SpotQuote(78462, "test", annual_vol=0.55, impulse=-103)
        coupon = _market(
            ticker="KXBTCD-26AUG2614-T78399.99",
            event_ticker="KXBTCD-26AUG2614",
            floor_strike=78399.99,
            yes_bid_dollars="0.59",
            yes_ask_dollars="0.60",
            no_bid_dollars="0.39",
            no_ask_dollars="0.40",
            open_time="2026-08-26T17:00:00Z",
            close_time="2026-08-26T18:00:00Z",
        )
        fill = {
            "ticker": "KXBTCD-26AUG2614-T78499.99",
            "event_ticker": "KXBTCD-26AUG2614",
            "side": "yes",
            "price": 0.50,
            "count": 10,
            "fee": 0.175,
            "cost": 5.175,
            "mode": "paper",
            "taker": True,
            "model_p": 0.521,
            "if_win_roi": 0.93,
            "expected_roi": 0.01,
            "status": "closed",
            "result": "t_stop",
            "pnl": -0.6494,
            "raw": {"play": "impulse_t"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = store_mod.Store(Path(tmp) / "t.sqlite")
            db.record_trade(fill)
            memory = db.swing_memories()["KXBTCD-26AUG2614"]
            session = refresh_session(db.session_memory(), "KXBTCD-26AUG2614")
        self.assertTrue(memory.dead)
        self.assertEqual(memory.play, "impulse_t")
        self.assertTrue(session.skip_next)
        self.assertEqual(session.last_play, "impulse_t")
        waits = apply_swing_memory(
            scan_markets([coupon], dump, self.settings, now),
            memory,
            session,
        )
        self.assertTrue(waits)
        self.assertEqual(waits[0].play, "impulse_wait")
        self.assertEqual(waits[0].ticker, coupon.ticker)

    def test_working_coupon_is_not_replaced_by_a_taker(self):
        coupon = _market(
            ticker="KXBTCD-26AUG2520-T78599.99",
            floor_strike=78599.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
        )
        atm = _market(
            ticker="KXBTCD-26AUG2520-T78799.99",
            floor_strike=78799.99,
            yes_bid_dollars="0.50",
            yes_ask_dollars="0.51",
            no_bid_dollars="0.49",
            no_ask_dollars="0.50",
        )
        dump = SpotQuote(78680, "test", annual_vol=0.55, impulse=-160)
        opps = scan_markets([coupon, atm], dump, self.settings, self.now)
        chosen = pick_flex_entries(opps, working_plays={"impulse_wait"})
        self.assertTrue(chosen)
        self.assertTrue(all(row.play == "impulse_wait" for row in chosen))
        self.assertEqual(chosen[0].ticker, coupon.ticker)
        self.assertFalse(any(row.taker for row in chosen))

    def test_flex_still_adds_dump_waits_while_one_is_working(self):
        other = _market(
            ticker="KXBTCD-26AUG2520-T78599.99",
            floor_strike=78599.99,
            yes_bid_dollars="0.62",
            yes_ask_dollars="0.63",
            no_bid_dollars="0.37",
            no_ask_dollars="0.38",
        )
        opps = scan_markets([_market(), other], self.spot, self.settings, self.now)
        chosen = pick_flex_entries(opps, working_plays={"impulse_wait"})
        self.assertEqual(len(chosen), 2)
        self.assertEqual([row.play for row in chosen], ["impulse_wait"] * 2)
        self.assertEqual(
            [row.ticker for row in chosen],
            ["KXBTCD-26AUG2520-T78699.99", "KXBTCD-26AUG2520-T78599.99"],
        )

    def test_working_coupon_memory_still_allows_other_rungs(self):
        # Live AUG2618 17:00 ET: T78499 YES rest was working, journal saw
        # T78399 / T78299 NO, scan opps stayed 0 because allow_swing treated
        # a live coupon like a one-ticker T.
        memory = SwingMemory(
            ticker="KXBTCD-26AUG2618-T78499.99",
            side="yes",
            dead=False,
            play="impulse_wait",
        )
        now = datetime(2026, 8, 26, 21, 3, tzinfo=timezone.utc)
        dump = SpotQuote(78428, "test", annual_vol=0.55, impulse=-160)
        other = _market(
            ticker="KXBTCD-26AUG2618-T78399.99",
            event_ticker="KXBTCD-26AUG2618",
            floor_strike=78399.99,
            yes_bid_dollars="0.60",
            yes_ask_dollars="0.61",
            no_bid_dollars="0.38",
            no_ask_dollars="0.39",
            open_time="2026-08-26T21:00:00Z",
            close_time="2026-08-26T22:00:00Z",
        )
        waits = evaluate_impulse_wait_market(other, dump, self.settings, now)
        self.assertTrue(waits)
        kept = apply_swing_memory(waits, memory)
        self.assertEqual([row.play for row in kept], ["impulse_wait"])
        self.assertEqual(kept[0].ticker, other.ticker)

    def test_flag_off_disables_the_rest(self):
        settings = Settings(playbook="flex", max_contracts=1, allow_maker=True, impulse_wait=False)
        self.assertEqual(evaluate_impulse_wait_market(_market(), self.spot, settings, self.now), [])

    def test_after_a_loss_skip_hour_sits_out_wait_and_taker(self):
        # Paper AUG2605/2606 same-dir taker stops were the live leak.
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
        taker_settings = Settings(playbook="flex", max_contracts=1, allow_maker=True, impulse_taker=True)
        wait_only = apply_swing_memory(
            scan_markets([wait_mkt], self.spot, self.settings, now),
            None,
            session,
        )
        self.assertEqual(wait_only, [])
        taker = apply_swing_memory(
            scan_markets([atm], dump, taker_settings, now),
            None,
            session,
        )
        self.assertEqual(taker, [])

    def test_store_rebuild_allows_dump_wait_two_hours_after_the_loss(self):
        wait_mkt = _market(
            ticker="KXBTCD-26AUG2604-T78899.99",
            event_ticker="KXBTCD-26AUG2604",
            open_time="2026-08-26T07:00:00Z",
            close_time="2026-08-26T08:00:00Z",
        )
        now = datetime(2026, 8, 26, 7, 20, tzinfo=timezone.utc)
        fill = {
            "ticker": "KXBTCD-26AUG2602-T78499.99",
            "event_ticker": "KXBTCD-26AUG2602",
            "side": "no",
            "price": 0.25,
            "count": 10,
            "fee": 0.0,
            "cost": 2.5,
            "mode": "paper",
            "taker": False,
            "model_p": 0.33,
            "if_win_roi": 3.0,
            "expected_roi": 0.33,
            "status": "closed",
            "result": "t_wait_stop",
            "pnl": -2.2204,
            "raw": {"play": "impulse_wait"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = store_mod.Store(Path(tmp) / "t.sqlite")
            db.record_trade(fill)
            session = refresh_session(db.session_memory(), "KXBTCD-26AUG2604")
        self.assertFalse(session.skip_next)
        waits = apply_swing_memory(
            scan_markets([wait_mkt], self.spot, self.settings, now),
            None,
            session,
        )
        self.assertTrue(waits)
        self.assertEqual(waits[0].play, "impulse_wait")
        self.assertEqual(waits[0].side, "no")

    def test_skip_hour_taker_loss_does_not_chain_another_skip(self):
        # AUG2518: after a loss, skip wait next hour; same-dir taker still ok.
        # AUG2605/AUG2606: that taker lost on the skip hour and re-armed skip,
        # so the dump coupon never got a live hour. Sit-out is one hour.
        first = remember_session_exit(SessionMemory(), "KXBTCD-26AUG2605", "t_stop", -1.24, "no")
        skip = refresh_session(first, "KXBTCD-26AUG2606")
        self.assertTrue(skip.skip_next)
        self.assertEqual(skip.skipped_event, "KXBTCD-26AUG2606")
        chained = remember_session_exit(skip, "KXBTCD-26AUG2606", "t_stop", -0.65, "no")
        self.assertEqual(chained.last_loss_event, "KXBTCD-26AUG2606")
        self.assertFalse(chained.skip_next)
        still_this_hour = refresh_session(chained, "KXBTCD-26AUG2606")
        live = refresh_session(chained, "KXBTCD-26AUG2607")
        self.assertFalse(live.skip_next)
        this_hour_waits = apply_swing_memory(
            scan_markets(
                [_market(
                    ticker="KXBTCD-26AUG2606-T78699.99",
                    event_ticker="KXBTCD-26AUG2606",
                    open_time="2026-08-26T09:00:00Z",
                    close_time="2026-08-26T10:00:00Z",
                )],
                self.spot,
                self.settings,
                datetime(2026, 8, 26, 9, 40, tzinfo=timezone.utc),
            ),
            None,
            still_this_hour,
        )
        self.assertEqual(this_hour_waits, [])
        wait_mkt = _market(
            ticker="KXBTCD-26AUG2607-T78699.99",
            event_ticker="KXBTCD-26AUG2607",
            open_time="2026-08-26T10:00:00Z",
            close_time="2026-08-26T11:00:00Z",
        )
        now = datetime(2026, 8, 26, 10, 20, tzinfo=timezone.utc)
        waits = apply_swing_memory(
            scan_markets([wait_mkt], self.spot, self.settings, now),
            None,
            live,
        )
        self.assertTrue(waits)
        self.assertEqual(waits[0].play, "impulse_wait")

    def test_store_rebuild_skip_hour_loss_allows_the_next_coupon(self):
        wait_skip = _market(
            ticker="KXBTCD-26AUG2606-T78699.99",
            event_ticker="KXBTCD-26AUG2606",
            open_time="2026-08-26T09:00:00Z",
            close_time="2026-08-26T10:00:00Z",
        )
        wait_live = _market(
            ticker="KXBTCD-26AUG2607-T78699.99",
            event_ticker="KXBTCD-26AUG2607",
            open_time="2026-08-26T10:00:00Z",
            close_time="2026-08-26T11:00:00Z",
        )
        first = {
            "ticker": "KXBTCD-26AUG2605-T78699.99",
            "event_ticker": "KXBTCD-26AUG2605",
            "side": "no",
            "price": 0.49,
            "count": 10,
            "fee": 0.175,
            "cost": 5.075,
            "mode": "paper",
            "taker": True,
            "model_p": 0.56,
            "if_win_roi": 0.97,
            "expected_roi": 0.11,
            "status": "closed",
            "result": "t_stop",
            "pnl": -1.243,
            "raw": {"play": "impulse_t"},
        }
        skip_loss = {
            **first,
            "ticker": "KXBTCD-26AUG2606-T78499.99",
            "event_ticker": "KXBTCD-26AUG2606",
            "price": 0.46,
            "pnl": -0.6455,
        }
        now_skip = datetime(2026, 8, 26, 9, 20, tzinfo=timezone.utc)
        now_live = datetime(2026, 8, 26, 10, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db = store_mod.Store(Path(tmp) / "t.sqlite")
            db.record_trade(first)
            db.record_trade(skip_loss)
            mem = db.session_memory()
        skip = refresh_session(mem, "KXBTCD-26AUG2606")
        live = refresh_session(mem, "KXBTCD-26AUG2607")
        self.assertEqual(skip.last_loss_event, "KXBTCD-26AUG2606")
        self.assertFalse(skip.skip_next)
        self.assertFalse(live.skip_next)
        self.assertEqual(
            apply_swing_memory(
                scan_markets([wait_skip], self.spot, self.settings, now_skip),
                None,
                skip,
            ),
            [],
        )
        live_waits = apply_swing_memory(
            scan_markets([wait_live], self.spot, self.settings, now_live),
            None,
            live,
        )
        self.assertTrue(live_waits)
        self.assertEqual(live_waits[0].play, "impulse_wait")

    def test_rally_rests_yes_under_the_forty_cent_book(self):
        rally = SpotQuote(78800, "test", annual_vol=0.55, impulse=160)
        market = _market(
            yes_bid_dollars="0.40",
            yes_ask_dollars="0.41",
            no_bid_dollars="0.59",
            no_ask_dollars="0.60",
        )
        opps = evaluate_impulse_wait_market(market, rally, self.settings, self.now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].side, "yes")
        self.assertAlmostEqual(opps[0].ask, 0.41)
        self.assertAlmostEqual(opps[0].limit_price, 0.25)
        self.assertFalse(opps[0].taker)

    def test_tape_at_rest_counts_only_the_hitting_side(self):
        yes_hit = _print("yes", 0.25, 4)
        yes_lift = _print("yes", 0.25, 9, book="bid")
        no_hit = _print("no", 0.25, 3)
        no_wrong = _print("no", 0.25, 8, book="ask")
        far = _print("yes", 0.96, 100)
        block = _print("no", 0.25, 12, is_block_trade=True)
        old = _print("no", 0.25, 5, created_time="2026-08-25T22:00:00Z")
        derived = {"yes_price_dollars": "0.2500", "taker_book_side": "ask", "count_fp": "2.00"}
        self.assertAlmostEqual(tape_at_rest([yes_hit, far], "yes", 0.25), 4)
        self.assertAlmostEqual(tape_at_rest([yes_lift], "yes", 0.25), 0)
        self.assertAlmostEqual(tape_at_rest([no_hit, no_wrong, block], "no", 0.25), 3)
        self.assertAlmostEqual(tape_at_rest([derived], "yes", 0.25), 2)
        since = datetime(2026, 8, 25, 23, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(tape_at_rest([old, no_hit], "no", 0.25, since=since), 3)

    def test_fade_is_not_a_flip_but_opposite_impulse_is(self):
        self.assertFalse(impulse_wait_flipped("no", -20, self.settings))
        self.assertFalse(impulse_wait_flipped("no", -160, self.settings))
        self.assertTrue(impulse_wait_flipped("no", 160, self.settings))
        self.assertFalse(impulse_wait_flipped("yes", 20, self.settings))
        self.assertTrue(impulse_wait_flipped("yes", -160, self.settings))

    def test_weak_up_is_quiet_no_not_yes(self):
        self.assertEqual(coupon_sides(160, self.settings), ["yes"])
        self.assertEqual(coupon_sides(100, self.settings), ["yes"])
        self.assertEqual(coupon_sides(80, self.settings), ["no"])
        self.assertEqual(coupon_sides(6, self.settings), ["no"])
        self.assertEqual(coupon_sides(0, self.settings), ["no"])
        self.assertEqual(coupon_sides(-3, self.settings), ["no"])
        self.assertEqual(coupon_sides(-160, self.settings), ["no"])
        self.assertTrue(impulse_wait_wrong_side("yes", 30, self.settings))
        self.assertTrue(impulse_wait_wrong_side("yes", -16, self.settings))
        self.assertFalse(impulse_wait_wrong_side("yes", 160, self.settings))
        self.assertFalse(impulse_wait_wrong_side("no", 80, self.settings))
        self.assertFalse(impulse_wait_wrong_side("no", -20, self.settings))
        self.assertTrue(impulse_wait_wrong_side("no", 160, self.settings))

    def test_minute_high_counts_as_a_maker_fill(self):
        self.assertFalse(wait_book_crossed("no", 0.25, 0.32))
        self.assertTrue(
            wait_book_crossed("no", 0.25, 0.32, yes_bid_high=0.76, impulse=-160, min_impulse=100)
        )
        self.assertTrue(wait_book_crossed("no", 0.25, 0.24, impulse=-160, min_impulse=100))
        self.assertTrue(
            wait_book_crossed("yes", 0.25, 0.32, yes_ask_low=0.24, impulse=160, min_impulse=100)
        )
        self.assertFalse(
            wait_book_crossed("yes", 0.25, 0.25, impulse=90, min_impulse=100)
        )

    def test_bounce_does_not_fill_a_dump_rest(self):
        self.assertFalse(wait_book_crossed("no", 0.25, 0.14, yes_bid_high=0.86, impulse=95))
        self.assertFalse(wait_book_crossed("no", 0.25, 0.24, impulse=80))

    def test_fade_ask_print_does_not_fill(self):
        # AUG2604 07:41: rest sat 31 min, spot already +$42, ask finally printed 25¢.
        self.assertFalse(wait_book_crossed("no", 0.25, 0.25, impulse=-3, min_impulse=100))
        self.assertFalse(
            wait_book_crossed("no", 0.25, 0.32, yes_bid_high=0.76, impulse=-40, min_impulse=100)
        )

    def test_already_through_rest_does_not_fill(self):
        # AUG2802 01:56: T79599/T79499 NO rest 0.25, spot already through the
        # strikes, NO ask 0.03–0.04, impulse finally −$100. Filling at 0.25
        # then t_wait_stop is not ask==rest.
        self.assertFalse(wait_book_crossed("no", 0.25, 0.03, impulse=-100, min_impulse=100))
        self.assertFalse(wait_book_crossed("no", 0.25, 0.10, impulse=-120, min_impulse=100))
        self.assertFalse(
            wait_book_crossed("no", 0.25, 0.32, yes_bid_high=0.97, impulse=-120, min_impulse=100)
        )
        self.assertTrue(wait_book_crossed("no", 0.25, 0.25, impulse=-100, min_impulse=100))
        self.assertTrue(wait_book_crossed("no", 0.25, 0.24, impulse=-100, min_impulse=100))

    def test_five_pm_daily_label_is_the_hourly_when_47_minutes_left(self):
        # 16:13 ET → next close is 17:00. Kalshi tags that print daily / ~25h life.
        now = datetime(2026, 8, 26, 20, 13, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2617-T78499.99",
            event_ticker="KXBTCD-26AUG2617",
            floor_strike=78499.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-25T20:00:00Z",
            close_time="2026-08-26T21:00:00Z",
        )
        self.assertFalse(is_fast_window(coupon.open_time, coupon.close_time))
        self.assertTrue(is_coupon_window(47 * 60, self.settings))
        spot = SpotQuote(78468, "test", annual_vol=0.55, impulse=-160)
        opps = evaluate_impulse_wait_market(coupon, spot, self.settings, now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].play, "impulse_wait")
        self.assertAlmostEqual(opps[0].limit_price, 0.25)
        self.assertAlmostEqual(opps[0].ask, 0.36)

    def test_same_daily_book_does_not_rest_with_three_hours_left(self):
        now = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2617-T78499.99",
            event_ticker="KXBTCD-26AUG2617",
            floor_strike=78499.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-25T20:00:00Z",
            close_time="2026-08-26T21:00:00Z",
        )
        spot = SpotQuote(78468, "test", annual_vol=0.55, impulse=-20)
        self.assertEqual(evaluate_impulse_wait_market(coupon, spot, self.settings, now), [])

    def test_fifteen_minute_book_is_not_the_hourly(self):
        now = datetime(2026, 8, 26, 20, 13, tzinfo=timezone.utc)
        market = _market(
            ticker="KXBTC15M-26AUG261615-T78455.56",
            event_ticker="KXBTC15M-26AUG261615",
            floor_strike=78455.56,
            yes_bid_dollars="0.61",
            yes_ask_dollars="0.62",
            no_bid_dollars="0.37",
            no_ask_dollars="0.38",
            open_time="2026-08-26T20:00:00Z",
            close_time="2026-08-26T20:15:00Z",
        )
        spot = SpotQuote(78468, "test", annual_vol=0.55, impulse=-20)
        self.assertTrue(is_fast_window(market.open_time, market.close_time))
        self.assertEqual(evaluate_impulse_wait_market(market, spot, self.settings, now), [])

    def test_daily_rung_218_away_rests_under_the_250_cap(self):
        now = datetime(2026, 8, 26, 20, 13, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2617-T78249.99",
            event_ticker="KXBTCD-26AUG2617",
            floor_strike=78249.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-25T20:00:00Z",
            close_time="2026-08-26T21:00:00Z",
        )
        spot = SpotQuote(78468, "test", annual_vol=0.55, impulse=-160)
        self.assertAlmostEqual(abs(78249.99 - 78468), 218.01)
        tight = Settings(playbook="flex", max_contracts=1, allow_maker=True, impulse_wait_max_distance=150)
        self.assertEqual(evaluate_impulse_wait_market(coupon, spot, tight, now), [])
        opps = evaluate_impulse_wait_market(coupon, spot, self.settings, now)
        self.assertTrue(opps)
        self.assertAlmostEqual(opps[0].limit_price, 0.25)

    def test_daily_second_rung_36_cent_no_is_in_play(self):
        now = datetime(2026, 8, 26, 20, 13, tzinfo=timezone.utc)
        coupon = _market(
            ticker="KXBTCD-26AUG2617-T77999.99",
            event_ticker="KXBTCD-26AUG2617",
            floor_strike=77999.99,
            yes_bid_dollars="0.63",
            yes_ask_dollars="0.64",
            no_bid_dollars="0.35",
            no_ask_dollars="0.36",
            open_time="2026-08-25T20:00:00Z",
            close_time="2026-08-26T21:00:00Z",
        )
        spot = SpotQuote(78368, "test", annual_vol=0.90, impulse=-160)
        self.assertGreater(abs(77999.99 - 78368), 250)
        self.assertLess(abs(77999.99 - 78368), 600)
        opps = evaluate_impulse_wait_market(coupon, spot, self.settings, now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].side, "no")

    def test_five_pm_rally_yes_28_is_the_live_mid(self):
        # 16:42 ET AUG2617: $250 rungs skip 32-42 NO. Only mid is T78499 YES 0.28.
        now = datetime(2026, 8, 26, 20, 42, tzinfo=timezone.utc)
        market = _market(
            ticker="KXBTCD-26AUG2617-T78499.99",
            event_ticker="KXBTCD-26AUG2617",
            floor_strike=78499.99,
            yes_bid_dollars="0.27",
            yes_ask_dollars="0.28",
            no_bid_dollars="0.72",
            no_ask_dollars="0.73",
            open_time="2026-08-25T20:00:00Z",
            close_time="2026-08-26T21:00:00Z",
        )
        spot = SpotQuote(78423, "test", annual_vol=0.55, impulse=160)
        opps = evaluate_impulse_wait_market(market, spot, self.settings, now)
        self.assertTrue(opps)
        self.assertEqual(opps[0].side, "yes")
        self.assertAlmostEqual(opps[0].ask, 0.28)
        self.assertAlmostEqual(opps[0].limit_price, 0.25)
        self.assertFalse(opps[0].taker)
        knife = _market(
            ticker="KXBTCD-26AUG2617-T78249.99",
            event_ticker="KXBTCD-26AUG2617",
            floor_strike=78249.99,
            yes_bid_dollars="0.94",
            yes_ask_dollars="0.95",
            no_bid_dollars="0.04",
            no_ask_dollars="0.05",
            open_time="2026-08-25T20:00:00Z",
            close_time="2026-08-26T21:00:00Z",
        )
        self.assertEqual(evaluate_impulse_wait_market(knife, spot, self.settings, now), [])

    def test_sparse_daily_atm_mid_is_the_current_book(self):
        # Paper AUG2817 16:29 ET: $500 rungs, only T77499 is the book (YES 0.51).
        # The 0.42 hang cap skipped that mid for half an hour. Rest 0.25 under
        # it. Do not take 0.51. Far 0.98 is not the current book.
        now = datetime(2026, 8, 28, 20, 29, tzinfo=timezone.utc)
        atm = _market(
            ticker="KXBTCD-26AUG2817-T77499.99",
            event_ticker="KXBTCD-26AUG2817",
            floor_strike=77499.99,
            yes_bid_dollars="0.50",
            yes_ask_dollars="0.51",
            no_bid_dollars="0.49",
            no_ask_dollars="0.52",
            open_time="2026-08-27T21:00:00Z",
            close_time="2026-08-28T21:00:00Z",
        )
        locked = _market(
            ticker="KXBTCD-26AUG2817-T76999.99",
            event_ticker="KXBTCD-26AUG2817",
            floor_strike=76999.99,
            yes_bid_dollars="0.97",
            yes_ask_dollars="0.98",
            no_bid_dollars="0.03",
            no_ask_dollars="0.04",
            open_time="2026-08-27T21:00:00Z",
            close_time="2026-08-28T21:00:00Z",
        )
        punched = _market(
            ticker="KXBTCD-26AUG2817-T77999.99",
            event_ticker="KXBTCD-26AUG2817",
            floor_strike=77999.99,
            yes_bid_dollars="0.02",
            yes_ask_dollars="0.03",
            no_bid_dollars="0.98",
            no_ask_dollars="0.99",
            open_time="2026-08-27T21:00:00Z",
            close_time="2026-08-28T21:00:00Z",
        )
        spot = SpotQuote(77500.16, "test", annual_vol=0.55, impulse=23)
        opps = scan_markets([atm, locked, punched], spot, self.settings, now)
        waits = [row for row in opps if row.play == "impulse_wait"]
        self.assertEqual(waits, [])
        dump = SpotQuote(77500.16, "test", annual_vol=0.55, impulse=-160)
        self.assertEqual(
            [row for row in scan_markets([atm, locked, punched], dump, self.settings, now) if row.play == "impulse_wait"],
            [],
        )


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
                at_rest = _market(
                    yes_bid_dollars="0.75",
                    yes_ask_dollars="0.76",
                    no_bid_dollars="0.24",
                    no_ask_dollars="0.25",
                )
                promoted = refresh_working(db, self.settings, [at_rest], self.spot, self.now)
                self.assertEqual(promoted[0]["status"], "open")
                row = db.open_trades()[0]
                self.assertEqual(row["id"], trade_id)
                self.assertAlmostEqual(row["price"], 0.25)
                self.assertEqual(row["taker"], 0)
                self.assertAlmostEqual(row["fee"], 0.0)
                self.assertEqual(still, [])

    def test_quote_touch_without_tape_is_not_a_fill(self):
        settings = Settings(playbook="flex", max_contracts=10, allow_maker=True)
        opp = evaluate_impulse_wait_market(_market(), self.spot, settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                at_rest = _market(
                    yes_bid_dollars="0.75",
                    yes_ask_dollars="0.76",
                    no_bid_dollars="0.24",
                    no_ask_dollars="0.25",
                )
                missed = refresh_working(
                    db, settings, [at_rest], self.spot, self.now, tapes={opp.ticker: []}
                )
                self.assertEqual(missed, [])
                self.assertEqual(len(db.working_trades()), 1)
                self.assertEqual(db.open_trades(), [])
                raw = json.loads(db.working_trades()[0]["raw"])
                self.assertEqual(raw.get("tape_at_rest"), 0)

    def test_promotes_only_the_printed_size(self):
        settings = Settings(playbook="flex", max_contracts=10, allow_maker=True)
        opp = evaluate_impulse_wait_market(_market(), self.spot, settings, self.now)[0]
        fill = paper_fill(opp)
        self.assertAlmostEqual(fill["count"], 10)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                at_rest = _market(
                    yes_bid_dollars="0.75",
                    yes_ask_dollars="0.76",
                    no_bid_dollars="0.24",
                    no_ask_dollars="0.25",
                )
                prints = [_print("no", 0.25, 3)]
                promoted = refresh_working(
                    db, settings, [at_rest], self.spot, self.now, tapes={opp.ticker: prints}
                )
                self.assertEqual(promoted[0]["status"], "open")
                self.assertAlmostEqual(promoted[0]["count"], 3)
                row = db.open_trades()[0]
                self.assertAlmostEqual(row["count"], 3)
                self.assertAlmostEqual(row["price"], 0.25)
                self.assertAlmostEqual(row["cost"], 0.75)
                self.assertEqual(row["taker"], 0)

    def test_cancels_when_the_book_is_already_through_rest(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                through = _market(
                    yes_bid_dollars="0.77",
                    yes_ask_dollars="0.78",
                    no_bid_dollars="0.22",
                    no_ask_dollars="0.23",
                )
                missed = refresh_working(db, self.settings, [through], self.spot, self.now)
                self.assertEqual(missed[0]["reason"], "wait_through")
                self.assertEqual(db.working_trades(), [])
                self.assertEqual(db.open_trades(), [])

    def test_cancels_paper_yes_when_the_tape_is_no_longer_a_rally(self):
        rally = SpotQuote(78800, "test", annual_vol=0.55, impulse=160)
        market = _market(
            yes_bid_dollars="0.36",
            yes_ask_dollars="0.37",
            no_bid_dollars="0.63",
            no_ask_dollars="0.64",
        )
        opp = evaluate_impulse_wait_market(market, rally, self.settings, self.now)[0]
        self.assertEqual(opp.side, "yes")
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                quiet = SpotQuote(78800, "test", annual_vol=0.55, impulse=30)
                updates = refresh_working(db, self.settings, [market], quiet, self.now)
                self.assertEqual(updates[0]["reason"], "wait_wrong_side")
                self.assertEqual(db.working_trades(), [])

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

    def test_does_not_promote_when_fade_prints_the_rest(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                faded = SpotQuote(78987, "test", annual_vol=0.55, impulse=-3)
                printed = _market(
                    yes_bid_dollars="0.75",
                    yes_ask_dollars="0.76",
                    no_bid_dollars="0.24",
                    no_ask_dollars="0.25",
                )
                updates = refresh_working(db, self.settings, [printed], faded, self.now)
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)
                self.assertEqual(db.open_trades(), [])

    def test_does_not_promote_on_a_bounce_print(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                bounce = SpotQuote(78910, "test", annual_vol=0.55, impulse=80)
                printed = _market(
                    yes_bid_dollars="0.75",
                    yes_ask_dollars="0.76",
                    no_bid_dollars="0.24",
                    no_ask_dollars="0.25",
                )
                updates = refresh_working(db, self.settings, [printed], bounce, self.now)
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)
                self.assertEqual(db.open_trades(), [])

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

    def test_promotes_when_the_minute_wick_prints_rest(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = db.record_trade(fill)
                still_open = _market(
                    yes_bid_dollars="0.67",
                    yes_ask_dollars="0.68",
                    no_bid_dollars="0.31",
                    no_ask_dollars="0.32",
                )
                missed = refresh_working(db, self.settings, [still_open], self.spot, self.now)
                self.assertEqual(missed, [])
                wick = {opp.ticker: {"yes_bid_high": 0.76}}
                promoted = refresh_working(
                    db, self.settings, [still_open], self.spot, self.now, extremes=wick
                )
                self.assertEqual(promoted[0]["status"], "open")
                row = db.open_trades()[0]
                self.assertEqual(row["id"], trade_id)
                self.assertAlmostEqual(row["price"], 0.25)
                self.assertEqual(row["taker"], 0)

    def test_atm_mid_close_does_not_fill_without_a_rest_wick(self):
        opp = evaluate_impulse_wait_market(_market(), self.spot, self.settings, self.now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                mid = _market(
                    yes_bid_dollars="0.54",
                    yes_ask_dollars="0.55",
                    no_bid_dollars="0.44",
                    no_ask_dollars="0.45",
                )
                wick = {opp.ticker: {"yes_ask_low": 0.45, "yes_bid_high": 0.55}}
                updates = refresh_working(
                    db, self.settings, [mid], self.spot, self.now, extremes=wick
                )
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)
                self.assertEqual(db.open_trades(), [])

    def test_client_prints_also_cap_the_fill(self):
        settings = Settings(playbook="flex", max_contracts=10, allow_maker=True)
        opp = evaluate_impulse_wait_market(_market(), self.spot, settings, self.now)[0]
        fill = paper_fill(opp)

        class Fake:
            def market_trades(self, ticker, min_ts=None, limit=200):
                return [_print("no", 0.25, 2.5)]

        from btchour import engine as engine_mod

        engine_mod._TAPE_CACHE.clear()

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                at_rest = _market(
                    yes_bid_dollars="0.75",
                    yes_ask_dollars="0.76",
                    no_bid_dollars="0.24",
                    no_ask_dollars="0.25",
                )
                promoted = refresh_working(
                    db,
                    settings,
                    [at_rest],
                    self.spot,
                    self.now,
                    extremes={},
                    client=Fake(),
                )
                self.assertAlmostEqual(promoted[0]["count"], 2.5)
                self.assertAlmostEqual(db.open_trades()[0]["count"], 2.5)


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

    def test_dump_coupon_rest_then_dump_fill(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        strike = 78699.99
        bars = [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.65, "yes_bid": 0.64}}, impulse=0),
            ReplayBar(int(maturity - 1740), 78790, 0.55, {strike: {"yes_ask": 0.64, "yes_bid": 0.63}}, impulse=-160),
            ReplayBar(
                int(maturity - 1680),
                78720,
                0.55,
                {strike: {"yes_ask": 0.65, "yes_bid": 0.64, "yes_bid_high": 0.76}},
                impulse=-180,
            ),
            ReplayBar(int(maturity - 1620), 78640, 0.55, {strike: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-120),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {strike: "no"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["play"], "impulse_wait")
        self.assertAlmostEqual(report["takes"][0]["ask"], 0.25)
        self.assertEqual(report["takes"][0]["exit_reason"], "t_clip")
        self.assertGreater(report["takes"][0]["pnl"], 0)

    def test_fade_keeps_the_bid_then_dump_reprint_fills(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        strike = 78699.99
        bars = [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.65, "yes_bid": 0.64}}, impulse=-112),
            ReplayBar(int(maturity - 1740), 78800, 0.55, {strike: {"yes_ask": 0.64, "yes_bid": 0.63}}, impulse=-87),
            ReplayBar(
                int(maturity - 1680),
                78840,
                0.55,
                {strike: {"yes_ask": 0.75, "yes_bid": 0.74, "yes_bid_high": 0.76}},
                impulse=-40,
            ),
            ReplayBar(
                int(maturity - 1620),
                78780,
                0.55,
                {strike: {"yes_ask": 0.65, "yes_bid": 0.64, "yes_bid_high": 0.76}},
                impulse=-160,
            ),
            ReplayBar(int(maturity - 1560), 78640, 0.55, {strike: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-120),
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
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.65, "yes_bid": 0.64}}, impulse=-112),
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

    def test_coupon_rest_beats_a_fifty_one_taker_on_the_same_dump(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = _close_ts("KXBTCD-26AUG2609")
        coupon = 78599.99
        taker = 78799.99
        bars = [
            ReplayBar(
                int(maturity - 1800),
                78680,
                0.55,
                {
                    coupon: {"yes_ask": 0.64, "yes_bid": 0.63},
                    taker: {"yes_ask": 0.50, "yes_bid": 0.49},
                },
                impulse=-211,
            ),
            ReplayBar(
                int(maturity - 1740),
                78640,
                0.55,
                {
                    coupon: {"yes_ask": 0.64, "yes_bid": 0.63, "yes_bid_high": 0.76},
                    taker: {"yes_ask": 0.49, "yes_bid": 0.48},
                },
                impulse=-180,
            ),
            ReplayBar(int(maturity - 1680), 78580, 0.55, {coupon: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-120),
        ]
        report = replay_bars("KXBTCD-26AUG2609", bars, {coupon: "no", taker: "yes"}, maturity, settings)
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["play"], "impulse_wait")
        self.assertEqual(report["takes"][0]["ticker"], f"KXBTCD-26AUG2609-T{coupon}")
        self.assertAlmostEqual(report["takes"][0]["ask"], 0.25)
        self.assertEqual(report["takes"][0]["exit_reason"], "t_clip")

    def test_dump_coupon_still_rests_after_a_taker_clip_on_replay(self):
        settings = Settings(
            playbook="flex",
            max_contracts=1,
            max_notional=10,
            allow_early_exit=True,
            impulse_taker=True,
        )
        maturity = _close_ts("KXBTCD-26AUG2611")
        taker = 78799.99
        coupon = 78599.99
        bars = [
            ReplayBar(
                int(maturity - 1800),
                78820,
                0.55,
                {taker: {"yes_ask": 0.26, "yes_bid": 0.25}},
                impulse=160,
            ),
            ReplayBar(
                int(maturity - 1740),
                78880,
                0.55,
                {taker: {"yes_ask": 0.26, "yes_bid": 0.24}},
                impulse=180,
            ),
            ReplayBar(
                int(maturity - 1680),
                78680,
                0.55,
                {coupon: {"yes_ask": 0.64, "yes_bid": 0.63}},
                impulse=-160,
            ),
            ReplayBar(
                int(maturity - 1620),
                78640,
                0.55,
                {coupon: {"yes_ask": 0.64, "yes_bid": 0.63, "yes_bid_high": 0.76}},
                impulse=-180,
            ),
            ReplayBar(int(maturity - 1560), 78580, 0.55, {coupon: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-80),
        ]
        report = replay_bars("KXBTCD-26AUG2611", bars, {taker: "yes", coupon: "no"}, maturity, settings)
        plays = [take["play"] for take in report["takes"]]
        self.assertIn("impulse_t", plays)
        self.assertIn("impulse_wait", plays)
        coupon_take = next(take for take in report["takes"] if take["play"] == "impulse_wait")
        self.assertEqual(coupon_take["ticker"], f"KXBTCD-26AUG2611-T{coupon}")
        self.assertAlmostEqual(coupon_take["ask"], 0.25)
        self.assertEqual(coupon_take["exit_reason"], "t_clip")
        self.assertGreater(coupon_take["pnl"], 0)

    def test_dump_coupon_still_rests_after_a_taker_stop_on_replay(self):
        # Same as the clip replay, but the YES taker stops. Session last_loss
        # used to eat the later dump coupon on that hour.
        settings = Settings(
            playbook="flex",
            max_contracts=1,
            max_notional=10,
            allow_early_exit=True,
            impulse_taker=True,
        )
        maturity = _close_ts("KXBTCD-26AUG2614")
        taker = 78799.99
        coupon = 78599.99
        bars = [
            ReplayBar(
                int(maturity - 1800),
                78820,
                0.55,
                {taker: {"yes_ask": 0.26, "yes_bid": 0.25}},
                impulse=160,
            ),
            ReplayBar(
                int(maturity - 1740),
                78740,
                0.55,
                {taker: {"yes_ask": 0.22, "yes_bid": 0.18}},
                impulse=40,
            ),
            ReplayBar(
                int(maturity - 1680),
                78680,
                0.55,
                {coupon: {"yes_ask": 0.64, "yes_bid": 0.63}},
                impulse=-160,
            ),
            ReplayBar(
                int(maturity - 1620),
                78640,
                0.55,
                {coupon: {"yes_ask": 0.64, "yes_bid": 0.63, "yes_bid_high": 0.76}},
                impulse=-180,
            ),
            ReplayBar(int(maturity - 1560), 78580, 0.55, {coupon: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-80),
        ]
        report = replay_bars("KXBTCD-26AUG2614", bars, {taker: "yes", coupon: "no"}, maturity, settings)
        plays = [take["play"] for take in report["takes"]]
        self.assertIn("impulse_t", plays)
        self.assertIn("impulse_wait", plays)
        self.assertEqual(report["takes"][0]["exit_reason"], "t_stop")
        coupon_take = next(take for take in report["takes"] if take["play"] == "impulse_wait")
        self.assertEqual(coupon_take["ticker"], f"KXBTCD-26AUG2614-T{coupon}")
        self.assertAlmostEqual(coupon_take["ask"], 0.25)
        self.assertEqual(coupon_take["exit_reason"], "t_clip")

    def test_zero_volume_wick_is_not_a_print(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        strike = 78699.99
        bars = [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.65, "yes_bid": 0.64}}, impulse=-160),
            ReplayBar(
                int(maturity - 1740),
                78720,
                0.55,
                {strike: {"yes_ask": 0.65, "yes_bid": 0.64, "yes_bid_high": 0.76, "volume": 0}},
                impulse=-180,
            ),
            ReplayBar(int(maturity - 1680), 78640, 0.55, {strike: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-120),
        ]
        report = replay_bars("KXBTCD-26AUG2520", bars, {strike: "no"}, maturity, settings)
        self.assertEqual(report["takes"], [])

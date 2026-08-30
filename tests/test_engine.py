from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from btchour import engine as engine_mod
from btchour import store as store_mod
from btchour.config import Settings
from btchour.kalshi import KalshiClient
from btchour.paper import paper_close, paper_fill, paper_settle
from btchour.strategy import Opportunity


class LedgerTests(unittest.TestCase):
    def test_paper_round_trip_hits_twenty_percent(self):
        opp = Opportunity(
            ticker="KXBTCD-26AUG2514-T78000",
            event_ticker="KXBTCD-26AUG2514",
            subtitle="$78,000 or above",
            side="yes",
            book_side="bid",
            strike=78000,
            spot=79200,
            seconds_left=1800,
            model_p=0.99,
            ask=0.81,
            max_price=0.82,
            limit_price=0.81,
            taker=True,
            b=0.21,
            if_win_roi=0.21,
            expected_roi=0.18,
            ev=0.18,
            fee=0.01,
            count=10,
            reason="test",
        )
        fill = paper_fill(opp)
        self.assertGreaterEqual(fill["if_win_roi"], 0.20)
        pnl = paper_settle(fill["cost"], fill["count"], "yes", "yes")
        self.assertGreater(pnl / fill["cost"], 0.20)
        self.assertLess(paper_settle(fill["cost"], fill["count"], "yes", "no"), 0)

    def test_store_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = db.record_trade(
                    {
                        "ticker": "KXBTCD-26AUG2513-T79099.99",
                        "event_ticker": "KXBTCD-26AUG2513",
                        "side": "yes",
                        "price": 0.82,
                        "count": 1,
                        "fee": 0.01,
                        "cost": 0.83,
                        "mode": "paper",
                        "taker": True,
                        "model_p": 0.99,
                        "if_win_roi": 0.20,
                        "expected_roi": 0.19,
                    }
                )
                db.settle_trade(trade_id, "yes", 0.17)
                summary = db.summary()
                self.assertEqual(summary["settled"], 1)
                self.assertEqual(summary["wins"], 1)
                self.assertAlmostEqual(summary["realized_pnl"], 0.17)

    def test_paper_close_and_store_count_early_exits(self):
        opp = Opportunity(
            ticker="KXBTCD-26AUG2514-T78000",
            event_ticker="KXBTCD-26AUG2514",
            subtitle="$78,000 or above",
            side="yes",
            book_side="bid",
            strike=78000,
            spot=79200,
            seconds_left=1800,
            model_p=0.70,
            ask=0.50,
            max_price=0.82,
            limit_price=0.50,
            taker=True,
            b=0.90,
            if_win_roi=0.90,
            expected_roi=0.15,
            ev=0.15,
            fee=0.02,
            count=1,
            reason="test",
            play="markout_scalp",
            lock_price=0.64,
        )
        fill = paper_fill(opp)
        closed = paper_close(fill, 0.70, "lock_on_book")
        self.assertGreaterEqual(closed["roi"], 0.20)
        self.assertEqual(closed["result"], "lock_on_book")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = db.record_trade(fill)
                db.close_trade(trade_id, closed["result"], closed["pnl"])
                summary = db.summary()
                self.assertEqual(summary["closed"], 1)
                self.assertEqual(summary["completed"], 1)
                self.assertEqual(summary["wins"], 1)
                self.assertGreater(summary["realized_pnl"], 0)


class SettingsTests(unittest.TestCase):
    def test_defaults_lock_twenty_percent(self):
        settings = Settings()
        self.assertEqual(settings.target_profit, 0.20)
        self.assertEqual(settings.min_ev, 0.20)
        self.assertEqual(settings.mode, "paper")
        self.assertFalse(settings.live)
        self.assertEqual(settings.playbook, "flex")
        self.assertEqual(settings.target_profit, 0.20)
        self.assertTrue(settings.allow_early_exit)
        self.assertTrue(settings.allow_maker)
        self.assertEqual(settings.swing_target, 0.10)
        self.assertEqual(settings.swing_max_clip, 0.50)
        self.assertTrue(settings.skip_after_loss)


class LoopGuardTests(unittest.TestCase):
    def test_scan_age_empty_and_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                self.assertIsNone(db.last_scan_at())
                self.assertIsNone(db.scan_age_seconds())
                db.record_scan("KXBTCD-26AUG2600", 79000, [])
                self.assertIsNotNone(db.last_scan_at())
                self.assertLess(db.scan_age_seconds(), 2)

    def test_bounded_cycle_raises_when_the_cycle_hangs(self):
        def hang(_client, _settings):
            time.sleep(2)
            return {}

        with patch.object(engine_mod, "run_cycle", hang):
            with self.assertRaises(TimeoutError):
                engine_mod._bounded_cycle(object(), Settings(), seconds=0.2)

    def test_paginate_stops_after_max_pages(self):
        class Fake(KalshiClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def get(self, path, params=None, signed=False):
                self.calls += 1
                return {"markets": [{"n": self.calls}], "cursor": "more"}

        client = Fake()
        items = client.paginate("/markets", "markets")
        self.assertEqual(client.calls, 10)
        self.assertEqual(len(items), 10)

    def test_minute_extremes_read_the_forming_candle(self):
        from datetime import datetime, timezone

        from btchour.kalshi import market_minute_extremes

        now = datetime(2026, 8, 28, 22, 18, 20, tzinfo=timezone.utc)
        minute_end = (int(now.timestamp()) // 60 + 1) * 60

        class Fake(KalshiClient):
            def candlesticks(self, ticker, start_ts, end_ts, period_interval=1, timeout=4):
                return [
                    {
                        "end_period_ts": minute_end,
                        "yes_ask": {"low_dollars": "0.24", "close_dollars": "0.32"},
                        "yes_bid": {"high_dollars": "0.76", "close_dollars": "0.68"},
                    }
                ]

        self.assertTrue(hasattr(KalshiClient(), "exchange_status"))
        self.assertEqual(
            market_minute_extremes(Fake(), "KXBTCD-26AUG2819-T77299.99", now),
            {"yes_ask_low": 0.24, "yes_bid_high": 0.76},
        )


class ExecuteWaitTests(unittest.TestCase):
    def _coupon(self) -> Opportunity:
        return Opportunity(
            ticker="KXBTCD-26AUG2617-T78499.99",
            event_ticker="KXBTCD-26AUG2617",
            subtitle="$78,499.99 or above",
            side="no",
            book_side="ask",
            strike=78499.99,
            spot=78492.97,
            seconds_left=3300,
            model_p=0.423,
            ask=0.40,
            max_price=0.42,
            limit_price=0.25,
            taker=False,
            b=3.0,
            if_win_roi=3.0,
            expected_roi=0.27,
            ev=0.27,
            fee=0.0,
            count=10,
            play="impulse_wait",
            reason="dump_gap NO 看见 0.40 rest 0.25",
        )

    def _lock_wait(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "event_ticker": "KXBTCD-26AUG2617",
            "side": "yes",
            "price": 0.83,
            "count": 1,
            "fee": 0.0,
            "cost": 0.83,
            "mode": "paper",
            "taker": False,
            "model_p": 0.999,
            "if_win_roi": 0.205,
            "expected_roi": 0.204,
            "status": "working",
            "raw": {"play": "lock_wait", "rest": 0.83},
        }

    def test_leftover_lock_waits_do_not_block_the_dump_coupon(self):
        # Paper AUG2617 16:04 ET: scan chose dump_gap NO 0.40, taken=1, then
        # _execute skipped because three leftover 0.83 lock_waits filled the cap.
        coupon = self._coupon()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                for ticker in (
                    "KXBTCD-26AUG2617-T75999.99",
                    "KXBTCD-26AUG2617-T76499.99",
                    "KXBTCD-26AUG2617-T76749.99",
                ):
                    db.record_trade(self._lock_wait(ticker))
                self.assertEqual(len(db.working_trades()), 3)
                filled = engine_mod._execute(coupon, object(), Settings(playbook="flex"), db)
                self.assertFalse(filled.get("skipped"))
                self.assertEqual(filled["ticker"], coupon.ticker)
                self.assertEqual(filled["status"], "working")
                self.assertAlmostEqual(filled["price"], 0.25)
                plays = [json.loads(row["raw"] or "{}").get("play") for row in db.working_trades()]
                self.assertEqual(plays.count("lock_wait"), 3)
                self.assertEqual(plays.count("impulse_wait"), 1)

    def test_second_and_third_dump_coupons_can_work(self):
        coupon = self._coupon()
        later = Opportunity(**{**coupon.__dict__, "ticker": "KXBTCD-26AUG2617-T78399.99"})
        third = Opportunity(**{**coupon.__dict__, "ticker": "KXBTCD-26AUG2617-T78199.99"})
        fourth = Opportunity(**{**coupon.__dict__, "ticker": "KXBTCD-26AUG2617-T78099.99"})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                first = engine_mod._execute(coupon, object(), Settings(playbook="flex"), db)
                self.assertFalse(first.get("skipped"))
                second = engine_mod._execute(later, object(), Settings(playbook="flex"), db)
                self.assertFalse(second.get("skipped"))
                self.assertEqual(second["status"], "working")
                again = engine_mod._execute(later, object(), Settings(playbook="flex"), db)
                self.assertTrue(again.get("skipped"))
                self.assertIn(again.get("reason"), {"already open", "ticker already working"})
                third_fill = engine_mod._execute(third, object(), Settings(playbook="flex"), db)
                self.assertFalse(third_fill.get("skipped"))
                fourth_fill = engine_mod._execute(fourth, object(), Settings(playbook="flex"), db)
                self.assertTrue(fourth_fill.get("skipped"))
                self.assertEqual(fourth_fill.get("reason"), "enough working coupons")

    def test_in_band_coupon_replaces_an_atm_mid_pad(self):
        pad = Opportunity(**{**self._coupon().__dict__, "ask": 0.61})
        pad_two = Opportunity(
            **{**pad.__dict__, "ticker": "KXBTCD-26AUG2617-T78399.99", "ask": 0.68}
        )
        pad_three = Opportunity(
            **{**pad.__dict__, "ticker": "KXBTCD-26AUG2617-T78199.99", "ask": 0.55}
        )
        coupon = Opportunity(
            **{**self._coupon().__dict__, "ticker": "KXBTCD-26AUG2617-T78099.99", "ask": 0.36}
        )
        settings = Settings(playbook="flex")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                self.assertFalse(engine_mod._execute(pad, object(), settings, db).get("skipped"))
                self.assertFalse(engine_mod._execute(pad_two, object(), settings, db).get("skipped"))
                self.assertFalse(engine_mod._execute(pad_three, object(), settings, db).get("skipped"))
                hung = engine_mod._execute(coupon, object(), settings, db)
                self.assertFalse(hung.get("skipped"))
                self.assertEqual(hung["ticker"], coupon.ticker)
                working = {(row["ticker"], row["side"]) for row in db.working_trades()}
                self.assertIn((coupon.ticker, "no"), working)
                self.assertNotIn((pad_two.ticker, "no"), working)
                self.assertEqual(len(working), 3)
                cancelled = [
                    row
                    for row in db.conn.execute("SELECT * FROM trades WHERE status = 'cancelled'")
                ]
                self.assertEqual(len(cancelled), 1)
                self.assertEqual(cancelled[0]["result"], "wait_replace")
                self.assertEqual(cancelled[0]["ticker"], pad_two.ticker)

    def test_same_ticker_opposite_side_can_work(self):
        no_rest = self._coupon()
        yes_rest = Opportunity(**{**no_rest.__dict__, "side": "yes", "book_side": "bid"})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                first = engine_mod._execute(no_rest, object(), Settings(playbook="flex"), db)
                self.assertFalse(first.get("skipped"))
                second = engine_mod._execute(yes_rest, object(), Settings(playbook="flex"), db)
                self.assertFalse(second.get("skipped"))
                self.assertEqual(second["status"], "working")
                again = engine_mod._execute(yes_rest, object(), Settings(playbook="flex"), db)
                self.assertTrue(again.get("skipped"))
                sides = {(row["ticker"], row["side"]) for row in db.working_trades()}
                self.assertEqual(
                    sides,
                    {(no_rest.ticker, "no"), (yes_rest.ticker, "yes")},
                )

    def test_entries_after_a_coupon_clip_do_not_hop(self):
        leftover = self._coupon()
        hop = Opportunity(**{**leftover.__dict__, "ticker": "KXBTCD-26AUG2617-T78699.99"})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                engine_mod._execute(leftover, object(), Settings(playbook="flex"), db)
                db.record_trade(
                    {
                        "ticker": hop.ticker,
                        "event_ticker": hop.event_ticker,
                        "side": "yes",
                        "price": 0.25,
                        "count": 10,
                        "fee": 0.0,
                        "cost": 2.5,
                        "mode": "paper",
                        "taker": False,
                        "model_p": 0.40,
                        "if_win_roi": 3.0,
                        "expected_roi": 0.6,
                        "status": "closed",
                        "result": "t_clip",
                        "pnl": 1.43,
                        "raw": {"play": "impulse_wait"},
                    }
                )
                chosen = engine_mod._entries_after_exits(
                    db, [hop.as_dict()], hop.event_ticker
                )
                self.assertEqual(chosen, [])

    def test_flex_cancels_lock_wait_on_the_far_5pm_daily(self):
        from datetime import datetime, timezone

        from btchour.kalshi import market_from_api
        from btchour.model import SpotQuote

        now = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
        daily = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2817-T70499.99",
                "event_ticker": "KXBTCD-26AUG2817",
                "title": "Bitcoin price",
                "subtitle": "$70,499.99 or above",
                "status": "active",
                "floor_strike": 70499.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.98",
                "yes_ask_dollars": "0.99",
                "no_bid_dollars": "0.01",
                "no_ask_dollars": "0.02",
                "open_time": "2026-08-26T20:00:00Z",
                "close_time": "2026-08-28T21:00:00Z",
                "result": "",
            }
        )
        lock = self._lock_wait("KXBTCD-26AUG2817-T70499.99")
        lock["event_ticker"] = "KXBTCD-26AUG2817"
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(lock)
                updates = engine_mod.refresh_working(
                    db,
                    Settings(playbook="flex"),
                    [daily],
                    SpotQuote(79743, "test", annual_vol=0.25),
                    now,
                )
                self.assertEqual(updates[0]["reason"], "wait_invalid")
                self.assertEqual(db.working_trades(), [])

    def test_flex_cancels_stale_impulse_wait_after_the_hour_closes(self):
        from datetime import datetime, timezone

        from btchour.kalshi import market_from_api
        from btchour.model import SpotQuote

        now = datetime(2026, 8, 28, 12, 1, tzinfo=timezone.utc)
        nxt = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2809-T79699.99",
                "event_ticker": "KXBTCD-26AUG2809",
                "title": "Bitcoin price",
                "subtitle": "$79,699.99 or above",
                "status": "active",
                "floor_strike": 79699.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.27",
                "yes_ask_dollars": "0.28",
                "no_bid_dollars": "0.72",
                "no_ask_dollars": "0.73",
                "open_time": "2026-08-28T11:00:00Z",
                "close_time": "2026-08-28T13:00:00Z",
                "result": "",
            }
        )
        stale = {
            "ticker": "KXBTCD-26AUG2808-T79599.99",
            "event_ticker": "KXBTCD-26AUG2808",
            "side": "no",
            "price": 0.25,
            "count": 10,
            "fee": 0.0,
            "cost": 2.5,
            "mode": "paper",
            "taker": False,
            "model_p": 0.47,
            "if_win_roi": 3.0,
            "expected_roi": 0.4,
            "status": "working",
            "raw": {"play": "impulse_wait", "rest": 0.25},
        }
        live = {
            **stale,
            "ticker": "KXBTCD-26AUG2809-T79699.99",
            "event_ticker": "KXBTCD-26AUG2809",
            "side": "yes",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(stale)
                db.record_trade(live)
                updates = engine_mod.refresh_working(
                    db,
                    Settings(playbook="flex"),
                    [nxt],
                    SpotQuote(79596, "test", annual_vol=0.25, impulse=160),
                    now,
                )
                self.assertEqual([row["ticker"] for row in updates], [stale["ticker"]])
                self.assertEqual(updates[0]["reason"], "wait_invalid")
                left = db.working_trades()
                self.assertEqual(len(left), 1)
                self.assertEqual(left[0]["ticker"], live["ticker"])

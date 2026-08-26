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

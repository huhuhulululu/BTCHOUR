from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from btchour import store as store_mod
from btchour.config import Settings
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
        self.assertTrue(settings.allow_early_exit)

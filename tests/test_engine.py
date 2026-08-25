from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from btchour import store as store_mod
from btchour.config import Settings
from btchour.paper import paper_fill, paper_settle
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


class SettingsTests(unittest.TestCase):
    def test_defaults_lock_twenty_percent(self):
        settings = Settings()
        self.assertEqual(settings.target_profit, 0.20)
        self.assertEqual(settings.mode, "paper")
        self.assertFalse(settings.live)

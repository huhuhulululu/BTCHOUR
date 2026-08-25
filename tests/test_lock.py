from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from btchour import store as store_mod
from btchour.config import Settings
from btchour.engine import refresh_working
from btchour.fees import fill_cost
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.paper import paper_fill, paper_settle
from btchour.strategy import evaluate_lock_market, evaluate_market


def _market(**overrides):
    base = {
        "ticker": "KXBTCD-26AUG2516-T78000",
        "event_ticker": "KXBTCD-26AUG2516",
        "title": "Bitcoin price",
        "subtitle": "$78,000 or above",
        "status": "active",
        "floor_strike": 78000,
        "strike_type": "greater",
        "yes_bid_dollars": "0.80",
        "yes_ask_dollars": "0.81",
        "no_bid_dollars": "0.19",
        "no_ask_dollars": "0.20",
        "open_time": "2026-08-25T19:00:00Z",
        "close_time": "2026-08-25T20:00:00Z",
        "result": "",
    }
    base.update(overrides)
    return market_from_api(base)


class LockStrategyTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(playbook="lock", max_contracts=1)
        self.now = datetime(2026, 8, 25, 19, 30, tzinfo=timezone.utc)
        self.spot = SpotQuote(79200, "test", annual_vol=0.55)

    def test_decided_cheap_ask_is_a_taker_lock(self):
        opps = evaluate_lock_market(_market(), self.spot, self.settings, self.now)
        yes = next(row for row in opps if row.side == "yes")
        self.assertEqual(yes.play, "lock_hold")
        self.assertTrue(yes.taker)
        self.assertGreaterEqual(yes.if_win_roi, 0.20)
        self.assertGreaterEqual(yes.ev, 0.20)
        self.assertGreaterEqual(yes.model_p, 0.998)

    def test_decided_expensive_ask_is_only_a_wait(self):
        market = _market(yes_bid_dollars="0.98", yes_ask_dollars="0.99", no_bid_dollars="0.01", no_ask_dollars="0.02")
        opps = evaluate_lock_market(market, self.spot, self.settings, self.now)
        yes = next(row for row in opps if row.side == "yes")
        self.assertEqual(yes.play, "lock_wait")
        self.assertFalse(yes.taker)
        self.assertEqual(yes.limit_price, 0.83)
        self.assertGreaterEqual(yes.if_win_roi, 0.20)

    def test_old_hold_ticket_at_95_percent_is_rejected(self):
        # ~1.7σ: this is the profile that lost when we treated p=95% as "locked".
        market = _market(
            ticker="KXBTCD-26AUG2516-T78450",
            floor_strike=78450,
            yes_bid_dollars="0.80",
            yes_ask_dollars="0.81",
            no_bid_dollars="0.19",
            no_ask_dollars="0.20",
        )
        lock = evaluate_lock_market(market, self.spot, self.settings, self.now)
        hold = evaluate_market(
            market,
            self.spot,
            Settings(playbook="hold", min_win_prob=0.95, allow_maker=False, max_contracts=1),
            self.now,
        )
        self.assertEqual(lock, [])
        self.assertTrue(any(row.side == "yes" for row in hold))

    def test_wait_is_not_a_paper_fill(self):
        market = _market(yes_bid_dollars="0.98", yes_ask_dollars="0.99", no_bid_dollars="0.01", no_ask_dollars="0.02")
        yes = next(row for row in evaluate_lock_market(market, self.spot, self.settings, self.now) if row.side == "yes")
        fill = paper_fill(yes)
        self.assertEqual(fill["status"], "working")

    def test_working_wait_promotes_only_when_ask_crosses(self):
        market = _market(yes_bid_dollars="0.98", yes_ask_dollars="0.99", no_bid_dollars="0.01", no_ask_dollars="0.02")
        yes = next(row for row in evaluate_lock_market(market, self.spot, self.settings, self.now) if row.side == "yes")
        fill = paper_fill(yes)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = db.record_trade(fill)
                still = refresh_working(db, self.settings, [market], self.spot, self.now)
                self.assertEqual(db.open_trades(), [])
                self.assertEqual(len(db.working_trades()), 1)
                crossed = _market(yes_bid_dollars="0.81", yes_ask_dollars="0.82", no_bid_dollars="0.18", no_ask_dollars="0.19")
                promoted = refresh_working(db, self.settings, [crossed], self.spot, self.now)
                self.assertEqual(promoted[0]["status"], "open")
                row = db.open_trades()[0]
                self.assertEqual(row["id"], trade_id)
                self.assertAlmostEqual(row["price"], 0.82)
                self.assertGreaterEqual(row["if_win_roi"], 0.20)
                self.assertGreaterEqual(paper_settle(row["cost"], row["count"], "yes", "yes") / row["cost"], 0.20)
                self.assertEqual(still, [])

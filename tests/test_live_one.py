from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from btchour import engine as engine_mod
from btchour import store as store_mod
from btchour.broker import (
    exchange_index_from_response,
    live_rest_one,
    order_fill_count,
    order_id_from_response,
)
from btchour.config import Settings
from btchour.engine import leftover_live_one_positions, reconcile_live_one, refresh_working
from btchour.exits import ExitAction
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.strategy import Opportunity


def _coupon(count: float = 10) -> Opportunity:
    return Opportunity(
        ticker="KXBTCD-26AUG2820-T77799.99",
        event_ticker="KXBTCD-26AUG2820",
        subtitle="$77,799.99 or above",
        side="yes",
        book_side="bid",
        strike=77799.99,
        spot=77750,
        seconds_left=3300,
        model_p=0.4,
        ask=0.37,
        max_price=0.70,
        limit_price=0.25,
        taker=False,
        b=3.0,
        if_win_roi=3.0,
        expected_roi=0.6,
        ev=0.6,
        fee=0.0,
        count=count,
        play="impulse_wait",
        reason="dump_gap YES 看见 0.37 rest 0.25",
        lock_price=0.32,
    )


def _signed() -> Settings:
    return Settings(playbook="flex", live_one=True, api_key_id="k", private_key_pem="p")


def _market():
    return market_from_api(
        {
            "ticker": "KXBTCD-26AUG2820-T77799.99",
            "event_ticker": "KXBTCD-26AUG2820",
            "title": "Bitcoin price",
            "subtitle": "$77,799.99 or above",
            "status": "active",
            "floor_strike": 77799.99,
            "strike_type": "greater",
            "yes_bid_dollars": "0.36",
            "yes_ask_dollars": "0.37",
            "no_bid_dollars": "0.63",
            "no_ask_dollars": "0.64",
            "open_time": "2026-08-28T23:00:00Z",
            "close_time": "2026-08-29T00:00:00Z",
            "result": "",
        }
    )


def _no_market(ask: str = "0.70"):
    return market_from_api(
        {
            "ticker": "KXBTCD-26AUG2918-T78099.99",
            "event_ticker": "KXBTCD-26AUG2918",
            "title": "Bitcoin price",
            "subtitle": "$78,099.99 or above",
            "status": "active",
            "floor_strike": 78099.99,
            "strike_type": "greater",
            "yes_bid_dollars": f"{float(ask) - 0.01:.2f}",
            "yes_ask_dollars": ask,
            "open_time": "2026-08-29T21:00:00Z",
            "close_time": "2026-08-29T22:00:00Z",
            "result": "",
        }
    )


class LiveRestHelpers(unittest.TestCase):
    def test_order_id_and_fill_count(self):
        self.assertEqual(order_id_from_response({"order_id": "a"}), "a")
        self.assertEqual(order_id_from_response({"order": {"order_id": "b"}}), "b")
        self.assertEqual(exchange_index_from_response({"order": {"exchange_index": 2}}), 2)
        self.assertAlmostEqual(order_fill_count({"fill_count_fp": "1.00"}), 1.0)
        self.assertAlmostEqual(order_fill_count({"fill_count": "0.00"}), 0.0)

    def test_live_rest_one_forces_one_contract_and_working(self):
        seen = {}

        class Fake:
            def create_order(self, **kwargs):
                seen.update(kwargs)
                return {"order_id": "ord-1", "status": "resting", "fill_count": "0.00"}

        trade = live_rest_one(Fake(), _coupon(10))
        self.assertEqual(seen["count"], 1.0)
        self.assertTrue(seen["post_only"])
        self.assertEqual(trade["status"], "working")
        self.assertEqual(trade["mode"], "paper")
        self.assertAlmostEqual(trade["count"], 1.0)
        self.assertTrue(trade["raw"]["live_one"])
        self.assertEqual(trade["raw"]["live_order_id"], "ord-1")
        self.assertEqual(trade["raw"]["exchange_index"], 2)


class LiveOneExecuteTests(unittest.TestCase):
    def test_clears_leftover_paper_bulk_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                paper = engine_mod._execute(_coupon(10), object(), Settings(playbook="flex"), db)
                self.assertAlmostEqual(paper["count"], 10)
                self.assertEqual(engine_mod.clear_paper_bulk_waits(db), [paper["id"]])
                self.assertEqual(db.working_trades(), [])

    def test_places_one_live_rest_and_refuses_a_second(self):
        placed = []

        class Fake:
            def create_order(self, **kwargs):
                placed.append(kwargs)
                return {"order_id": f"ord-{len(placed)}", "status": "resting", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                if status == "resting":
                    return (
                        [{"order_id": "ord-1", "ticker": _coupon().ticker, "status": "resting"}]
                        if placed
                        else []
                    )
                return []

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                first = engine_mod._execute(_coupon(10), Fake(), _signed(), db)
                self.assertFalse(first.get("skipped"))
                self.assertEqual(first["status"], "working")
                self.assertAlmostEqual(first["count"], 1.0)
                self.assertTrue(json.loads(db.working_trades()[0]["raw"])["live_one"])
                second = engine_mod._execute(
                    Opportunity(**{**_coupon().__dict__, "ticker": "KXBTCD-26AUG2820-T77899.99"}),
                    Fake(),
                    _signed(),
                    db,
                )
                self.assertTrue(second.get("skipped"))
                self.assertEqual(second["reason"], "already_one_live")
                self.assertEqual(len(placed), 1)

    def test_refuses_an_atm_pad_on_the_live_ticket(self):
        placed = []

        class Fake:
            def create_order(self, **kwargs):
                placed.append(kwargs)
                return {"order_id": "ord-pad", "status": "resting", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                return []

        pad = Opportunity(**{**_coupon().__dict__, "ask": 0.51})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                skipped = engine_mod._execute(pad, Fake(), _signed(), db)
                self.assertTrue(skipped.get("skipped"))
                self.assertEqual(skipped["reason"], "pad_not_live")
                self.assertEqual(placed, [])
                self.assertEqual(db.working_trades(), [])

    def test_unsigned_paper_still_allows_three_rests(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                settings = Settings(playbook="flex", live_one=True)
                first = engine_mod._execute(_coupon(), object(), settings, db)
                later = Opportunity(**{**_coupon().__dict__, "ticker": "KXBTCD-26AUG2820-T77899.99"})
                third = Opportunity(**{**_coupon().__dict__, "ticker": "KXBTCD-26AUG2820-T77699.99"})
                self.assertFalse(first.get("skipped"))
                self.assertFalse(engine_mod._execute(later, object(), settings, db).get("skipped"))
                self.assertFalse(engine_mod._execute(third, object(), settings, db).get("skipped"))
                self.assertEqual(len(db.working_trades()), 3)


class LiveOneRefreshTests(unittest.TestCase):
    def test_promotes_only_when_the_exchange_fills(self):
        now = datetime(2026, 8, 28, 23, 20, tzinfo=timezone.utc)
        spot = SpotQuote(77750, "test", annual_vol=0.55, impulse=160)
        orders = [
            {
                "order_id": "ord-1",
                "ticker": _coupon().ticker,
                "status": "resting",
                "fill_count_fp": "0.00",
            }
        ]

        class Fake:
            def create_order(self, **kwargs):
                return {"order_id": "ord-1", "status": "resting", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                return list(orders)

            def cancel_order(self, order_id, market_ticker=None, exchange_index=None):
                orders.clear()
                return {"order_id": order_id}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(live_rest_one(Fake(), _coupon(10)))
                missed = refresh_working(
                    db, _signed(), [_market()], spot, now, extremes={}, client=Fake()
                )
                self.assertEqual(missed, [])
                self.assertEqual(len(db.working_trades()), 1)
                orders[0] = {
                    "order_id": "ord-1",
                    "ticker": _coupon().ticker,
                    "status": "executed",
                    "fill_count_fp": "1.00",
                }
                filled = refresh_working(
                    db, _signed(), [_market()], spot, now, extremes={}, client=Fake()
                )
                self.assertEqual(filled[0]["reason"], "live_fill")
                self.assertAlmostEqual(db.open_trades()[0]["count"], 1.0)

    def test_cancels_the_live_rest_when_the_book_is_through(self):
        now = datetime(2026, 8, 28, 23, 20, tzinfo=timezone.utc)
        spot = SpotQuote(77750, "test", annual_vol=0.55, impulse=160)
        cancelled = []

        class Fake:
            def create_order(self, **kwargs):
                return {"order_id": "ord-1", "status": "resting", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                return [{"order_id": "ord-1", "status": "resting", "fill_count_fp": "0.00"}]

            def cancel_order(self, order_id, market_ticker=None, exchange_index=None):
                cancelled.append(order_id)
                return {}

        through = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2820-T77799.99",
                "event_ticker": "KXBTCD-26AUG2820",
                "title": "Bitcoin price",
                "subtitle": "$77,799.99 or above",
                "status": "active",
                "floor_strike": 77799.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.22",
                "yes_ask_dollars": "0.23",
                "no_bid_dollars": "0.77",
                "no_ask_dollars": "0.78",
                "open_time": "2026-08-28T23:00:00Z",
                "close_time": "2026-08-29T00:00:00Z",
                "result": "",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(live_rest_one(Fake(), _coupon(10)))
                updates = refresh_working(
                    db, _signed(), [through], spot, now, extremes={}, client=Fake()
                )
                self.assertEqual(updates[0]["reason"], "wait_through")
                self.assertEqual(cancelled, ["ord-1"])
                self.assertEqual(db.working_trades(), [])

    def test_cancels_a_weak_up_yes_when_the_tape_is_not_a_rally(self):
        now = datetime(2026, 8, 28, 23, 20, tzinfo=timezone.utc)
        quiet = SpotQuote(77750, "test", annual_vol=0.55, impulse=-16)
        cancelled = []

        class Fake:
            def create_order(self, **kwargs):
                return {"order_id": "ord-1", "status": "resting", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                return [{"order_id": "ord-1", "status": "resting", "fill_count_fp": "0.00"}]

            def cancel_order(self, order_id, market_ticker=None, exchange_index=None):
                cancelled.append(order_id)
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(live_rest_one(Fake(), _coupon(1)))
                updates = refresh_working(
                    db, _signed(), [_market()], quiet, now, extremes={}, client=Fake()
                )
                self.assertEqual(updates[0]["reason"], "wait_wrong_side")
                self.assertEqual(cancelled, ["ord-1"])
                self.assertEqual(db.working_trades(), [])

    def test_cancels_a_live_pad_even_on_a_real_rally(self):
        now = datetime(2026, 8, 28, 23, 20, tzinfo=timezone.utc)
        rally = SpotQuote(77750, "test", annual_vol=0.55, impulse=160)
        cancelled = []

        class Fake:
            def create_order(self, **kwargs):
                return {"order_id": "ord-pad", "status": "resting", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                return [{"order_id": "ord-pad", "status": "resting", "fill_count_fp": "0.00"}]

            def cancel_order(self, order_id, market_ticker=None, exchange_index=None):
                cancelled.append(order_id)
                return {}

        pad = Opportunity(**{**_coupon().__dict__, "ask": 0.51})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(live_rest_one(Fake(), pad))
                updates = refresh_working(
                    db, _signed(), [_market()], rally, now, extremes={}, client=Fake()
                )
                self.assertEqual(updates[0]["reason"], "pad_not_live")
                self.assertEqual(cancelled, ["ord-pad"])
                self.assertEqual(db.working_trades(), [])

    def _live_no(self, db):
        return db.record_trade(
            {
                "ticker": "KXBTCD-26AUG2918-T78099.99",
                "event_ticker": "KXBTCD-26AUG2918",
                "side": "no",
                "price": 0.25,
                "count": 1.0,
                "fee": 0.0,
                "cost": 0.25,
                "mode": "paper",
                "taker": 0,
                "model_p": 0.47,
                "if_win_roi": 3.0,
                "expected_roi": 0.9,
                "status": "open",
                "raw": {
                    "live_one": True,
                    "live_order_id": "ord-378",
                    "exchange_index": 2,
                },
            }
        )

    def test_unfilled_live_flatten_does_not_paper_close(self):
        class Fake:
            def create_order(self, **kwargs):
                return {"order_id": "flat-miss", "fill_count": "0.00", "remaining_count": "0.00"}

        action = ExitAction(reason="t_clip", price=0.36, note="band")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = self._live_no(db)
                row = db.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
                missed = engine_mod._close_position(
                    row, action, Fake(), _signed(), db, market=_no_market()
                )
                self.assertTrue(missed.get("skipped"))
                self.assertEqual(missed.get("reason"), "flatten_unfilled")
                left = db.conn.execute("SELECT status, result FROM trades WHERE id = ?", (trade_id,)).fetchone()
                self.assertEqual(left["status"], "open")
                self.assertIsNone(left["result"])

    def test_live_flatten_books_the_exchange_fill_not_the_clip_mark(self):
        class Fake:
            def create_order(self, **kwargs):
                return {
                    "order_id": "flat-hit",
                    "fill_count": "1.00",
                    "average_fill_price": "0.5000",
                }

        action = ExitAction(reason="t_clip", price=0.36, note="band")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = self._live_no(db)
                row = db.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
                closed = engine_mod._close_position(
                    row, action, Fake(), _signed(), db, market=_no_market("0.50")
                )
                self.assertFalse(closed.get("skipped"))
                self.assertAlmostEqual(closed["exit_price"], 0.50)
                done = db.conn.execute(
                    "SELECT status, result, pnl, raw FROM trades WHERE id = ?", (trade_id,)
                ).fetchone()
                self.assertEqual(done["status"], "closed")
                self.assertEqual(done["result"], "t_clip")
                raw = json.loads(done["raw"])
                self.assertAlmostEqual(raw["exit_price"], 0.50)
                self.assertNotAlmostEqual(done["pnl"], 0.0938)

    def test_reconcile_flattens_a_closed_ledger_with_a_live_position(self):
        class Fake:
            def __init__(self):
                self.held = -1.0

            def positions(self):
                return {
                    "market_positions": [
                        {"ticker": "KXBTCD-26AUG2918-T78099.99", "position_fp": f"{self.held:.2f}"}
                    ]
                }

            def create_order(self, **kwargs):
                self.held = 0.0
                return {
                    "order_id": "flat-retry",
                    "fill_count": "1.00",
                    "average_fill_price": "0.5000",
                }

            def markets_by_event(self, event_ticker):
                return [_no_market("0.50")]

        fake = Fake()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = self._live_no(db)
                db.close_trade(trade_id, "t_clip", 0.0938)
                updates = reconcile_live_one(fake, db, _signed(), markets=[_no_market("0.50")])
                self.assertEqual(updates[0]["result"], "flatten_reconcile")
                row = db.conn.execute("SELECT pnl, raw FROM trades WHERE id = ?", (trade_id,)).fetchone()
                self.assertGreater(row["pnl"], 0.09)
                raw = json.loads(row["raw"])
                self.assertAlmostEqual(raw["exit_price"], 0.50)
                self.assertEqual(leftover_live_one_positions(fake, db), {})

    def test_reconcile_does_not_flatten_a_working_live_fill(self):
        class Fake:
            def __init__(self):
                self.orders_created = []

            def positions(self):
                return {
                    "market_positions": [
                        {"ticker": "KXBTCD-26AUG2918-T78099.99", "position_fp": "-1.00"}
                    ]
                }

            def create_order(self, **kwargs):
                self.orders_created.append(kwargs)
                return {
                    "order_id": "should-not",
                    "fill_count": "1.00",
                    "average_fill_price": "0.7500",
                }

            def markets_by_event(self, event_ticker):
                return [_no_market("0.75")]

        fake = Fake()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                trade_id = db.record_trade(
                    {
                        "ticker": "KXBTCD-26AUG2918-T78099.99",
                        "event_ticker": "KXBTCD-26AUG2918",
                        "side": "no",
                        "price": 0.25,
                        "count": 1.0,
                        "fee": 0.0,
                        "cost": 0.25,
                        "mode": "paper",
                        "taker": 0,
                        "model_p": 0.47,
                        "if_win_roi": 3.0,
                        "expected_roi": 0.9,
                        "status": "working",
                        "raw": {
                            "live_one": True,
                            "live_order_id": "ord-379",
                            "exchange_index": 2,
                            "live_status": "resting",
                            "live_fill": 0,
                        },
                    }
                )
                updates = reconcile_live_one(fake, db, _signed(), markets=[_no_market("0.75")])
                self.assertEqual(updates, [])
                self.assertEqual(fake.orders_created, [])
                left = db.conn.execute(
                    "SELECT status, result FROM trades WHERE id = ?", (trade_id,)
                ).fetchone()
                self.assertEqual(left["status"], "working")
                self.assertIsNone(left["result"])
                self.assertEqual(
                    leftover_live_one_positions(fake, db),
                    {"KXBTCD-26AUG2918-T78099.99": -1.0},
                )

    def test_leftover_live_blocks_a_second_live_one(self):
        class Fake:
            def positions(self):
                return {
                    "market_positions": [
                        {"ticker": "KXBTCD-26AUG2918-T78099.99", "position_fp": "-1.00"}
                    ]
                }

            def create_order(self, **kwargs):
                return {"order_id": "should-not", "fill_count": "0.00"}

            def orders(self, status=None, ticker=None, min_ts=None):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                self._live_no(db)
                db.conn.execute("UPDATE trades SET status = 'closed', result = 't_clip' WHERE id = 1")
                db.conn.commit()
                skipped = engine_mod._execute(_coupon(1), Fake(), _signed(), db)
                self.assertTrue(skipped.get("skipped"))
                self.assertEqual(skipped.get("reason"), "leftover_live")

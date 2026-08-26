from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from btchour import engine as engine_mod
from btchour import store as store_mod
from btchour.config import Settings
from btchour.engine import refresh_working
from btchour.kalshi import KalshiError, market_from_api, parse_exchange_status, read_exchange_status
from btchour.model import SpotQuote
from btchour.paper import paper_fill
from btchour.strategy import Opportunity, evaluate_impulse_wait_market


LIVE_STATUS = {
    "exchange_active": True,
    "trading_active": True,
    "intra_exchange_transfers_active": True,
    "exchange_index_statuses": [
        {
            "description": "Default",
            "exchange_active": True,
            "exchange_index": 0,
            "intra_exchange_transfers_active": True,
            "trading_active": True,
        },
        {
            "description": "Crypto",
            "exchange_active": True,
            "exchange_index": 2,
            "intra_exchange_transfers_active": True,
            "trading_active": True,
        },
    ],
}


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


class ParseExchangeStatusTests(unittest.TestCase):
    def test_top_level_only_uses_default_flags(self):
        parsed = parse_exchange_status({"exchange_active": True, "trading_active": True})
        self.assertTrue(parsed["can_trade"])
        self.assertEqual(parsed["description"], "default")
        self.assertEqual(parsed["index"], 0)

    def test_empty_payload_is_closed(self):
        parsed = parse_exchange_status({})
        self.assertFalse(parsed["can_trade"])
        self.assertFalse(parsed["exchange_active"])
        self.assertFalse(parsed["trading_active"])

    def test_crypto_pause_blocks_even_if_default_is_open(self):
        payload = {
            "exchange_active": True,
            "trading_active": True,
            "exchange_index_statuses": [
                {"description": "Default", "exchange_index": 0, "exchange_active": True, "trading_active": True},
                {"description": "Crypto", "exchange_index": 2, "exchange_active": True, "trading_active": False},
            ],
        }
        parsed = parse_exchange_status(payload)
        self.assertFalse(parsed["can_trade"])
        self.assertEqual(parsed["description"], "Crypto")
        self.assertEqual(parsed["index"], 2)

    def test_crypto_open_allows_trade(self):
        parsed = parse_exchange_status(LIVE_STATUS)
        self.assertTrue(parsed["can_trade"])
        self.assertEqual(parsed["description"], "Crypto")
        self.assertTrue(parsed["exchange_active"])
        self.assertTrue(parsed["trading_active"])

    def test_maintenance_blocks(self):
        parsed = parse_exchange_status({"exchange_active": False, "trading_active": False})
        self.assertFalse(parsed["can_trade"])

    def test_read_fails_closed_on_kalshi_error(self):
        class Down:
            def exchange_status(self):
                raise KalshiError("GET /exchange/status -> 503: paused", 503, "")

        parsed = read_exchange_status(Down())
        self.assertFalse(parsed["ok"])
        self.assertFalse(parsed["can_trade"])
        self.assertEqual(parsed["description"], "unreachable")


class ExchangeGateTests(unittest.TestCase):
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
            reason="dump_gap NO",
        )

    def test_execute_skips_when_exchange_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                filled = engine_mod._execute(
                    self._coupon(), object(), Settings(playbook="flex"), db, can_trade=False
                )
                self.assertTrue(filled.get("skipped"))
                self.assertEqual(filled.get("reason"), "exchange_not_trading")
                self.assertEqual(db.working_trades(), [])

    def test_paper_does_not_fill_a_crossed_rest_when_paused(self):
        now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        settings = Settings(playbook="flex", max_contracts=1, allow_maker=True)
        spot = SpotQuote(78800, "test", annual_vol=0.55, impulse=-160)
        opp = evaluate_impulse_wait_market(_market(), spot, settings, now)[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                crossed = _market(
                    yes_bid_dollars="0.77",
                    yes_ask_dollars="0.78",
                    no_bid_dollars="0.22",
                    no_ask_dollars="0.23",
                )
                updates = refresh_working(db, settings, [crossed], spot, now, can_trade=False)
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)
                self.assertEqual(db.open_trades(), [])

    def test_live_pause_leaves_working_orders_untouched(self):
        now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        settings = Settings(playbook="flex", mode="live", max_contracts=1, allow_maker=True)
        spot = SpotQuote(78800, "test", annual_vol=0.55, impulse=160)
        opp = evaluate_impulse_wait_market(
            _market(), SpotQuote(78800, "test", annual_vol=0.55, impulse=-160), Settings(playbook="flex"), now
        )[0]
        fill = paper_fill(opp)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_trade(fill)
                updates = refresh_working(db, settings, [_market()], spot, now, can_trade=False)
                self.assertEqual(updates, [])
                self.assertEqual(len(db.working_trades()), 1)

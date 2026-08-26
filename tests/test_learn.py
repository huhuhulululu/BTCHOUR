from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from btchour import store as store_mod
from btchour.config import Settings
from btchour.kalshi import market_from_api
from btchour.learn import diagnose_impulse, journal_line, merge_impulse, series_impulse, tape_impulse
from btchour.model import SpotQuote


class LearnTests(unittest.TestCase):
    def test_tape_impulse_uses_three_minute_print(self):
        now = datetime(2026, 8, 26, 0, 10, tzinfo=timezone.utc)
        points = [
            (now - timedelta(seconds=200), 78700.0),
            (now - timedelta(seconds=90), 78650.0),
            (now - timedelta(seconds=3), 78400.0),
        ]
        self.assertAlmostEqual(tape_impulse(points, now, 78400.0), -300.0)

    def test_series_impulse_uses_oldest_if_lookback_missing(self):
        ts = 1_000_000
        series = [{"t": ts - 70_000, "v": 79000.0}, {"t": ts - 1_000, "v": 78800.0}]
        self.assertAlmostEqual(series_impulse(series, 78800.0, ts), -200.0)

    def test_merge_keeps_larger_move(self):
        self.assertEqual(merge_impulse(-40.0, -303.0), -303.0)
        self.assertEqual(merge_impulse(20.0, -80.0), -80.0)

    def test_diagnose_explains_expensive_ask(self):
        now = datetime(2026, 8, 25, 20, 47, tzinfo=timezone.utc)
        market = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2517-T78499.99",
                "event_ticker": "KXBTCD-26AUG2517",
                "floor_strike": 78499.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.20",
                "yes_ask_dollars": "0.21",
                "no_bid_dollars": "0.79",
                "no_ask_dollars": "0.80",
                "open_time": "2026-08-25T20:00:00Z",
                "close_time": "2026-08-25T21:00:00Z",
            }
        )
        spot = SpotQuote(78200, "test", annual_vol=0.55, impulse=-250)
        report = diagnose_impulse([market], spot, Settings(), now)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["candidates"])
        self.assertTrue(any("ask" in reason for reason in report["candidates"][0]["reasons"]))

    def test_diagnose_marks_dump_wait_when_taker_is_blocked(self):
        now = datetime(2026, 8, 25, 23, 30, tzinfo=timezone.utc)
        market = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2520-T78699.99",
                "event_ticker": "KXBTCD-26AUG2520",
                "floor_strike": 78699.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.59",
                "yes_ask_dollars": "0.60",
                "no_bid_dollars": "0.40",
                "no_ask_dollars": "0.41",
                "open_time": "2026-08-25T23:00:00Z",
                "close_time": "2026-08-26T00:00:00Z",
            }
        )
        spot = SpotQuote(78800, "test", annual_vol=0.55, impulse=-160)
        report = diagnose_impulse([market], spot, Settings(playbook="flex"), now)
        self.assertEqual(report["status"], "wait")
        self.assertEqual(report["wait"], "KXBTCD-26AUG2520-T78699.99")
        self.assertGreaterEqual(report["wait_count"], 1)

    def test_diagnose_wait_picks_near_atm_not_cheapest_ask(self):
        now = datetime(2026, 8, 26, 5, 6, tzinfo=timezone.utc)
        far = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2602-T78599.99",
                "event_ticker": "KXBTCD-26AUG2602",
                "floor_strike": 78599.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.61",
                "yes_ask_dollars": "0.62",
                "no_bid_dollars": "0.37",
                "no_ask_dollars": "0.38",
                "open_time": "2026-08-26T05:00:00Z",
                "close_time": "2026-08-26T06:00:00Z",
            }
        )
        near = market_from_api(
            {
                "ticker": "KXBTCD-26AUG2602-T78699.99",
                "event_ticker": "KXBTCD-26AUG2602",
                "floor_strike": 78699.99,
                "strike_type": "greater",
                "yes_bid_dollars": "0.59",
                "yes_ask_dollars": "0.60",
                "no_bid_dollars": "0.39",
                "no_ask_dollars": "0.40",
                "open_time": "2026-08-26T05:00:00Z",
                "close_time": "2026-08-26T06:00:00Z",
            }
        )
        spot = SpotQuote(78689.70, "test", annual_vol=0.55, impulse=-104)
        report = diagnose_impulse([far, near], spot, Settings(playbook="flex"), now)
        self.assertEqual(report["status"], "wait")
        self.assertEqual(report["wait"], "KXBTCD-26AUG2602-T78699.99")
        self.assertEqual(report["wait_count"], 2)

    def test_store_journal_and_tape(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store_mod, "DATA_DIR", Path(tmp)):
                db = store_mod.Store(Path(tmp) / "t.sqlite")
                db.record_scan("KXBTCD-26AUG2521", 78700, [])
                db.conn.execute(
                    "UPDATE scans SET created_at = ? WHERE id = 1",
                    ((now - timedelta(seconds=180)).isoformat(),),
                )
                db.conn.commit()
                db.record_journal("KXBTCD-26AUG2521", 78400, -300, -300, "blocked", "ask 0.80>0.52")
                points = db.tape_points("KXBTCD-26AUG2521")
                self.assertEqual(len(points), 1)
                self.assertEqual(len(db.recent_journal(5)), 1)
                self.assertAlmostEqual(tape_impulse(points, now, 78400.0), -300.0, delta=1.0)

    def test_journal_line_skips_empty_placeholder(self):
        self.assertEqual(journal_line({"status": "no_impulse"}), "")
        self.assertNotIn("None", journal_line({"status": "blocked", "candidates": []}))

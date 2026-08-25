from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from btchour.tickers import format_event_ticker, is_hourly_window, parse_event_ticker, parse_market_ticker


class TickerTests(unittest.TestCase):
    def test_parse_2pm_edt(self):
        event = parse_event_ticker("KXBTCD-26AUG2514")
        self.assertEqual(event["hour_et"], 14)
        self.assertEqual(event["close_utc"].isoformat(), "2026-08-25T18:00:00+00:00")

    def test_parse_market_strike(self):
        market = parse_market_ticker("KXBTCD-26AUG2514-T79199.99")
        self.assertEqual(market["strike"], 79199.99)
        self.assertEqual(market["event_ticker"], "KXBTCD-26AUG2514")

    def test_round_trip(self):
        close = datetime(2026, 8, 25, 14, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(format_event_ticker(close), "KXBTCD-26AUG2514")

    def test_hourly_window(self):
        self.assertTrue(is_hourly_window("2026-08-25T17:00:00Z", "2026-08-25T18:00:00Z"))
        self.assertFalse(is_hourly_window("2026-08-25T17:00:00Z", "2026-08-25T21:00:00Z"))

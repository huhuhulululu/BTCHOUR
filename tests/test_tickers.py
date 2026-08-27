from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from btchour.catalog import current_hourly_events
from btchour.tickers import (
    format_et,
    format_event_ticker,
    is_hourly_window,
    next_event_ticker,
    next_hourly_close,
    next_session_event_ticker,
    parse_event_ticker,
    parse_market_ticker,
)


class TickerTests(unittest.TestCase):
    def test_format_et_uses_new_york(self):
        now = datetime(2026, 8, 26, 18, 47, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(format_et(now), "2026-08-26 14:47:00 EDT")

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

    def test_next_event_rolls_the_et_day(self):
        self.assertEqual(next_event_ticker("KXBTCD-26AUG2602"), "KXBTCD-26AUG2603")
        self.assertEqual(next_event_ticker("KXBTCD-26AUG2523"), "KXBTCD-26AUG2600")

    def test_next_session_is_the_next_whole_hour_close(self):
        et = ZoneInfo("America/New_York")
        at_416 = datetime(2026, 8, 26, 16, 13, tzinfo=et)
        self.assertEqual(next_hourly_close(at_416).hour, 17)
        self.assertEqual(next_session_event_ticker(at_416), "KXBTCD-26AUG2617")
        after_close = datetime(2026, 8, 26, 17, 0, 1, tzinfo=et)
        self.assertEqual(next_session_event_ticker(after_close), "KXBTCD-26AUG2618")

    def test_hourly_window(self):
        self.assertTrue(is_hourly_window("2026-08-25T17:00:00Z", "2026-08-25T18:00:00Z"))
        self.assertFalse(is_hourly_window("2026-08-25T17:00:00Z", "2026-08-25T21:00:00Z"))

    def test_focus_stays_on_the_live_hour_after_the_close_print(self):
        # Live 06:00 UTC 2026-08-26: Kalshi still listed AUG2602 (just closed) and AUG2603.
        # abs(close-now) preferred the dead hour for ~30 minutes.
        now = datetime(2026, 8, 26, 6, 0, 20, tzinfo=ZoneInfo("UTC"))
        events = [
            {"event_ticker": "KXBTCD-26AUG2602"},
            {"event_ticker": "KXBTCD-26AUG2603"},
        ]
        focus = current_hourly_events(events, now)
        self.assertEqual(focus[0]["event_ticker"], "KXBTCD-26AUG2603")

    def test_focus_stays_on_the_next_hour_when_it_is_not_listed_yet(self):
        now = datetime(2026, 8, 26, 6, 0, 20, tzinfo=ZoneInfo("UTC"))
        events = [{"event_ticker": "KXBTCD-26AUG2602"}]
        focus = current_hourly_events(events, now)
        self.assertEqual(focus[0]["event_ticker"], "KXBTCD-26AUG2603")

    def test_focus_does_not_hop_to_the_5pm_daily_overnight(self):
        now = datetime(2026, 8, 27, 1, 1, tzinfo=ZoneInfo("America/New_York"))
        events = [
            {"event_ticker": "KXBTCD-26AUG2701"},
            {"event_ticker": "KXBTCD-26AUG2717"},
            {"event_ticker": "KXBTCD-26AUG2817"},
        ]
        focus = current_hourly_events(events, now)
        self.assertEqual(focus[0]["event_ticker"], "KXBTCD-26AUG2702")
        self.assertNotEqual(focus[0]["event_ticker"], "KXBTCD-26AUG2717")

    def test_focus_is_the_5pm_book_at_416_even_among_later_dailies(self):
        now = datetime(2026, 8, 26, 16, 13, tzinfo=ZoneInfo("America/New_York"))
        events = [
            {"event_ticker": "KXBTCD-26AUG2617"},
            {"event_ticker": "KXBTCD-26AUG2717"},
            {"event_ticker": "KXBTCD-26AUG2817"},
        ]
        focus = current_hourly_events(events, now)
        self.assertEqual(focus[0]["event_ticker"], "KXBTCD-26AUG2617")

    def test_focus_rolls_to_6pm_after_the_5pm_close(self):
        now = datetime(2026, 8, 26, 17, 0, 1, tzinfo=ZoneInfo("America/New_York"))
        events = [
            {"event_ticker": "KXBTCD-26AUG2617"},
            {"event_ticker": "KXBTCD-26AUG2618"},
        ]
        focus = current_hourly_events(events, now)
        self.assertEqual(focus[0]["event_ticker"], "KXBTCD-26AUG2618")

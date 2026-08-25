from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
MONTH_ABBR = {v: k for k, v in MONTHS.items()}

EVENT_RE = re.compile(r"^(?P<series>KXBTCD)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})$")
MARKET_RE = re.compile(
    r"^(?P<event>KXBTCD-\d{2}[A-Z]{3}\d{4})-T(?P<strike>\d+(?:\.\d+)?)$"
)


def parse_event_ticker(ticker: str) -> dict:
    match = EVENT_RE.match(ticker)
    if not match:
        raise ValueError(f"not a KXBTCD hourly event ticker: {ticker}")
    year = 2000 + int(match.group("yy"))
    month = MONTHS[match.group("mon")]
    day = int(match.group("dd"))
    hour = int(match.group("hh"))
    close_et = datetime(year, month, day, hour, 0, 0, tzinfo=ET)
    return {
        "series": match.group("series"),
        "event_ticker": ticker,
        "close_et": close_et,
        "close_utc": close_et.astimezone(ZoneInfo("UTC")),
        "hour_et": hour,
    }


def parse_market_ticker(ticker: str) -> dict:
    match = MARKET_RE.match(ticker)
    if not match:
        raise ValueError(f"not a KXBTCD threshold market ticker: {ticker}")
    event = parse_event_ticker(match.group("event"))
    return {
        **event,
        "ticker": ticker,
        "strike": float(match.group("strike")),
    }


def format_event_ticker(close_et: datetime, series: str = "KXBTCD") -> str:
    local = close_et.astimezone(ET)
    return f"{series}-{str(local.year)[2:]}{MONTH_ABBR[local.month]}{local.day:02d}{local.hour:02d}"


def is_hourly_window(open_time: str | None, close_time: str | None) -> bool:
    if not open_time or not close_time:
        return False
    start = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
    end = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    seconds = (end - start).total_seconds()
    return 50 * 60 <= seconds <= 70 * 60

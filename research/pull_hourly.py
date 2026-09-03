"""Pull settled KXBTCD hourly history into a local sqlite for offline research.

Public endpoints only (no key material):

  GET /events?series_ticker=KXBTCD&status=settled   -> settled hourly events
  GET /events/{event_ticker}                        -> the whole strike ladder + result
  GET /live_data/events/{event_ticker}?range=1h     -> per-second BRTI (3h ending at close)
  GET /series/KXBTCD/markets/{ticker}/candlesticks   -> per-minute yes_bid / yes_ask / price

Settlement truth is the event's `expiration_value` (the 60-second BRTI average that
Kalshi actually settled on), not a spot proxy -- see catalog/rules/settlement.md.

    python3 research/pull_hourly.py --days 90 --workers 8

Writes `data/hourly.sqlite` (gitignored). Re-running is incremental: events already
stored with candles are skipped unless --refresh.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXBTCD"
UA = "btchour-research/0.1"
REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "data" / "hourly.sqlite"

SPOT_STEP_MS = 10_000  # store BRTI every 10 seconds

_print_lock = threading.Lock()


def log(*args) -> None:
    with _print_lock:
        print(*args, file=sys.stderr, flush=True)


def get(path: str, params: dict | None = None, tries: int = 5, timeout: int = 30):
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = BASE + path + (("?" + query) if query else "")
    delay = 1.0
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return None


# --------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_ticker TEXT PRIMARY KEY,
    open_ts      INTEGER NOT NULL,
    close_ts     INTEGER NOT NULL,
    settle_value REAL,
    n_markets    INTEGER,
    pulled_ts    INTEGER
);
CREATE TABLE IF NOT EXISTS markets (
    event_ticker TEXT NOT NULL,
    strike       REAL NOT NULL,
    ticker       TEXT NOT NULL,
    result       TEXT,
    volume       REAL,
    open_interest REAL,
    PRIMARY KEY (event_ticker, strike)
);
CREATE TABLE IF NOT EXISTS spot (
    event_ticker TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    value        REAL NOT NULL,
    PRIMARY KEY (event_ticker, ts)
);
CREATE TABLE IF NOT EXISTS quotes (
    event_ticker TEXT NOT NULL,
    strike       REAL NOT NULL,
    ts           INTEGER NOT NULL,           -- end_period_ts of the 1m candle
    yes_bid_open REAL, yes_bid_high REAL, yes_bid_low REAL, yes_bid_close REAL,
    yes_ask_open REAL, yes_ask_high REAL, yes_ask_low REAL, yes_ask_close REAL,
    price_close  REAL,
    volume       REAL,
    open_interest REAL,
    PRIMARY KEY (event_ticker, strike, ts)
);
CREATE INDEX IF NOT EXISTS quotes_by_event ON quotes (event_ticker, ts);
CREATE INDEX IF NOT EXISTS spot_by_event ON spot (event_ticker, ts);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# --------------------------------------------------------------------------- pull


def _ts(value) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _f(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def list_settled_events(days: int) -> list[dict]:
    """Newest-first settled events, trimmed to the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    cursor = None
    for _ in range(200):
        payload = get("/events", {"series_ticker": SERIES, "status": "settled", "limit": 200, "cursor": cursor})
        rows = (payload or {}).get("events") or []
        if not rows:
            break
        stop = False
        for row in rows:
            close = _ts(row.get("strike_date"))
            if close is None:
                continue
            if close < cutoff.timestamp():
                stop = True
                continue
            out.append({"event_ticker": row["event_ticker"], "close_ts": close})
        cursor = (payload or {}).get("cursor")
        if stop or not cursor:
            break
    return out


def pull_spot(event_ticker: str) -> list[tuple[int, float]]:
    payload = get(f"/live_data/events/{event_ticker}", {"range": "1h"})
    if not payload:
        return []
    live = payload.get("live_data") or payload
    series = ((live.get("details") or {}).get("timeseries")) or []
    keep: dict[int, float] = {}
    for point in series:
        try:
            ms = int(point["t"])
            value = float(point["v"])
        except (KeyError, TypeError, ValueError):
            continue
        bucket = (ms // SPOT_STEP_MS) * SPOT_STEP_MS
        keep[bucket // 1000] = value  # last observation inside the bucket
    return sorted(keep.items())


def pull_event(event_ticker: str, band: float) -> dict | None:
    payload = get(f"/events/{event_ticker}")
    if not payload:
        return None
    raw_markets = payload.get("markets") or []
    if not raw_markets:
        return None
    first = raw_markets[0]
    open_ts = _ts(first.get("open_time"))
    close_ts = _ts(first.get("close_time"))
    settle = _f(first.get("expiration_value"))
    if open_ts is None or close_ts is None:
        return None

    spot = pull_spot(event_ticker)
    inside = [v for ts, v in spot if open_ts <= ts <= close_ts]
    if not inside:
        return None
    lo, hi = min(inside) - band, max(inside) + band

    markets = []
    for item in raw_markets:
        strike = _f(item.get("floor_strike"))
        if strike is None:
            continue
        markets.append(
            {
                "strike": strike,
                "ticker": item.get("ticker") or "",
                "result": (item.get("result") or "").lower(),
                "volume": _f(item.get("volume_fp")) or 0.0,
                "open_interest": _f(item.get("open_interest_fp")) or 0.0,
                "in_band": lo <= strike <= hi,
            }
        )
    return {
        "event_ticker": event_ticker,
        "open_ts": open_ts,
        "close_ts": close_ts,
        "settle_value": settle,
        "spot": spot,
        "markets": markets,
    }


def pull_candles(ticker: str, open_ts: int, close_ts: int) -> list[dict]:
    payload = get(
        f"/series/{SERIES}/markets/{ticker}/candlesticks",
        {"start_ts": open_ts, "end_ts": close_ts + 60, "period_interval": 1},
        timeout=20,
    )
    return ((payload or {}).get("candlesticks")) or []


def _side(stick: dict, key: str) -> tuple:
    block = stick.get(key) or {}
    return (
        _f(block.get("open_dollars")),
        _f(block.get("high_dollars")),
        _f(block.get("low_dollars")),
        _f(block.get("close_dollars")),
    )


def store_event(conn: sqlite3.Connection, event: dict, quote_rows: list[tuple]) -> None:
    ev = event["event_ticker"]
    conn.execute(
        "INSERT OR REPLACE INTO events (event_ticker, open_ts, close_ts, settle_value, n_markets, pulled_ts)"
        " VALUES (?,?,?,?,?,?)",
        (ev, event["open_ts"], event["close_ts"], event["settle_value"], len(event["markets"]), int(time.time())),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO markets (event_ticker, strike, ticker, result, volume, open_interest)"
        " VALUES (?,?,?,?,?,?)",
        [(ev, m["strike"], m["ticker"], m["result"], m["volume"], m["open_interest"]) for m in event["markets"]],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO spot (event_ticker, ts, value) VALUES (?,?,?)",
        [(ev, ts, value) for ts, value in event["spot"]],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO quotes (event_ticker, strike, ts,"
        " yes_bid_open, yes_bid_high, yes_bid_low, yes_bid_close,"
        " yes_ask_open, yes_ask_high, yes_ask_low, yes_ask_close,"
        " price_close, volume, open_interest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        quote_rows,
    )
    conn.commit()


def worker(event_ticker: str, band: float) -> tuple[dict, list[tuple]] | None:
    event = pull_event(event_ticker, band)
    if event is None:
        return None
    rows: list[tuple] = []
    for market in event["markets"]:
        if not market["in_band"]:
            continue
        sticks = pull_candles(market["ticker"], event["open_ts"], event["close_ts"])
        for stick in sticks:
            ts = stick.get("end_period_ts")
            if ts is None:
                continue
            bid = _side(stick, "yes_bid")
            ask = _side(stick, "yes_ask")
            price = (stick.get("price") or {}).get("close_dollars")
            rows.append(
                (
                    event["event_ticker"],
                    market["strike"],
                    int(ts),
                    bid[0], bid[1], bid[2], bid[3],
                    ask[0], ask[1], ask[2], ask[3],
                    _f(price),
                    _f(stick.get("volume_fp")),
                    _f(stick.get("open_interest_fp")),
                )
            )
    return event, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--band", type=float, default=1200.0, help="strike distance from the window spot range")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0, help="stop after N events (pilot runs)")
    parser.add_argument("--refresh", action="store_true", help="re-pull events already stored")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    have = {row[0] for row in conn.execute("SELECT event_ticker FROM events")}

    events = list_settled_events(args.days)
    todo = [e["event_ticker"] for e in events if args.refresh or e["event_ticker"] not in have]
    if args.limit:
        todo = todo[: args.limit]
    log(f"settled events in window: {len(events)}; to pull: {len(todo)}")

    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(lambda ev: (ev, _safe(worker, ev, args.band)), todo):
            ticker, payload = result
            done += 1
            if payload is None:
                log(f"  skip {ticker}")
                continue
            event, rows = payload
            store_event(conn, event, rows)
            if done % 25 == 0 or done == len(todo):
                rate = done / max(time.time() - started, 1e-9)
                left = (len(todo) - done) / max(rate, 1e-9)
                log(f"  {done}/{len(todo)} events  {rate:.2f}/s  eta {left/60:.1f}m")

    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("events", "markets", "spot", "quotes")
    }
    log("stored:", counts)
    conn.close()
    return 0


def _safe(fn, *fnargs):
    try:
        return fn(*fnargs)
    except Exception as exc:  # a single bad hour must not kill a 2000-event pull
        log("  error:", type(exc).__name__, str(exc)[:160])
        return None


if __name__ == "__main__":
    raise SystemExit(main())

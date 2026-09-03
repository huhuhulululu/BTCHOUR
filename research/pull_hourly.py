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


HOUR_MIN_SECONDS, HOUR_MAX_SECONDS = 50 * 60, 70 * 60


def pull_event(event_ticker: str, band: float, anchor: float | None = None) -> dict | None:
    """One settled hour. `anchor` is the PREVIOUS hour's settlement.

    BRTI is the preferred way to choose which rungs to store, but some hours come back
    with markets and a settlement and no `/live_data`. Under ADR 022 those hours are
    unrecoverable once they age out, so they are archived anyway: the strike band is then
    centred on `anchor`, which is known before this hour opens and therefore cannot bias
    the sample toward strikes near settlement. Studies that need spot skip these hours;
    the price-band and exit studies do not.
    """
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
    # Kalshi files some weekly contracts under an hourly-looking ticker: KXBTCD-26JUL1017
    # runs 2026-07-03 20:00 -> 2026-07-10 21:00 with 50 rungs. ADR 001 scopes this repo to
    # the next-hour ladder, and the candlestick endpoint 400s on a range that wide, so
    # these were being retried on every run forever. Skip them by window length.
    if not (HOUR_MIN_SECONDS <= close_ts - open_ts <= HOUR_MAX_SECONDS):
        return None

    spot = pull_spot(event_ticker)
    inside = [v for ts, v in spot if open_ts <= ts <= close_ts]
    if inside:
        lo, hi = min(inside) - band, max(inside) + band
    elif anchor:
        lo, hi = anchor - 2.0 * band, anchor + 2.0 * band
    else:
        return None

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


def worker(event_ticker: str, band: float, anchor: float | None = None) -> tuple[dict, list[tuple]] | None:
    event = pull_event(event_ticker, band, anchor)
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
    parser.add_argument("--coverage", action="store_true",
                        help="report what is stored, where the gaps are, and what is about to age out")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the archive has fallen behind (for cron)")
    parser.add_argument("--max-lag-hours", type=float, default=6.0,
                        help="how stale the newest archived hour may be before --check fails")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    if args.coverage:
        return report_coverage(conn)
    if args.check:
        return check_fresh(conn, args.max_lag_hours)
    have = {row[0] for row in conn.execute("SELECT event_ticker FROM events")}

    events = list_settled_events(args.days)
    # The previous hour's settlement is the ex-ante anchor for hours with no BRTI.
    settles = {
        row[0]: row[1]
        for row in conn.execute("SELECT close_ts, settle_value FROM events WHERE settle_value IS NOT NULL")
    }
    todo = [e["event_ticker"] for e in events if args.refresh or e["event_ticker"] not in have]
    anchors = {e["event_ticker"]: settles.get(e["close_ts"] - 3600) for e in events}
    if args.limit:
        todo = todo[: args.limit]
    log(f"settled events in window: {len(events)}; to pull: {len(todo)}")

    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(lambda ev: (ev, _safe(worker, ev, args.band, anchors.get(ev))), todo):
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


RETENTION_DAYS = 66  # measured 2026-09-03: /events/{ticker} returns no markets past this


def check_fresh(conn: sqlite3.Connection, max_lag_hours: float) -> int:
    """Exit non-zero when the archive has fallen behind. For cron, so silence is safe.

    ADR 022: Kalshi keeps roughly 66 days of settled markets. History cannot be
    back-filled, so a scheduled job that quietly stops working destroys the only
    irreplaceable thing this repo owns -- and it destroys it invisibly, a day at a time,
    until the gap is older than the window. A puller that succeeds at pulling nothing
    exits 0, which is exactly the failure cron will never tell anyone about. This is the
    check that makes the silence mean something.
    """
    row = conn.execute("SELECT MAX(close_ts) FROM events").fetchone()
    newest = row[0] if row else None
    if not newest:
        log("CHECK FAIL: the archive is empty")
        return 1

    lag_hours = (time.time() - float(newest)) / 3600.0
    stored = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    span_days = 0.0
    oldest = conn.execute("SELECT MIN(close_ts) FROM events").fetchone()[0]
    if oldest:
        span_days = (float(newest) - float(oldest)) / 86400.0

    stamp = datetime.fromtimestamp(int(newest), timezone.utc).strftime("%Y-%m-%d %H:%M")
    log(f"newest archived hour {stamp}  lag {lag_hours:.1f}h"
        f"  stored {stored} hours over {span_days:.1f} days")
    if lag_hours > max_lag_hours:
        log(f"CHECK FAIL: newest archived hour is {lag_hours:.1f}h old,"
            f" limit is {max_lag_hours:.1f}h. Anything older than {RETENTION_DAYS} days"
            f" that is still missing is gone for good (ADR 022).")
        return 1
    if span_days >= 130:
        log("note: the archive now spans >=130 days, so ADR 016's reopen condition for"
            " cushion_hold is met -- re-run research/study_cushion_map.py --slice early/late")
    log("CHECK OK")
    return 0


def report_coverage(conn: sqlite3.Connection) -> int:
    """What is archived, what is missing, and what the rolling window is about to drop.

    Kalshi keeps the settled market records for about 66 days. `/events` still LISTS
    older events -- 8000 of them, back to 2025-08 -- but 6443 of those come back with no
    markets: no result, no expiration_value, no candlesticks. So history cannot be
    back-filled. Anything not archived before it ages out is gone for good, and the only
    way the sample grows is forward, by running this puller regularly.
    """
    rows = conn.execute("SELECT close_ts FROM events ORDER BY close_ts").fetchall()
    if not rows:
        log("nothing stored yet")
        return 0
    stored = [int(r[0]) for r in rows]
    now = int(time.time())
    window_start = now - RETENTION_DAYS * 86400

    # Only count as missing what Kalshi actually lists. The exchange does not open every
    # hour, and some slots carry weekly contracts, so a raw hour-grid diff overstates it.
    listed = {e["close_ts"] for e in list_settled_events(RETENTION_DAYS)}
    have = set(stored)
    missing = sorted(ts for ts in listed if ts >= window_start and ts not in have)

    def day(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")

    log(f"stored          {len(stored)} hours, {day(stored[0])} -> {day(stored[-1])}"
        f"  ({(stored[-1] - stored[0]) / 86400:.1f} days)")
    log(f"live window     last {RETENTION_DAYS} days: {day(window_start)} -> {day(now)}")
    log(f"listed by Kalshi {len(listed)} events in the window")
    log(f"not archived     {len(missing)} of them"
        f"  (weekly contracts filed under an hourly ticker are excluded by design, ADR 001)")
    if missing:
        log(f"  oldest {day(missing[0])}, newest {day(missing[-1])}")
    oldest_ok = max(window_start, stored[0])
    log(f"about to age out: hours before {day(window_start + 7 * 86400)} leave the window"
        f" within a week -- pull them now or lose them")
    log("")
    log("ADR 016 wants >=130 days before cushion_hold may be re-proposed. That cannot be"
        " back-filled; it arrives by running this puller until the archive spans 130 days.")
    return 0


def _safe(fn, *fnargs):
    try:
        return fn(*fnargs)
    except Exception as exc:  # a single bad hour must not kill a 2000-event pull
        log("  error:", type(exc).__name__, str(exc)[:160])
        return None


if __name__ == "__main__":
    raise SystemExit(main())

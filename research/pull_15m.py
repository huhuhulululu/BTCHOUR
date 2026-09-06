"""Pull settled KXBTC15M into its own tables -- an INDEPENDENT test bed for ADR 046.

046's candidate (perp premium -> direction) works on the full hourly tape and dies out of
sample: the late 33 days carry it, the early 34 days are t=0.85. Kalshi retains only ~66
days so a longer history does not exist. What DOES exist is a different instrument on the
same exchange and the same settlement source.

KXBTC15M is structurally cleaner than the ladder for a directional test:

  * ONE market per 15-minute window, strike fixed at the window's opening price, so every
    contract is at the money by construction. No band selection, no rung choice.
  * therefore no cold-rung problem, no min-over-many, and no cross-rung simultaneity --
    four of the thirteen failure modes cannot occur here at all.
  * one observation per window makes dedup structural.
  * ~96 windows/day against 24 hours/day, so roughly 4x the sample.

Same CF Benchmarks settlement family as KXBTCD, so the perp premium's relationship to
settlement should carry across if it is real. If it does not appear here, 046 closes.

    python3 research/pull_15m.py --days 70
    python3 research/pull_15m.py --coverage
"""

from __future__ import annotations

import argparse
import json
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import DEFAULT_DB  # noqa: E402

API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"
SCHEMA = """
CREATE TABLE IF NOT EXISTS w15 (
    event_ticker TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    open_ts      INTEGER NOT NULL,
    close_ts     INTEGER NOT NULL,
    strike       REAL NOT NULL,
    result       TEXT,
    settle_value REAL
);
CREATE TABLE IF NOT EXISTS w15_quotes (
    event_ticker TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    yes_bid_close REAL, yes_ask_close REAL,
    yes_bid_high  REAL, yes_ask_low   REAL,
    volume       REAL,
    PRIMARY KEY (event_ticker, ts)
);
CREATE INDEX IF NOT EXISTS w15_by_close ON w15 (close_ts);
"""

_lock = threading.Lock()


def log(message: str) -> None:
    with _lock:
        print(message, flush=True)


def get(path: str, params: dict, tries: int = 4):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (2 ** attempt))
    return None


def _ts(value):
    if value in (None, ""):
        return None
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _f(value):
    """Kalshi hands some numbers back as strings WITH thousands separators ('77,362.10')."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value:
            return None
    return float(value)


def list_settled(days: int):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out, cursor = [], ""
    while True:
        payload = get("/events", {"series_ticker": SERIES, "status": "settled",
                                  "limit": 200, "with_nested_markets": "true",
                                  **({"cursor": cursor} if cursor else {})})
        if not payload:
            break
        for event in payload.get("events") or []:
            markets = event.get("markets") or []
            if len(markets) != 1:
                continue                      # the series is one market per window
            market = markets[0]
            close = _ts(market.get("close_time"))
            if close is None or datetime.fromtimestamp(close, timezone.utc) < cutoff:
                return out
            out.append({
                "event_ticker": event["event_ticker"],
                "ticker": market["ticker"],
                "open_ts": _ts(market.get("open_time")),
                "close_ts": close,
                "strike": _f(market.get("floor_strike")),
                "result": market.get("result") or "",
                "settle_value": _f(market.get("expiration_value")),
            })
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    return out


def candles(ticker: str, open_ts: int, close_ts: int):
    payload = get(f"/series/{SERIES}/markets/{ticker}/candlesticks",
                  {"start_ts": open_ts, "end_ts": close_ts, "period_interval": 1})
    return (payload or {}).get("candlesticks") or []


def side(stick, key):
    block = stick.get(key) or {}
    return (_f(block.get("close_dollars")), _f(block.get("high_dollars")),
            _f(block.get("low_dollars")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--days", type=int, default=70)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--coverage", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=60)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")

    if args.coverage:
        n = conn.execute("SELECT COUNT(*) FROM w15").fetchone()[0]
        q = conn.execute("SELECT COUNT(*) FROM w15_quotes").fetchone()[0]
        span = conn.execute("SELECT MIN(close_ts), MAX(close_ts) FROM w15").fetchone()
        print(f"windows {n}, quote-minutes {q}")
        if span[0]:
            lo = datetime.fromtimestamp(span[0], timezone.utc)
            hi = datetime.fromtimestamp(span[1], timezone.utc)
            print(f"span {lo:%Y-%m-%d %H:%M} -> {hi:%Y-%m-%d %H:%M}"
                  f"  ({(span[1]-span[0])/86400:.1f} days)")
        res = conn.execute("SELECT result, COUNT(*) FROM w15 GROUP BY result").fetchall()
        print(f"results: {dict(res)}")
        return 0

    have = {r[0] for r in conn.execute("SELECT event_ticker FROM w15")}
    events = [e for e in list_settled(args.days) if e["event_ticker"] not in have]
    if args.limit:
        events = events[: args.limit]
    log(f"settled 15m windows to pull: {len(events)}")

    started = time.time()
    done = 0

    def work(event):
        try:
            return event, candles(event["ticker"], event["open_ts"], event["close_ts"])
        except Exception as exc:
            log(f"  skip {event['ticker']}: {type(exc).__name__}")
            return event, None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for event, sticks in pool.map(work, events):
            done += 1
            if sticks is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO w15 (event_ticker, ticker, open_ts, close_ts,"
                " strike, result, settle_value) VALUES (?,?,?,?,?,?,?)",
                (event["event_ticker"], event["ticker"], event["open_ts"],
                 event["close_ts"], event["strike"], event["result"], event["settle_value"]))
            rows = []
            for stick in sticks:
                ts = stick.get("end_period_ts")
                if ts is None:
                    continue
                bid_c, bid_h, _ = side(stick, "yes_bid")
                ask_c, _, ask_l = side(stick, "yes_ask")
                rows.append((event["event_ticker"], int(ts), bid_c, ask_c, bid_h, ask_l,
                             _f(stick.get("volume_fp"))))
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO w15_quotes (event_ticker, ts, yes_bid_close,"
                    " yes_ask_close, yes_bid_high, yes_ask_low, volume)"
                    " VALUES (?,?,?,?,?,?,?)", rows)
            if done % 200 == 0:
                conn.commit()
                rate = done / max(time.time() - started, 1e-9)
                log(f"  {done}/{len(events)}  {rate:.1f}/s  eta {(len(events)-done)/max(rate,1e-9)/60:.1f}m")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM w15").fetchone()[0]
    log(f"done. windows stored {n}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

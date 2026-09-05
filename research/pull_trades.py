"""Pull SIGNED taker flow -- the one public field this repo has never used.

Every conclusion in ADR 016-042 rests on 1-minute OHLC of the top of book. Two standard
microstructure inputs were never in the data at all:

  * which side was the AGGRESSOR. ADR 023/029 measured rung `volume`, which is unsigned:
    it cannot tell "someone lifted the offer" from "someone hit the bid". Signed flow is
    the single most studied short-horizon predictor in microstructure, and
    `/markets/trades` publishes `taker_side` for free.
  * order book DEPTH. `/markets/{ticker}/orderbook` returns empty for settled markets, so
    depth is live-only and cannot be backtested. That road is closed; this one is not.

Retention: trades survive about as long as the markets do -- 1550 of the 1575 archived
hours have them (checked by binary search, the edge is 2026-06-30). The endpoint has a
time-windowed mode, but `series_ticker` / `ticker_prefix` are ignored there and it returns
every series on the exchange mixed together, so this pulls per ticker instead and only for
rungs the quotes table already shows traded (31,373 of 290,156 markets, ~20 per hour).

    python3 research/pull_trades.py                 # incremental; stored rungs are skipped
    python3 research/pull_trades.py --coverage
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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import DEFAULT_DB  # noqa: E402

API = "https://api.elections.kalshi.com/trade-api/v2"
PAGE = 1000
SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id     TEXT PRIMARY KEY,
    event_ticker TEXT NOT NULL,
    strike       REAL NOT NULL,
    ts           INTEGER NOT NULL,      -- unix seconds
    ts_us        INTEGER NOT NULL,      -- microseconds within the second
    taker_side   TEXT NOT NULL,         -- 'yes' or 'no': which side the AGGRESSOR took
    count        REAL,
    yes_price    REAL,
    no_price     REAL
);
CREATE INDEX IF NOT EXISTS trades_by_event ON trades (event_ticker, ts);
CREATE INDEX IF NOT EXISTS trades_by_rung ON trades (event_ticker, strike, ts);
CREATE TABLE IF NOT EXISTS trades_done (
    event_ticker TEXT NOT NULL,
    strike       REAL NOT NULL,
    n            INTEGER,
    PRIMARY KEY (event_ticker, strike)
);
"""

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def _get(path: str, params: dict, tries: int = 4):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == tries - 1:
                raise
            # 429 and transient network both back off the same way
            time.sleep(1.5 * (2 ** attempt))
    return None


def _parse_ts(raw: str) -> tuple[int, int]:
    """`2026-09-03T22:36:49.121228Z` -> (unix seconds, microseconds)."""
    text = raw.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(text)
    return int(stamp.timestamp()), stamp.microsecond


def _f(value):
    return None if value in (None, "") else float(value)


def pull_rung(event_ticker: str, strike: float, ticker: str) -> list[tuple]:
    """Every trade on one rung, paging until the cursor runs out."""
    rows: list[tuple] = []
    cursor = ""
    while True:
        params = {"ticker": ticker, "limit": PAGE}
        if cursor:
            params["cursor"] = cursor
        payload = _get("/markets/trades", params)
        if not payload:
            break
        for trade in payload.get("trades") or []:
            seconds, micro = _parse_ts(trade["created_time"])
            rows.append((
                trade["trade_id"], event_ticker, strike, seconds, micro,
                trade.get("taker_side") or "",
                _f(trade.get("count_fp")),
                _f(trade.get("yes_price_dollars")),
                _f(trade.get("no_price_dollars")),
            ))
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N rungs (pilot runs)")
    ap.add_argument("--coverage", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=60)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    if args.coverage:
        done = conn.execute("SELECT COUNT(*) FROM trades_done").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        span = conn.execute("SELECT MIN(ts), MAX(ts) FROM trades").fetchone()
        want = conn.execute("""SELECT COUNT(*) FROM (SELECT event_ticker, strike FROM quotes
                               WHERE volume>0 GROUP BY event_ticker, strike)""").fetchone()[0]
        log(f"rungs pulled {done} of {want}   trades stored {n}")
        if span[0]:
            lo = datetime.fromtimestamp(span[0], timezone.utc)
            hi = datetime.fromtimestamp(span[1], timezone.utc)
            log(f"trade span {lo:%Y-%m-%d %H:%M} -> {hi:%Y-%m-%d %H:%M}")
        sides = conn.execute("SELECT taker_side, COUNT(*) FROM trades GROUP BY taker_side").fetchall()
        log(f"taker_side: {dict(sides)}")
        return 0

    have = {(r[0], r[1]) for r in conn.execute("SELECT event_ticker, strike FROM trades_done")}
    todo = [
        (r[0], float(r[1]), r[2])
        for r in conn.execute("""
            SELECT q.event_ticker, q.strike, m.ticker
            FROM quotes q JOIN markets m
              ON m.event_ticker = q.event_ticker AND m.strike = q.strike
            WHERE q.volume > 0
            GROUP BY q.event_ticker, q.strike
            ORDER BY q.event_ticker DESC""")
        if (r[0], float(r[1])) not in have
    ]
    if args.limit:
        todo = todo[: args.limit]
    log(f"traded rungs to pull: {len(todo)} (already have {len(have)})")

    started = time.time()
    done = 0

    def work(item):
        event_ticker, strike, ticker = item
        try:
            return item, pull_rung(event_ticker, strike, ticker)
        except Exception as exc:  # a dead rung must not kill the run
            log(f"  skip {ticker}: {type(exc).__name__}")
            return item, None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for (event_ticker, strike, ticker), rows in pool.map(work, todo):
            done += 1
            if rows is None:
                continue
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO trades (trade_id, event_ticker, strike, ts, ts_us,"
                    " taker_side, count, yes_price, no_price) VALUES (?,?,?,?,?,?,?,?,?)", rows)
            conn.execute("INSERT OR REPLACE INTO trades_done (event_ticker, strike, n) VALUES (?,?,?)",
                         (event_ticker, strike, len(rows)))
            if done % 200 == 0:
                conn.commit()
                rate = done / max(time.time() - started, 1e-9)
                left = (len(todo) - done) / max(rate, 1e-9)
                log(f"  {done}/{len(todo)} rungs  {rate:.1f}/s  eta {left/60:.1f}m")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    log(f"done. rungs={done} trades stored={total}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

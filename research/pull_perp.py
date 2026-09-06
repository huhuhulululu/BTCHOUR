"""Pull the Binance USD-M perpetual PREMIUM INDEX -- the spot-perp basis, 1-minute.

ADR 044 closed the cross-market family but left exactly one lead alive: the perp premium
index looked like t=+2.83 before controls and +1.83 after momentum controls, failing this
repo's Bonferroni bar across six cuts and sitting on ADR 035's tripwire. It is the last
untested input, so it gets tested properly rather than argued about.

Note on access: fapi.binance.com returns HTTP 451 from this environment, but the public
dump mirror data.binance.vision is reachable and carries the same series as monthly and
daily zips. Monthly covers whole months; daily fills the ragged ends.

The premium index is the funding basis: roughly (perp mark - index) / index. It is
information about POSITIONING -- who is paying to be long -- which is not a transform of
the Kalshi book and not a transform of BRTI. That is why it is worth one more test after
DVOL and BVOL both came back at dR2 = 0.00000 (ADR 044): those were volatility, this is
direction.

    python3 research/pull_perp.py
    python3 research/pull_perp.py --coverage
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import DEFAULT_DB  # noqa: E402

BASE = "https://data.binance.vision/data/futures/um"
SYMBOL = "BTCUSDT"
SCHEMA = """
CREATE TABLE IF NOT EXISTS perp_premium (
    ts     INTEGER PRIMARY KEY,   -- minute open, unix seconds
    open   REAL, high REAL, low REAL, close REAL
);
"""


def fetch(url: str):
    try:
        with urllib.request.urlopen(url, timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def rows_from_zip(blob: bytes):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        name = archive.namelist()[0]
        text = archive.read(name).decode("utf-8", "replace")
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().lstrip("-").isdigit():
            continue  # newer dumps carry a header line
        ms = int(row[0])
        # dumps switched to microseconds for some ranges; normalise on magnitude
        seconds = ms // 1000 if ms > 10_000_000_000 else ms
        if seconds > 10_000_000_000:
            seconds //= 1000
        out.append((seconds, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--start", default="2026-06-29")
    ap.add_argument("--end", default="2026-09-05")
    ap.add_argument("--coverage", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=60)
    conn.executescript(SCHEMA)

    if args.coverage:
        n = conn.execute("SELECT COUNT(*) FROM perp_premium").fetchone()[0]
        span = conn.execute("SELECT MIN(ts), MAX(ts) FROM perp_premium").fetchone()
        print(f"perp_premium rows={n}")
        if span[0]:
            lo = datetime.fromtimestamp(span[0], timezone.utc)
            hi = datetime.fromtimestamp(span[1], timezone.utc)
            gaps = conn.execute(
                "SELECT COUNT(*) FROM (SELECT ts - LAG(ts) OVER (ORDER BY ts) d"
                " FROM perp_premium) WHERE d > 60").fetchone()[0]
            print(f"span {lo:%Y-%m-%d %H:%M} -> {hi:%Y-%m-%d %H:%M}  gaps>1min: {gaps}")
        return 0

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    urls = []
    seen_months = set()
    day = start
    while day <= end:
        month = f"{day:%Y-%m}"
        # a whole month inside the range is one request instead of thirty
        nxt = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
        if month not in seen_months and day.day == 1 and nxt <= end:
            urls.append(f"{BASE}/monthly/premiumIndexKlines/{SYMBOL}/1m/{SYMBOL}-1m-{month}.zip")
            seen_months.add(month)
            day = nxt
            continue
        urls.append(f"{BASE}/daily/premiumIndexKlines/{SYMBOL}/1m/{SYMBOL}-1m-{day:%Y-%m-%d}.zip")
        day += timedelta(days=1)

    total = 0
    for url in urls:
        blob = fetch(url)
        if blob is None:
            print(f"  miss {url.rsplit('/', 1)[-1]}")
            continue
        rows = rows_from_zip(blob)
        conn.executemany(
            "INSERT OR IGNORE INTO perp_premium (ts, open, high, low, close) VALUES (?,?,?,?,?)",
            rows)
        conn.commit()
        total += len(rows)
        print(f"  {url.rsplit('/', 1)[-1]}: {len(rows)} rows")
    n = conn.execute("SELECT COUNT(*) FROM perp_premium").fetchone()[0]
    print(f"fetched {total}, stored {n}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

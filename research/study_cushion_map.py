"""Study 5 -- the cushion x ask map, instead of one grid-searched threshold.

A single winning cell out of a grid is what a walk-forward maximises when the rule space
is big (15m repo, phase19 `rulespace_size`). A *map* is harder to fool: if the mechanism
is real, calibration should rise monotonically with the cushion at a fixed ask band, and
the cell the shipped rule sits in should not be a lonely island.

Rows are one (event, strike, side) observation at the first minute the cell is entered,
taker at the ask, held to settlement.

    python3 research/study_cushion_map.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    cushion,
    load_hours,
    net_cents,
    sample_days,
)

Z_BUCKETS = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 9.9)]
ASK_BUCKETS = [(0.55, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 1.00)]
LEFT_BUCKETS = [(120, 900), (900, 1800), (1800, 3600)]


def bucket_of(value: float, buckets) -> tuple | None:
    for lo, hi in buckets:
        if lo <= value < hi:
            return (lo, hi)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"],
                    help="calendar half: fit on early, validate on late")
    ap.add_argument("--min-n", type=int, default=150)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    print(f"# hours={len(hours)} days={days:.1f}  taker in, hold to settlement")
    print("# one row per (event, strike, side), first minute it enters the cell")

    by_cell: dict[tuple, Bucket] = {}
    by_z_left: dict[tuple, Bucket] = {}
    for hour in hours:
        seen: set[tuple] = set()
        for bar in hour.bars:
            if bar.seconds_left < 120:
                continue
            for strike, quote in bar.quotes.items():
                if quote.volume < 1.0:
                    continue
                side = "yes" if bar.spot > strike else "no"
                ask = quote.ask(side)
                if ask is None:
                    continue
                z = cushion(bar, strike)
                zb = bucket_of(z, Z_BUCKETS)
                ab = bucket_of(ask, ASK_BUCKETS)
                if zb is None or ab is None:
                    continue
                key = (strike, side, zb, ab)
                if key in seen:
                    continue
                seen.add(key)
                won = hour.won(strike, side)
                if won is None:
                    continue
                cents = net_cents(ask, won)
                by_cell.setdefault(
                    (zb, ab), Bucket(f"z {zb[0]:.1f}-{zb[1]:.1f}  ask {ab[0]:.2f}-{ab[1]:.2f}")
                ).add(hour.event_ticker, cents, won, ask, hour.close_ts)
                lb = bucket_of(bar.seconds_left, LEFT_BUCKETS)
                if lb is not None and ab in ((0.70, 0.80), (0.80, 0.90)):
                    by_z_left.setdefault(
                        (zb, lb), Bucket(f"z {zb[0]:.1f}-{zb[1]:.1f}  left {lb[0]}-{lb[1]}s")
                    ).add(hour.event_ticker, cents, won, ask, hour.close_ts)

    print("\n## cushion x ask  (the shipped rule is z 1.5+ x ask 0.70-0.90)")
    for ab in ASK_BUCKETS:
        print(f"  -- ask {ab[0]:.2f}-{ab[1]:.2f}")
        for zb in Z_BUCKETS:
            bucket = by_cell.get((zb, ab))
            if bucket is None or len(bucket) < args.min_n:
                continue
            print("   " + bucket.result(days).row())

    print("\n## cushion x minutes left (ask 0.70-0.90 only)")
    for zb in Z_BUCKETS:
        for lb in LEFT_BUCKETS:
            bucket = by_z_left.get((zb, lb))
            if bucket is None or len(bucket) < args.min_n:
                continue
            print("   " + bucket.result(days).row())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

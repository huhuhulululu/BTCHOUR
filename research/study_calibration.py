"""Study 1 -- where is the money on the KXBTCD hourly ladder?

Two maps, both "buy one contract taker, hold to settlement", no signal at all:

  A. by ask price band              -- is the hourly book favorite-longshot biased?
  B. by price band x minutes left   -- does the mispricing live early, mid, or in the tail?

The 15m repo found (phase12b) cheap side 0.20-0.30 overpriced by ~2pp and the favorite
0.70-0.80 fair to +0.09pp. This asks the same question of the hour ladder, which is the
book BTCHOUR actually trades and which nobody has measured.

    python3 research/study_calibration.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    load_hours,
    net_cents,
    sample_days,
)

BANDS = [
    (0.02, 0.10), (0.10, 0.18), (0.18, 0.25), (0.25, 0.32), (0.32, 0.42),
    (0.42, 0.50), (0.50, 0.58), (0.58, 0.68), (0.68, 0.75), (0.75, 0.82),
    (0.82, 0.90), (0.90, 0.95), (0.95, 0.98), (0.98, 1.00),
]
LEFT_BUCKETS = [(2700, 3600), (1800, 2700), (900, 1800), (300, 900), (0, 300)]


def band_of(price: float) -> tuple[float, float] | None:
    for lo, hi in BANDS:
        if lo <= price < hi:
            return (lo, hi)
    return None


def left_of(seconds: float) -> tuple[int, int] | None:
    for lo, hi in LEFT_BUCKETS:
        if lo <= seconds < hi:
            return (lo, hi)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"],
                    help="calendar half: fit on early, validate on late")
    ap.add_argument("--step", type=int, default=5, help="sample every Nth minute")
    ap.add_argument("--min-volume", type=float, default=1.0, help="candle volume floor (tradeable)")
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    print(f"# hours={len(hours)} days={days:.1f}  minute step={args.step}  candle volume >= {args.min_volume}")

    by_band: dict[tuple, Bucket] = {}
    by_band_left: dict[tuple, Bucket] = {}
    for hour in hours:
        for bar in hour.bars:
            if bar.minute % args.step:
                continue
            if bar.seconds_left < 60:
                continue
            for strike, quote in bar.quotes.items():
                if quote.volume < args.min_volume:
                    continue
                for side in ("yes", "no"):
                    ask = quote.ask(side)
                    if ask is None or ask <= 0.01 or ask >= 1.0:
                        continue
                    band = band_of(ask)
                    if band is None:
                        continue
                    won = hour.won(strike, side)
                    if won is None:
                        continue
                    cents = net_cents(ask, won)
                    key = (band, side)
                    by_band.setdefault(key, Bucket(f"{band[0]:.2f}-{band[1]:.2f} {side}")).add(
                        hour.event_ticker, cents, won, ask, hour.close_ts
                    )
                    left = left_of(bar.seconds_left)
                    if left is not None:
                        lkey = (band, left)
                        by_band_left.setdefault(
                            lkey, Bucket(f"{band[0]:.2f}-{band[1]:.2f} left {left[0]}-{left[1]}s")
                        ).add(hour.event_ticker, cents, won, ask, hour.close_ts)

    print("\n## A. by ask band and side (taker, hold to settlement)")
    for (band, side), bucket in sorted(by_band.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(bucket) < 200:
            continue
        print("   " + bucket.result(days).row())

    print("\n## A'. by ask band, sides pooled")
    pooled: dict[tuple, Bucket] = {}
    for (band, side), bucket in by_band.items():
        target = pooled.setdefault(band, Bucket(f"{band[0]:.2f}-{band[1]:.2f} pooled"))
        target.cents.extend(bucket.cents)
        target.clusters.extend(bucket.clusters)
        target.close_ts.extend(bucket.close_ts)
        target.wins += bucket.wins
        target.entry_sum += bucket.entry_sum
    for band in sorted(pooled):
        bucket = pooled[band]
        if len(bucket) < 200:
            continue
        print("   " + bucket.result(days).row())

    print("\n## B. by ask band x seconds left")
    for key in sorted(by_band_left, key=lambda k: (k[0], -k[1][0])):
        bucket = by_band_left[key]
        if len(bucket) < 300:
            continue
        print("   " + bucket.result(days).row())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

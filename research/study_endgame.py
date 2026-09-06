"""Study 13 -- the last five minutes, which every other study filters out.

study_calibration cuts at seconds_left < 60, study_maker / study_rule / study_density /
study_cushion_map at 120, study_candidates at 120, study_conditioning decides at T-30m.
So the endgame has never been measured, and it is the most mechanically interesting
stretch on the clock:

  * Settlement is the mean of the last 60 seconds of BRTI, so inside T-60s part of the
    settlement price is ALREADY FIXED. ADR 034 validated that branch of the model
    (sd(z) 0.96-1.06 across 30-60s) against a naive model that overstates the remaining
    move by 4.5x at tau=20s. If the BOOK is as naive as the model used to be, the last
    minutes are where it shows.
  * The quadratic fee is smallest where the ladder is most decided, and by T-2m most
    rungs are decided.

Against that: liquidity thins out exactly here, and ADR 029 is emphatic that a positive
living in cold rungs is not a tradeable positive. So this reports the liquidity split
first and the pooled number second, never the other way round.

Discipline: full minute resolution, one row per (event, strike, side, bucket), both
sides priced at their own ask, production fees, clustered by event, both halves. ADR
035's tripwire is in force -- anything over 2pp here is a stale quote, not an edge.

    python3 research/study_endgame.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    liquidity_tier,
    load_hours,
    net_cents,
    rung_reference_volume,
    sample_days,
)

LEFT_BUCKETS = [(20, 60), (60, 120), (120, 180), (180, 300), (300, 600)]
ASK_BANDS = [(0.02, 0.20), (0.20, 0.45), (0.45, 0.55), (0.55, 0.80), (0.80, 0.98)]


def bucket_of(value, edges):
    for lo, hi in edges:
        if lo <= value < hi:
            return (lo, hi)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-n", type=int, default=200)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)

    by_left: dict = {}
    by_left_liquid: dict = {}
    by_cell: dict = {}
    seen: set = set()

    for hour in hours:
        for bar in hour.bars:
            left = bucket_of(bar.seconds_left, LEFT_BUCKETS)
            if left is None:
                continue
            reference = rung_reference_volume(bar)
            for strike, quote in bar.quotes.items():
                if quote.yes_bid is None or quote.yes_ask is None or quote.volume < 1.0:
                    continue
                cold = liquidity_tier(quote.volume, reference) == "cold"
                for side in ("yes", "no"):
                    ask = quote.ask(side)
                    won = hour.won(strike, side)
                    if ask is None or won is None:
                        continue
                    band = bucket_of(ask, ASK_BANDS)
                    if band is None:
                        continue
                    key = (hour.event_ticker, strike, side, left)
                    if key in seen:
                        continue
                    seen.add(key)
                    cents = net_cents(ask, won)
                    label = f"T-{left[0]:>3}..{left[1]:<3}s"
                    by_left.setdefault(label, Bucket(label + " all")).add(
                        hour.event_ticker, cents, won, ask, hour.close_ts)
                    if not cold:
                        by_left_liquid.setdefault(label, Bucket(label + " LIQUID")).add(
                            hour.event_ticker, cents, won, ask, hour.close_ts)
                        cell = f"{label} ask {band[0]:.2f}-{band[1]:.2f}"
                        by_cell.setdefault(cell, Bucket(cell)).add(
                            hour.event_ticker, cents, won, ask, hour.close_ts)

    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}")
    print("# every other study filters this stretch out; both sides, deduped per bucket")
    cells = [k for k in by_cell if len(by_cell[k]) >= args.min_n]
    bar_t = 2.77 if len(cells) <= 10 else 3.2
    print(f"# {len(cells)} liquid cells -> Bonferroni |t| > {bar_t:.2f}."
          f" Over 2pp of calibration is a stale quote (035), not an edge.")

    print("\n## by time left, LIQUID rungs only (029: read this one first)")
    for label in sorted(by_left_liquid):
        bucket = by_left_liquid[label]
        if len(bucket) >= args.min_n:
            print("   " + bucket.result(days).row())

    print("\n## by time left, all rungs (cold included -- for contrast only)")
    for label in sorted(by_left):
        bucket = by_left[label]
        if len(bucket) >= args.min_n:
            print("   " + bucket.result(days).row())

    print("\n## liquid cells that clear Bonferroni")
    hits = 0
    for cell in sorted(cells):
        result = by_cell[cell].result(days)
        if abs(result.t) > bar_t:
            hits += 1
            print("   " + result.row())
    if not hits:
        print("   none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

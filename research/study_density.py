"""Study 7 -- the ladder's implied density, and the range bets synthesised from it.

Every rule measured so far is ONE outright leg, so the price-band map in study 1 can
only ever see the market's *marginal* P(S > K). The 188-rung ladder carries more than
that: differencing P(S > K) across adjacent strikes gives the market's implied density
over $100 buckets, and two legs of KXBTCD synthesise a range bet on it.

  YES at K1  pays $1 if S > K1
  NO  at K2  pays $1 if S <= K2          (K1 < K2)
  together   pays $1 always, plus $1 more if K1 < S <= K2

So the pair is a range bet whose true price is `yes_ask(K1) + no_ask(K2) - 1`, and both
legs are deep when the range is wide -- which is where the quadratic fee is smallest.
A ±$1000 range around spot costs ~0.14c in fees against a ~$1 payout, versus ~1.3c on a
single mid-priced outright. Whatever the ladder gets wrong about the *shape* of the
settlement distribution shows up here and nowhere in study 1.

Three questions:

  A. implied vs realised   bucket by normalised moneyness z = (K - S) / sigma_remaining;
                           does the implied density match where settlement actually lands
  B. range bets            buy the synthetic range at each half-width, hold to settlement
  C. TWAP compression      settlement is a 60-second BRTI mean, not a point price, so the
                           terminal distribution is narrower than a point-price model.
                           Does the book price that in the last minutes?

    python3 research/study_density.py --slice early
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.fees import taker_fee  # noqa: E402
from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    load_hours,
    sample_days,
)

Z_EDGES = [-3.0, -2.0, -1.5, -1.0, -0.6, -0.3, 0.0, 0.3, 0.6, 1.0, 1.5, 2.0, 3.0]
HALF_WIDTHS = [200.0, 300.0, 400.0, 600.0, 800.0, 1200.0]
MINUTE_STEP = 5


def sigma_remaining(bar) -> float:
    """Dollars of residual move, on the repo's own vol and the 60s TWAP floor."""
    tau = max(bar.seconds_left, 60.0) / (365.25 * 24 * 3600)
    return bar.spot * bar.annual_vol * math.sqrt(tau)


def z_bucket(z: float):
    for lo, hi in zip(Z_EDGES, Z_EDGES[1:]):
        if lo <= z < hi:
            return (lo, hi)
    return None


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-n", type=int, default=300)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f"  minute step={MINUTE_STEP}")

    # ---- A. implied vs realised, by normalised moneyness -------------------------
    implied_sum: dict = {}
    realised_sum: dict = {}
    counts: dict = {}
    # ---- B. synthetic range bets -------------------------------------------------
    ranges: dict = {}
    # ---- C. the same range, split by minutes left --------------------------------
    ranges_left: dict = {}

    for hour in hours:
        for bar in hour.bars:
            if bar.minute % MINUTE_STEP or bar.seconds_left < 120:
                continue
            sig = sigma_remaining(bar)
            if sig <= 0:
                continue
            strikes = sorted(bar.quotes)
            if len(strikes) < 6:
                continue

            # A: the ladder's marginal P(S > K) against the realised indicator.
            for strike in strikes:
                quote = bar.quotes[strike]
                if quote.yes_bid is None or quote.yes_ask is None or quote.volume < 1.0:
                    continue
                mid = (quote.yes_bid + quote.yes_ask) / 2.0
                won = hour.won(strike, "yes")
                if won is None:
                    continue
                key = z_bucket((strike - bar.spot) / sig)
                if key is None:
                    continue
                implied_sum[key] = implied_sum.get(key, 0.0) + mid
                realised_sum[key] = realised_sum.get(key, 0.0) + (1.0 if won else 0.0)
                counts[key] = counts.get(key, 0) + 1

            # B/C: the synthetic range, bought at the two asks and held to settlement.
            for half in HALF_WIDTHS:
                lo = max((k for k in strikes if k <= bar.spot - half), default=None)
                hi = min((k for k in strikes if k >= bar.spot + half), default=None)
                if lo is None or hi is None or lo >= hi:
                    continue
                a1 = bar.quotes[lo].ask("yes")     # YES at the low rung
                a2 = bar.quotes[hi].ask("no")      # NO at the high rung
                if a1 is None or a2 is None:
                    continue
                if bar.quotes[lo].volume < 1.0 or bar.quotes[hi].volume < 1.0:
                    continue
                price = a1 + a2 - 1.0              # what the range itself costs
                if not (0.02 < price < 0.98):
                    continue
                fees = taker_fee(a1) + taker_fee(a2)
                inside = hour.settle is not None and lo < hour.settle <= hi
                cents = ((1.0 if inside else 0.0) - price - fees) * 100.0
                ranges.setdefault(half, Bucket(f"range +-${half:.0f}")).add(
                    hour.event_ticker, cents, inside, price, hour.close_ts
                )
                left = "T-<10m" if bar.seconds_left < 600 else (
                    "T-10..30m" if bar.seconds_left < 1800 else "T->30m")
                ranges_left.setdefault((half, left), Bucket(f"range +-${half:.0f} {left}")).add(
                    hour.event_ticker, cents, inside, price, hour.close_ts
                )

    print("\n## A. implied P(S>K) vs realised, by normalised moneyness z=(K-S)/sigma_left")
    print("   (a GBM ladder would sit on N(-z); the 60s TWAP settlement should make the")
    print("    real distribution *narrower* than the model, i.e. realised > implied for z<0)")
    print(f"   {'z bucket':>14} {'n':>7} {'implied':>9} {'realised':>9} {'diff pp':>9} {'N(-z)':>8}")
    for key in sorted(counts, key=lambda k: k[0]):
        n = counts[key]
        if n < args.min_n:
            continue
        imp = implied_sum[key] / n
        real = realised_sum[key] / n
        mid_z = (key[0] + key[1]) / 2
        print(f"   [{key[0]:>5.1f},{key[1]:>5.1f}) {n:>7} {imp:>9.4f} {real:>9.4f}"
              f" {(real - imp) * 100:>+9.2f} {normal_cdf(-mid_z):>8.4f}")

    print("\n## B. synthetic range bought at both asks, held to settlement")
    for half in HALF_WIDTHS:
        bucket = ranges.get(half)
        if bucket and len(bucket) >= args.min_n:
            print("   " + bucket.result(days).row())

    print("\n## C. same range, by time left (C = the TWAP-compression question)")
    for half in HALF_WIDTHS:
        for left in ("T->30m", "T-10..30m", "T-<10m"):
            bucket = ranges_left.get((half, left))
            if bucket and len(bucket) >= args.min_n:
                print("   " + bucket.result(days).row())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

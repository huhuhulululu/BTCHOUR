"""Study 16 -- the one untested cell: 045's signal entered as a MAKER.

ADR 045 made the problem precise. Conditioned on the perp premium sitting in its bottom
quintile, buying NO in the ATM band is +5.47c at t=+1.81 -- real, and about 4.8c short of
what it costs to take it. That 4.8c is a round trip: the half-spread plus the quadratic
taker fee.

ADR 036 measured the other half of the arithmetic: the maker fee on this series is ZERO,
so a rested entry's break-even bar is 0.0pp rather than 100*taker_fee(ask).

015/027 closed the maker route, but on a play with NO signal -- a 25c coupon triggered by
the 3-minute impulse, which section 10 showed carries no direction at all. Adverse
selection eats an edgeless rest by construction. It has never been tested on a rest whose
DIRECTION is significant.

So this is the one cell where both halves hold at once: a Bonferroni-clearing directional
filter, and an entry whose fee bar is zero. Single leg, so none of 036's legging risk.

What can still kill it, and is measured here rather than assumed:
  * fill rate -- a rest that never fills earns nothing
  * adverse selection -- if fills concentrate where the signal is wrong, the edge inverts
  * ADR 027: touch is an UPPER BOUND on maker fills. --fill-ticks tightens it to a strict
    cross, and the honest number is the tightened one.

    python3 research/study_perp_maker.py
    python3 research/study_perp_maker.py --fill-ticks 1 --slice late
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    clustered_t,
    liquidity_tier,
    load_hours,
    rung_reference_volume,
    sample_days,
)
from research.study_perp import (  # noqa: E402
    BAND_HI,
    BAND_LO,
    DECISION_SECONDS,
    deviation,
    load_premium,
)


def no_ask_low(quote, ticks: int):
    """Lowest NO ask printed inside the minute, shifted by the fill convention.

    A resting NO bid at p is hit when the NO ask trades down to p. NO ask = 1 - yes_bid,
    so the minute's lowest NO ask comes from the minute's HIGHEST yes bid. Kalshi prices
    sit on a 1c grid, so a strict cross is `low <= rest - 1 tick`; shifting the observed
    low up by `ticks` cents expresses that without touching the comparison.
    """
    high = quote.yes_bid_high
    return None if high is None else round(1.0 - high, 4) + 0.01 * ticks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--quantile", type=float, default=0.20, help="premium tail to trade")
    ap.add_argument("--fill-ticks", type=int, default=0,
                    help="0 = quote touch (upper bound, ADR 027); 1 = strict cross")
    ap.add_argument("--improve", type=int, default=0,
                    help="rest this many ticks better than the bid (buys queue position)")
    args = ap.parse_args(argv)

    hours = load_hours(args.db, slice_half=args.slice)
    days = sample_days(hours)
    prem = load_premium(args.db)

    # the premium cut is taken on the WHOLE sample, then applied -- never re-optimised
    devs = []
    picked = {}
    for hour in hours:
        bar = min(hour.bars, key=lambda b: abs(b.seconds_left - DECISION_SECONDS))
        if abs(bar.seconds_left - DECISION_SECONDS) > 180:
            continue
        dev = deviation(prem, bar.ts, 5, 60)
        if dev is None:
            continue
        picked[hour.event_ticker] = (bar, dev)
        devs.append(dev)
    if not devs:
        print("no premium data")
        return 0
    cut = sorted(devs)[int(len(devs) * args.quantile)]

    taker = Bucket("TAKER at the NO ask (what 045 measured)")
    maker = Bucket("MAKER rested at the NO bid")
    hung = filled = 0
    waits = []
    unfilled_would_have_won = 0
    unfilled = 0

    for hour in hours:
        entry = picked.get(hour.event_ticker)
        if entry is None:
            continue
        bar, dev = entry
        if dev >= cut:                       # only the signal tail
            continue
        reference = rung_reference_volume(bar)
        index = hour.bars.index(bar)
        for strike, quote in bar.quotes.items():
            if quote.yes_bid is None or quote.yes_ask is None or quote.volume < 1.0:
                continue
            if liquidity_tier(quote.volume, reference) == "cold":
                continue
            ask = quote.ask("no")
            bid = quote.bid("no")
            won = hour.won(strike, "no")
            if ask is None or bid is None or won is None:
                continue
            if not (BAND_LO <= ask <= BAND_HI):
                continue

            from btchour.fees import taker_fee
            taker.add(hour.event_ticker,
                      ((1.0 if won else 0.0) - ask - taker_fee(ask)) * 100.0,
                      won, ask, hour.close_ts)

            rest = round(bid + 0.01 * args.improve, 4)
            if not (0.0 < rest < 1.0) or rest >= ask:
                continue
            hung += 1
            # a rest placed on this bar can only be hit from the NEXT bar on (ADR 032)
            hit_at = None
            for offset, later in enumerate(hour.bars[index + 1:], start=1):
                q = later.quotes.get(strike)
                if q is None:
                    continue
                low = no_ask_low(q, args.fill_ticks)
                if low is not None and low <= rest + 1e-9:
                    hit_at = offset
                    break
            if hit_at is None:
                unfilled += 1
                unfilled_would_have_won += 1 if won else 0
                continue
            filled += 1
            waits.append(hit_at)
            # maker fee is 0 on this series
            maker.add(hour.event_ticker, ((1.0 if won else 0.0) - rest) * 100.0,
                      won, rest, hour.close_ts)

    conv = "touch (upper bound)" if not args.fill_ticks else f"strict cross +{args.fill_ticks} tick"
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f"  premium bottom {args.quantile:.0%}, fill: {conv}, improve {args.improve} tick(s)")
    print(f"# rests hung {hung}, filled {filled} ({filled/max(hung,1):.1%})"
          f"{'' if not waits else f', median wait {statistics.median(waits):.0f} bars'}")
    if unfilled:
        print(f"# ADVERSE SELECTION CHECK: unfilled rests would have won"
              f" {unfilled_would_have_won/unfilled:.1%} of the time"
              f" vs {maker.wins/max(len(maker),1):.1%} for the filled ones")
        print(f"#   (filled MUCH worse than unfilled = the fills are the bad ones)")
    for bucket in (taker, maker):
        if len(bucket):
            print("   " + bucket.result(days).row())
    if len(maker) and len(taker):
        mr, tr = maker.result(days), taker.result(days)
        print(f"   maker minus taker: {mr.mean_cents - tr.mean_cents:+.2f}c per contract"
              f"   (this is the round trip 045 said was eating the edge)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

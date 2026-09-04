"""Study 12 -- rest BOTH legs of a rung pair. The one family with a 0.0pp fee bar.

Study 11 found the ladder arbitrage-free to takers: the cheapest pair per bar costs
$1.0107 median against a payoff of at least $1, so it misses by 1.07c -- almost exactly
two half-spreads plus two quadratic fees. But the same pair RESTED costs $0.9900 median,
inside $1 on 99.78% of bars, and maker fee is 0 on this series.

What that inequality actually says, precisely, is `yes_bid(K1) < yes_ask(K2)` for
K1 < K2 -- the spread is wider than the density difference between the two rungs. It is
not an arbitrage. Capturing it is MARKET MAKING, and the whole question is legging risk:

  * both legs fill  -> payoff is $1 in every state and $2 when K1 < S <= K2, against a
                       cost under $1. Locked, with NO directional exposure.
  * one leg fills   -> a naked directional position at a price someone chose to hit,
                       which is the adverse selection 015/027 measured.

015/027 closed the DIRECTIONAL single-leg coupon. It says nothing about this, because a
completed pair has no direction to be adversely selected on. The exposure here is the
incomplete pair, and nobody has measured how often that happens or what it costs.

Fill convention, per ADR 032/033: a rest placed on bar t can only be hit from bar t+1
onward, and "was my rest hit" is the one question the minute extreme legitimately
answers. A YES bid at p fills when yes_ask_low <= p; a NO bid at p fills when
1 - yes_bid_high <= p.

    python3 research/study_two_leg_maker.py
    python3 research/study_two_leg_maker.py --slice early
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.fees import taker_fee  # noqa: E402
from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    liquidity_tier,
    load_hours,
    rung_reference_volume,
    sample_days,
)

MAX_PAIRS_PER_HOUR = 1


def yes_fill(quote, price: float) -> bool:
    """A resting YES bid at `price` is hit when the ask trades down to it."""
    low = quote.yes_ask_low
    return low is not None and low <= price + 1e-9


def no_fill(quote, price: float) -> bool:
    """A resting NO bid at `price` is hit when the NO ask (1 - yes_bid) trades down."""
    high = quote.yes_bid_high
    return high is not None and (1.0 - high) <= price + 1e-9


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-capture", type=float, default=0.005,
                    help="only rest a pair whose rested cost is at least this far under $1")
    ap.add_argument("--min-left", type=float, default=300.0,
                    help="seconds left required to place a pair")
    ap.add_argument("--complete", action="store_true",
                    help="when one leg fills, finish the pair by TAKING the other leg on"
                         " the next bar instead of carrying a naked position. This is what"
                         " a maker actually does: pay one spread and one fee rather than"
                         " hold direction")
    ap.add_argument("--liquid-only", action="store_true",
                    help="restrict CANDIDATES to non-cold rungs before choosing the best"
                         " pair. Filtering afterwards is the 029 trap: the cheapest pair"
                         " on the whole ladder is always the most broken quote on it")
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)

    both = Bucket("both legs filled -> locked pair")
    one = Bucket("one leg filled -> completed as taker" if "--complete" in sys.argv
                 else "one leg filled -> naked to settlement")
    combined = Bucket("ALL placed pairs (both + one + none)")
    liquid = Bucket("ALL placed pairs, liquid rungs")

    placed = filled_both = filled_one = filled_none = 0
    captures: list[float] = []

    for hour in hours:
        pairs_this_hour = 0
        for index, bar in enumerate(hour.bars):
            if pairs_this_hour >= MAX_PAIRS_PER_HOUR:
                break
            if bar.seconds_left < args.min_left:
                continue
            reference = rung_reference_volume(bar)
            strikes = sorted(bar.quotes)
            if len(strikes) < 3:
                continue

            # cheapest rested pair on this bar
            if args.liquid_only:
                strikes = [k for k in strikes
                           if liquidity_tier(bar.quotes[k].volume, reference) != "cold"]
                if len(strikes) < 3:
                    continue
            best = None
            for i, k1 in enumerate(strikes):
                yb1 = bar.quotes[k1].bid("yes")
                if yb1 is None:
                    continue
                for k2 in strikes[i + 1:]:
                    nb2 = bar.quotes[k2].bid("no")
                    if nb2 is None:
                        continue
                    cost = yb1 + nb2          # maker fee is 0 on this series
                    if best is None or cost < best[0]:
                        best = (cost, k1, k2, yb1, nb2)
            if best is None:
                continue
            cost, k1, k2, yb1, nb2 = best
            if cost > 1.0 - args.min_capture:
                continue

            placed += 1
            pairs_this_hour += 1
            captures.append((1.0 - cost) * 100.0)
            cold = (liquidity_tier(bar.quotes[k1].volume, reference) == "cold"
                    or liquidity_tier(bar.quotes[k2].volume, reference) == "cold")

            # walk forward: a rest placed here can only be hit from the NEXT bar on
            hit1 = hit2 = False
            at1 = at2 = None          # WHICH bar each leg filled on
            for offset, later in enumerate(hour.bars[index + 1:], start=index + 1):
                q1, q2 = later.quotes.get(k1), later.quotes.get(k2)
                if not hit1 and q1 is not None and yes_fill(q1, yb1):
                    hit1, at1 = True, offset
                if not hit2 and q2 is not None and no_fill(q2, nb2):
                    hit2, at2 = True, offset
                if hit1 and hit2:
                    break

            settle = hour.settle
            if hit1 and hit2:
                filled_both += 1
                extra = 1.0 if (settle is not None and k1 < settle <= k2) else 0.0
                cents = (1.0 + extra - cost) * 100.0
                both.add(hour.event_ticker, cents, True, cost, hour.close_ts)
            elif hit1 or hit2:
                filled_one += 1
                if args.complete:
                    # Finish the pair by lifting the missing leg, starting from the bar
                    # AFTER the one that actually filled us. Completing from the bar after
                    # PLACEMENT would lift the second leg before the first had filled --
                    # the same folded time axis as ADR 032, and it flatters this rule by
                    # pricing the hedge at a moment we had no reason to hedge.
                    fill_bar = at1 if hit1 else at2
                    done = None
                    for later in hour.bars[fill_bar + 1:]:
                        q1, q2 = later.quotes.get(k1), later.quotes.get(k2)
                        if hit1 and q2 is not None:
                            other = q2.ask("no")
                            if other is not None:
                                done = yb1 + other + taker_fee(other)
                                break
                        if hit2 and q1 is not None:
                            other = q1.ask("yes")
                            if other is not None:
                                done = nb2 + other + taker_fee(other)
                                break
                    if done is None:
                        continue
                    extra = 1.0 if (settle is not None and k1 < settle <= k2) else 0.0
                    cents = (1.0 + extra - done) * 100.0
                    one.add(hour.event_ticker, cents, cents > 0, done, hour.close_ts)
                else:
                    if hit1:
                        won = hour.won(k1, "yes")
                        leg_cost, leg_price = yb1, yb1
                    else:
                        won = hour.won(k2, "no")
                        leg_cost, leg_price = nb2, nb2
                    if won is None:
                        continue
                    cents = ((1.0 if won else 0.0) - leg_cost) * 100.0
                    one.add(hour.event_ticker, cents, bool(won), leg_price, hour.close_ts)
            else:
                filled_none += 1
                cents = 0.0
                combined.add(hour.event_ticker, 0.0, True, cost, hour.close_ts)
                if not cold:
                    liquid.add(hour.event_ticker, 0.0, True, cost, hour.close_ts)
                continue

            combined.add(hour.event_ticker, cents, cents > 0, cost, hour.close_ts)
            if not cold:
                liquid.add(hour.event_ticker, cents, cents > 0, cost, hour.close_ts)

    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f"  min capture {args.min_capture * 100:.1f}c, <=1 pair/hour, rests hit from t+1 on")
    if placed:
        print(f"# placed {placed}  both legs {filled_both} ({filled_both / placed:.1%})"
              f"  one leg {filled_one} ({filled_one / placed:.1%})"
              f"  neither {filled_none} ({filled_none / placed:.1%})")
        print(f"# capture if both fill: median {statistics.median(captures):+.2f}c"
              f"  mean {statistics.fmean(captures):+.2f}c")
    for bucket in (both, one, combined, liquid):
        if len(bucket):
            print("   " + bucket.result(days).row())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

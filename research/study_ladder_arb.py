"""Study 11 -- the ladder's internal consistency. Not a bet: an inequality.

Every family closed so far was directional, and section 10b explains why they all
failed the same way: the required calibration edge is 100 * taker_fee(ask), and the
whole ladder sits within +-0.15pp of that bar. But that argument only governs bets on
where BTC goes. It says nothing about whether the 188 rungs are consistent with EACH
OTHER at the same instant.

Two constraints must hold on any coherent book:

  A. monotonicity   YES(K) pays $1 if S > K, so for K1 < K2 the K1 contract pays
                    whenever the K2 one does, and sometimes more. Buying YES(K1) and
                    NO(K2) therefore pays $1 in every state and $2 when K1 < S <= K2.
                    Cost is yes_ask(K1) + no_ask(K2). A locked profit exists whenever

                        yes_ask(K1) + no_ask(K2) + fees < 1     (K1 < K2)

                    which, since no_ask(K2) = 1 - yes_bid(K2), is exactly
                    yes_ask(K1) < yes_bid(K2) - fees: the ask on the MORE likely rung
                    below the bid on the LESS likely one. Stale quotes make this.

  B. sum-to-one     YES(K) and NO(K) together always pay exactly $1, so
                    yes_ask(K) + no_ask(K) + fees < 1 is the same free lunch on one rung.

Neither requires predicting anything, so neither is subject to the fee bar -- only to
whether the violations are real, big enough to clear two taker fees, and on rungs that
could actually be filled.

Discipline, per 025-035: both legs priced at the SAME bar's close (decide and transact
at one instant), one row per (hour, pair), liquidity split, and ADR 035's tripwire in
force -- a large apparent edge here is a stale-quote artefact until shown otherwise.

    python3 research/study_ladder_arb.py
"""

from __future__ import annotations

import argparse
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-edge", type=float, default=0.0,
                    help="only count violations worth at least this many dollars net")
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)

    mono = Bucket("monotonicity: YES(K1) + NO(K2), K1<K2")
    mono_liquid = Bucket("  ... both legs liquid")
    sumone = Bucket("sum-to-one: YES(K) + NO(K) on one rung")
    sumone_liquid = Bucket("  ... liquid rung")

    scanned = pairs = 0
    seen: set = set()
    worst_gap = 0.0
    per_hour: dict[str, int] = {}
    # How close does the book come? The cheapest pair cost per bar, as a distribution.
    # Two taker legs must clear $1. A MAKER leg pays no fee on this series and rests
    # half a spread better, so if the near-misses cluster inside that improvement, the
    # family is not closed -- it is closed *to takers*.
    near: list[float] = []
    near_maker: list[float] = []

    for hour in hours:
        for bar in hour.bars:
            reference = rung_reference_volume(bar)
            strikes = sorted(bar.quotes)
            if len(strikes) < 3:
                continue
            scanned += 1

            # B. one rung, both sides.
            for strike in strikes:
                q = bar.quotes[strike]
                ya, na = q.ask("yes"), q.ask("no")
                if ya is None or na is None:
                    continue
                cost = ya + na + taker_fee(ya) + taker_fee(na)
                if cost >= 1.0 - args.min_edge:
                    continue
                key = ("B", hour.event_ticker, strike)
                if key in seen:
                    continue
                seen.add(key)
                cents = (1.0 - cost) * 100.0
                sumone.add(hour.event_ticker, cents, True, cost, hour.close_ts)
                if liquidity_tier(q.volume, reference) != "cold":
                    sumone_liquid.add(hour.event_ticker, cents, True, cost, hour.close_ts)

            # A. two rungs. Only the adjacent-and-beyond pairs that can violate:
            # cheapest yes_ask below, richest yes_bid above.
            best_bar: tuple = (float("inf"), None)
            for i, k1 in enumerate(strikes):
                q1 = bar.quotes[k1]
                ya1 = q1.ask("yes")
                if ya1 is None:
                    continue

                for k2 in strikes[i + 1:]:
                    q2 = bar.quotes[k2]
                    na2 = q2.ask("no")
                    if na2 is None:
                        continue
                    pairs += 1
                    cost = ya1 + na2 + taker_fee(ya1) + taker_fee(na2)
                    if cost < best_bar[0]:
                        # the same pair if both legs were RESTED instead of taken:
                        # no fee, and filled at the bid rather than the ask.
                        yb1, nb2 = q1.bid("yes"), q2.bid("no")
                        maker = (yb1 + nb2) if (yb1 is not None and nb2 is not None) else None
                        best_bar = (cost, maker)
                    if cost >= 1.0 - args.min_edge:
                        continue
                    key = ("A", hour.event_ticker, k1, k2)
                    if key in seen:
                        continue
                    seen.add(key)
                    # payoff is 1 always, 2 when k1 < settle <= k2
                    extra = 1.0 if (hour.settle is not None and k1 < hour.settle <= k2) else 0.0
                    cents = (1.0 + extra - cost) * 100.0
                    worst_gap = max(worst_gap, (1.0 - cost) * 100.0)
                    per_hour[hour.event_ticker] = per_hour.get(hour.event_ticker, 0) + 1
                    mono.add(hour.event_ticker, cents, True, cost, hour.close_ts)
                    if (liquidity_tier(q1.volume, reference) != "cold"
                            and liquidity_tier(q2.volume, reference) != "cold"):
                        mono_liquid.add(hour.event_ticker, cents, True, cost, hour.close_ts)

            if best_bar[0] < float("inf"):
                near.append(best_bar[0])
                if best_bar[1] is not None:
                    near_maker.append(best_bar[1])

    import statistics

    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}")
    print(f"# bars scanned {scanned}, ordered rung pairs examined {pairs}")
    print("# a violation is locked profit BEFORE settlement: payoff >= $1 in every state,")
    print("# so 'win' is 100% by construction and the number that matters is net cents.")
    for bucket in (sumone, sumone_liquid, mono, mono_liquid):
        if len(bucket):
            print("   " + bucket.result(days).row())
        else:
            print(f"   {bucket.label:<40} none found")
    if len(mono):
        print(f"   guaranteed floor on the best pair: {worst_gap:+.3f}c")
        print(f"   hours containing at least one: {len(per_hour)} of {len(hours)}")

    if near:
        near.sort()
        print("\n## how close the book comes: cheapest pair per bar, cost to clear $1.00")
        print("   (taker = both legs lifted at the ask, with both quadratic fees)")
        for label, pct in (("min", 0.0), ("p1", 1.0), ("p5", 5.0), ("median", 50.0)):
            idx = min(int(len(near) * pct / 100.0), len(near) - 1)
            print(f"   {label:>7}  ${near[idx]:.4f}   ({(near[idx] - 1.0) * 100:+.2f}c from free)")
        under = sum(1 for c in near if c < 1.0)
        print(f"   bars whose best taker pair is already under $1: {under} of {len(near)}"
              f"  ({under / len(near):.4%})")
    if near_maker:
        near_maker.sort()
        print("\n## the same pairs RESTED instead of taken (maker fee is 0 on this series)")
        print("   (this is an upper bound: it assumes both rests fill, which 015/027 is")
        print("    exactly the reason to distrust -- it bounds the family, it does not open it)")
        for label, pct in (("min", 0.0), ("p1", 1.0), ("p5", 5.0), ("median", 50.0)):
            idx = min(int(len(near_maker) * pct / 100.0), len(near_maker) - 1)
            print(f"   {label:>7}  ${near_maker[idx]:.4f}"
                  f"   ({(near_maker[idx] - 1.0) * 100:+.2f}c from free)")
        under = sum(1 for c in near_maker if c < 1.0)
        print(f"   bars whose best rested pair would be under $1: {under} of {len(near_maker)}"
              f"  ({under / len(near_maker):.4%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Study 2 -- candidate entry rules on the KXBTCD hourly ladder.

Every rule is one entry per (event, strike, side) at the first minute it triggers,
taker at the ask, and by default held to settlement. The grid is deliberately small and
pre-listed so the Bonferroni threshold stays honest (15m repo lesson: a 600-rule walk
forward maximises noise).

Families
--------
`favorite`   ask band + minute window, no spot input at all -- the pure calibration play.
`cushion`    the C1 port: |ln(S/K)| >= Z residual-vol sigmas AND the ask band.
`tail`       last N minutes only, deep favorite -- the T5 shape.
`coupon`     the current `impulse_wait` band bought as taker -- negative control.
`cheap`      buy the longshot side -- negative control.

Exits
-----
`--exits` re-prices identical entries under the 10%-50% clip band (both an optimistic
intra-minute touch and a conservative minute-close fill) so hold-vs-clip is measured on
the same trades rather than on two different samples.

    python3 research/study_candidates.py --family cushion
    python3 research/study_candidates.py --exits
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.fees import lock_exit_price, round_trip_roi, taker_fee  # noqa: E402
from research.hourly_lab import (  # noqa: E402
    Bar,
    DEFAULT_DB,
    Hour,
    Trade,
    close_trade,
    cushion,
    load_hours,
    model_p,
    sample_days,
    settle_trade,
    summarize,
)


@dataclass(frozen=True)
class Rule:
    name: str
    lo_ask: float
    hi_ask: float
    min_minute: int = 5
    max_minute: int = 55
    min_cushion: float = 0.0
    max_cushion: float = 99.0
    min_volume: float = 1.0
    max_per_event: int = 3
    with_spot: bool = True       # side must agree with spot-vs-strike
    max_distance: float = 0.0    # 0 = no cap
    min_seconds_left: float = 120.0
    min_gap: float = 0.0         # model p - ask, the repo's own `swing_min_gap`
    slip_ticks: int = 0          # pay N extra cents: the "book moved" robustness check

    def label(self) -> str:
        return self.name


def run_rule(hours: list[Hour], rule: Rule) -> list[Trade]:
    trades: list[Trade] = []
    for hour in hours:
        taken: set[tuple[float, str]] = set()
        count = 0
        for bar in hour.bars:
            if count >= rule.max_per_event:
                break
            if not (rule.min_minute <= bar.minute <= rule.max_minute):
                continue
            if bar.seconds_left < rule.min_seconds_left:
                continue
            # Widest cushion first, exactly like `strategy.pick_cushion`. Sorting by
            # strike instead would quietly bias every cap toward the YES side.
            rungs = sorted(bar.quotes.items(), key=lambda kv: -cushion(bar, kv[0]))
            for strike, quote in rungs:
                if count >= rule.max_per_event:
                    break
                if quote.volume < rule.min_volume:
                    continue
                if rule.max_distance and abs(bar.spot - strike) > rule.max_distance:
                    continue
                z = cushion(bar, strike)
                if not (rule.min_cushion <= z <= rule.max_cushion):
                    continue
                for side in ("yes", "no"):
                    if (strike, side) in taken:
                        continue
                    if rule.with_spot and side != ("yes" if bar.spot > strike else "no"):
                        continue
                    ask = quote.ask(side)
                    if ask is None or not (rule.lo_ask <= ask <= rule.hi_ask):
                        continue
                    p = model_p(bar, strike, side)
                    if rule.min_gap and p - ask + 1e-12 < rule.min_gap:
                        continue
                    paid = ask + 0.01 * rule.slip_ticks
                    if paid >= 1.0:
                        continue
                    trade = settle_trade(
                        hour, bar, strike, side, paid, rule.name,
                        model_p=p, cushion=z,
                    )
                    if trade is None:
                        continue
                    trades.append(trade)
                    taken.add((strike, side))
                    count += 1
                    break
    return trades


# --------------------------------------------------------------------------- exits


def replay_exit(
    hour: Hour,
    trade: Trade,
    target: float,
    stop: float,
    cap: float,
    optimistic: bool,
) -> Trade:
    """Re-price one held trade under the repo's clip band / hard stop.

    `optimistic` uses the intra-minute best bid (the touch upper bound the 15m repo
    warns about); otherwise only the minute-close bid can fill.
    """
    cost = trade.entry + taker_fee(trade.entry)
    floor_price = lock_exit_price(cost, 1.0, target)
    cap_price = lock_exit_price(cost, 1.0, cap)
    peak: float | None = None
    for bar in hour.bars:
        if bar.ts <= trade.ts:
            continue
        quote = bar.quotes.get(trade.strike)
        if quote is None:
            continue
        close_bid = quote.bid(trade.side)
        if optimistic:
            # yes bid high is the YES best bid; the NO bid peak is 1 - yes ask low.
            best = quote.yes_bid_high if trade.side == "yes" else (
                None if quote.yes_ask_low is None else round(1.0 - quote.yes_ask_low, 4)
            )
        else:
            best = close_bid
        if best is None:
            continue
        if cap_price is not None and best + 1e-12 >= cap_price:
            return close_trade(trade, cap_price, "t_clip_cap")
        if close_bid is not None:
            roi = round_trip_roi(cost, close_bid)
            if roi + 1e-12 <= -stop:
                return close_trade(trade, close_bid, "t_stop")
            peak = close_bid if peak is None else max(peak, close_bid)
            if (
                floor_price is not None
                and peak + 1e-12 >= floor_price
                and peak - close_bid + 1e-12 >= 0.04
            ):
                return close_trade(trade, close_bid, "t_trail")
    return trade  # never triggered: held to settlement


def replay_lock_on_book(hour: Hour, trade: Trade, target: float) -> Trade:
    """The `lock_on_book` branch: leave only when the bid already locks `target` ROI.

    Unlike the clip band this never sells at a loss, so it is the one early exit worth
    measuring against hold-to-settlement on a favorite.
    """
    cost = trade.entry + taker_fee(trade.entry)
    price = lock_exit_price(cost, 1.0, target)
    if price is None:
        return trade
    for bar in hour.bars:
        if bar.ts <= trade.ts:
            continue
        quote = bar.quotes.get(trade.strike)
        if quote is None:
            continue
        bid = quote.bid(trade.side)
        if bid is not None and bid + 1e-12 >= price:
            return close_trade(trade, price, f"lock_on_book_{target:.0%}")
    return trade


def with_lock_on_book(hours: list[Hour], trades: list[Trade], target: float) -> list[Trade]:
    by_event = {hour.event_ticker: hour for hour in hours}
    return [
        trade if by_event.get(trade.event_ticker) is None
        else replay_lock_on_book(by_event[trade.event_ticker], trade, target)
        for trade in trades
    ]


def with_exits(hours: list[Hour], trades: list[Trade], optimistic: bool, target=0.10, stop=0.12, cap=0.50) -> list[Trade]:
    by_event = {hour.event_ticker: hour for hour in hours}
    out = []
    for trade in trades:
        hour = by_event.get(trade.event_ticker)
        out.append(trade if hour is None else replay_exit(hour, trade, target, stop, cap, optimistic))
    return out


# --------------------------------------------------------------------------- grids


def grid(family: str) -> list[Rule]:
    rules: list[Rule] = []
    if family in ("favorite", "all"):
        for lo, hi in ((0.82, 0.90), (0.86, 0.94), (0.90, 0.95), (0.90, 0.97), (0.95, 0.99)):
            rules.append(Rule(f"fav {lo:.2f}-{hi:.2f} m5-55", lo, hi))
    if family in ("cushion", "all"):
        for z in (1.0, 1.5, 2.0, 2.5):
            for lo, hi in ((0.55, 0.90), (0.70, 0.90), (0.82, 0.94), (0.86, 0.96)):
                rules.append(Rule(f"cush z>={z} {lo:.2f}-{hi:.2f}", lo, hi, min_cushion=z))
    if family in ("tail", "all"):
        for lo, hi in ((0.90, 0.97), (0.95, 0.99), (0.97, 0.99)):
            rules.append(Rule(f"tail {lo:.2f}-{hi:.2f} last10m", lo, hi, min_minute=45, min_seconds_left=120))
    if family in ("coupon", "all"):
        rules.append(Rule("coupon 0.32-0.42 taker", 0.32, 0.42, with_spot=False, max_distance=600))
        rules.append(Rule("coupon 0.28-0.42 with-tape", 0.28, 0.42, with_spot=True, max_distance=600))
    if family in ("cheap", "all"):
        rules.append(Rule("cheap 0.18-0.30 anti-tape", 0.18, 0.30, with_spot=False, max_distance=600))
    if family == "robust":
        # One shape, stressed on the things that usually kill a paper edge:
        # stale quotes (volume floor), a book that moved (slippage), and crowding.
        base = dict(lo_ask=0.55, hi_ask=0.90, min_cushion=1.5)
        for volume in (1.0, 50.0, 200.0, 1000.0):
            rules.append(Rule(f"vol>={volume:.0f}", min_volume=volume, **base))
        for slip in (1, 2):
            rules.append(Rule(f"slip +{slip}c", slip_ticks=slip, **base))
        for cap in (1, 2, 5):
            rules.append(Rule(f"max {cap}/hour", max_per_event=cap, **base))
        for lo, hi in ((5, 30), (30, 55), (5, 45), (15, 55)):
            rules.append(Rule(f"minutes {lo}-{hi}", min_minute=lo, max_minute=hi, **base))
        for gap in (0.02, 0.05, 0.08):
            rules.append(Rule(f"gap>={gap:.2f}", min_gap=gap, **base))
    if family == "zscan":
        for z in (1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0):
            rules.append(Rule(f"z>={z} 0.55-0.90", 0.55, 0.90, min_cushion=z))
    if family == "hicap":
        for hi in (0.85, 0.88, 0.90, 0.92, 0.95):
            rules.append(Rule(f"z>=1.5 0.55-{hi:.2f}", 0.55, hi, min_cushion=1.5))
    if family == "final":
        # Pre-registered short list. Nine rules, so the Bonferroni bar stays low enough
        # that a survivor means something (phase19: |R| is what a walk-forward maximises).
        rules += [
            Rule("A cushion 1.5 / 0.70-0.90", 0.70, 0.90, min_cushion=1.5),
            Rule("B cushion 1.5 / 0.70-0.95", 0.70, 0.95, min_cushion=1.5),
            Rule("C cushion 2.0 / 0.70-0.90", 0.70, 0.90, min_cushion=2.0),
            Rule("D cushion 1.2 / 0.70-0.90", 0.70, 0.90, min_cushion=1.2),
            Rule("E cushion 1.5 / 0.82-0.94", 0.82, 0.94, min_cushion=1.5),
            Rule("F gap>=0.05 / 0.70-0.90", 0.70, 0.90, min_gap=0.05),
            Rule("G deep 0.90-0.98 no signal", 0.90, 0.98, min_cushion=0.0),
            Rule("H coupon 0.32-0.42 taker", 0.32, 0.42, with_spot=False, max_distance=600),
            Rule("I cheap 0.18-0.30", 0.18, 0.30, with_spot=False, max_distance=600),
        ]
    return rules


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"],
                    help="calendar half: fit on early, validate on late")
    ap.add_argument("--family", default="all")
    ap.add_argument("--exits", action="store_true", help="also re-price entries under the clip band")
    ap.add_argument("--split", action="store_true", help="report a calendar discovery/holdout split")
    ap.add_argument("--slip", type=int, default=0, help="pay N extra ticks on entry")
    ap.add_argument("--max-per-event", type=int, default=3)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    rules = [
        Rule(**{**rule.__dict__, "max_per_event": args.max_per_event, "slip_ticks": args.slip})
        for rule in grid(args.family)
    ]
    bonf = 1.96 if len(rules) <= 1 else _bonferroni(len(rules))
    print(f"# hours={len(hours)} days={days:.1f} rules={len(rules)} Bonferroni |t| >= {bonf:.3f}")
    print("# hold to settlement, taker entry, one entry per (event,strike,side)")

    for rule in rules:
        trades = run_rule(hours, rule)
        if len(trades) < 60:
            print(f"   {rule.label():<34} n={len(trades):>6}  (too few)")
            continue
        held = summarize(trades, rule.label(), days)
        print("   " + held.row() + f" $/day={daily_dollars(trades, days):>+6.2f}" + ("  **" if abs(held.t) >= bonf else ""))
        if args.split:
            cut = hours[len(hours) // 2].close_ts
            early = [t for t in trades if t.close_ts < cut]
            late = [t for t in trades if t.close_ts >= cut]
            half = days / 2
            for tag, subset in (("discovery", early), ("holdout ", late)):
                if len(subset) >= 30:
                    print("      " + summarize(subset, f"  ↳ {tag}", half).row()
                          + f" $/day={daily_dollars(subset, half):>+6.2f}")
        if args.exits:
            for optimistic, tag in ((True, "clip-touch"), (False, "clip-close")):
                clipped = with_exits(hours, trades, optimistic)
                print("      " + summarize(clipped, f"  ↳ {tag}", days).row()
                      + f" $/day={daily_dollars(clipped, days):>+6.2f}")
    return 0


def daily_dollars(trades: list[Trade], days: float) -> float:
    """One contract per signal: dollars per calendar day over the sample."""
    if days <= 0:
        return 0.0
    return sum(t.net for t in trades) / days


def _bonferroni(k: int) -> float:
    """Two-sided normal quantile for alpha=0.05/k (Acklam-free bisection)."""
    alpha = 0.05 / k
    target = 1.0 - alpha / 2.0
    lo, hi = 0.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 0.5 * (1.0 + math.erf(mid / math.sqrt(2.0))) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


if __name__ == "__main__":
    raise SystemExit(main())

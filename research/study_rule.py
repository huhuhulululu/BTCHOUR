"""Study 4 -- freeze one rule and stress it: risk, capacity, decay, negative controls.

This is the phase19 discipline from the 15m repo applied to the hour ladder. A rule that
survives a grid is not a rule yet; it has to survive:

  halves / thirds      does the edge exist in every slice of the calendar
  slippage             +1c and +2c on entry
  liquidity            candle volume floors (a paper quote nobody traded is not a fill)
  crowding             1 vs 3 vs 5 contracts per hour
  hour of day          is it one session's artefact
  shuffle null         reshuffle settlement inside the sample; the edge must vanish
  risk                 daily P&L, worst day, max drawdown, longest losing streak

    python3 research/study_rule.py
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import DEFAULT_DB, Trade, load_hours, sample_days, summarize  # noqa: E402
from research.study_candidates import Rule, run_rule, with_exits, with_lock_on_book  # noqa: E402

FROZEN = Rule(
    name="cushion_hold z>=1.5 0.70-0.90",
    lo_ask=0.70,
    hi_ask=0.90,
    min_minute=5,
    max_minute=55,
    min_cushion=1.5,
    min_volume=1.0,
    max_per_event=3,
    with_spot=True,
    min_seconds_left=120.0,
)


def daily(trades: list[Trade]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for trade in trades:
        day = datetime.fromtimestamp(trade.close_ts, timezone.utc).strftime("%Y-%m-%d")
        out[day] += trade.net
    return dict(out)


def risk_table(trades: list[Trade], hours) -> list[str]:
    per_day = daily(trades)
    all_days = sorted(
        {datetime.fromtimestamp(h.close_ts, timezone.utc).strftime("%Y-%m-%d") for h in hours}
    )
    series = [per_day.get(day, 0.0) for day in all_days]
    if not series:
        return ["   (no days)"]
    equity, peak, mdd = 0.0, 0.0, 0.0
    for value in series:
        equity += value
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    losing = sum(1 for value in series if value < 0)
    streak = worst = 0
    for trade in sorted(trades, key=lambda t: t.close_ts):
        streak = streak + 1 if not trade.won else 0
        worst = max(worst, streak)
    return [
        f"   days={len(series)}  mean=${statistics.fmean(series):+.2f}/day  "
        f"median=${statistics.median(series):+.2f}  worst=${min(series):+.2f}  best=${max(series):+.2f}",
        f"   losing days={losing}/{len(series)} ({losing / len(series):.0%})  "
        f"total=${sum(series):+.2f}  max drawdown=${mdd:+.2f}  longest losing streak={worst}",
    ]


def shuffle_null(hours, rule: Rule, rounds: int, seed: int) -> tuple[float, float]:
    """Re-run with settlement results permuted across hours; the edge must die."""
    rng = random.Random(seed)
    originals = [dict(hour.results) for hour in hours]
    means = []
    for _ in range(rounds):
        pool = [dict(r) for r in originals]
        rng.shuffle(pool)
        for hour, results in zip(hours, pool):
            hour.results = results
        trades = run_rule(hours, rule)
        means.append(statistics.fmean([t.cents for t in trades]) if trades else 0.0)
    for hour, results in zip(hours, originals):
        hour.results = results
    means.sort()
    return (statistics.fmean(means), means[int(0.95 * (len(means) - 1))])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"],
                    help="calendar half: fit on early, validate on late")
    ap.add_argument("--null-rounds", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--z", type=float, default=None, help="override min cushion")
    ap.add_argument("--lo", type=float, default=None, help="override the ask floor")
    ap.add_argument("--hi", type=float, default=None, help="override the ask cap")
    ap.add_argument("--min-left", type=float, default=None, help="override min seconds left")
    ap.add_argument("--max-minute", type=int, default=None, help="override the last entry minute")
    args = ap.parse_args(argv)

    frozen = FROZEN
    overrides = {
        "min_cushion": args.z,
        "lo_ask": args.lo,
        "hi_ask": args.hi,
        "min_seconds_left": args.min_left,
        "max_minute": args.max_minute,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}
    if overrides:
        frozen = Rule(**{**FROZEN.__dict__, **overrides})
        frozen = Rule(**{
            **frozen.__dict__,
            "name": (
                f"cushion_hold z>={frozen.min_cushion} {frozen.lo_ask:.2f}-{frozen.hi_ask:.2f}"
                f" m{frozen.min_minute}-{frozen.max_minute} left>={frozen.min_seconds_left:.0f}s"
            ),
        })

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    trades = run_rule(hours, frozen)
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}  rule={frozen.name}")
    print("\n## headline (taker in, hold to settlement)")
    print("   " + summarize(trades, "frozen", days).row())

    print("\n## calendar thirds")
    ordered = sorted(trades, key=lambda t: t.close_ts)
    cut = len(ordered) // 3
    for i, tag in enumerate(("T1", "T2", "T3")):
        chunk = ordered[i * cut : (i + 1) * cut] if i < 2 else ordered[2 * cut :]
        if chunk:
            print("   " + summarize(chunk, tag, days / 3).row())

    print("\n## slippage")
    for slip in (0, 1, 2):
        rule = Rule(**{**frozen.__dict__, "slip_ticks": slip, "name": f"+{slip}c"})
        print("   " + summarize(run_rule(hours, rule), f"+{slip}c entry", days).row())

    print("\n## liquidity floor (candle volume)")
    for volume in (1.0, 50.0, 200.0, 1000.0):
        rule = Rule(**{**frozen.__dict__, "min_volume": volume, "name": f"vol>={volume:.0f}"})
        print("   " + summarize(run_rule(hours, rule), f"volume >= {volume:.0f}", days).row())

    print("\n## strike-selection safety")
    print("   (the puller keeps rungs within $1200 of the window's spot *range*, which is")
    print("    future information at decision time. Capping distance from the spot at the")
    print("    decision minute keeps every candidate inside the band from spot alone.)")
    for distance in (500.0, 700.0, 900.0):
        rule = Rule(**{**frozen.__dict__, "max_distance": distance, "name": f"|S-K|<={distance:.0f}"})
        print("   " + summarize(run_rule(hours, rule), f"|spot-strike| <= ${distance:.0f}", days).row())

    print("\n## contracts per hour")
    for cap in (1, 2, 3, 5):
        rule = Rule(**{**frozen.__dict__, "max_per_event": cap, "name": f"cap{cap}"})
        sub = run_rule(hours, rule)
        print("   " + summarize(sub, f"max {cap}/hour", days).row()
              + f" $/day={sum(t.net for t in sub) / days:+.2f}")

    print("\n## exits on identical entries (hold is the benchmark above)")
    for optimistic, tag in ((True, "clip band (touch)"), (False, "clip band (close)")):
        print("   " + summarize(with_exits(hours, trades, optimistic), tag, days).row())
    for target in (0.08, 0.12, 0.20):
        print("   " + summarize(with_lock_on_book(hours, trades, target), f"lock_on_book {target:.0%}", days).row())

    print("\n## by hour of day (UTC)")
    by_hour: dict[int, list[Trade]] = defaultdict(list)
    for trade in trades:
        by_hour[datetime.fromtimestamp(trade.close_ts, timezone.utc).hour].append(trade)
    for hour_utc in sorted(by_hour):
        chunk = by_hour[hour_utc]
        if len(chunk) >= 25:
            print("   " + summarize(chunk, f"{hour_utc:02d}:00 UTC", days).row())

    print("\n## risk (1 contract per signal)")
    for line in risk_table(trades, hours):
        print(line)

    if args.null_rounds:
        print(f"\n## shuffled-settlement null ({args.null_rounds} rounds)")
        mean, p95 = shuffle_null(hours, frozen, args.null_rounds, args.seed)
        observed = statistics.fmean([t.cents for t in trades])
        print(f"   null mean={mean:+.3f}c  null 95th={p95:+.3f}c  observed={observed:+.3f}c"
              f"  -> {'PASS' if observed > p95 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

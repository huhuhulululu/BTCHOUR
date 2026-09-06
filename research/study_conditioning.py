"""Study 10 -- the last untested signal families: cross-hour state, session, vol regime.

Every family closed so far was measured *inside* the hour: price band, moneyness, impulse,
volume, two legs, the coupon. Three conditioners have never been tested at all, and each
is the kind a human would try first:

  A. cross-hour   did the previous hour settle up or down? does the ladder carry that in
  B. session      ET hour of day, grouped Asia / Europe / US open / US late
  C. vol regime   terciles of the trailing annualised vol at the decision point

Section 10b says what any of them would have to deliver: the required calibration edge is
100 * taker_fee(ask), the cheap side is short by 1.6-4.5pp and the strong side ties. A
conditioner does not have to beat the market, it has to move calibration by more than the
fee -- so the question is whether any of these shifts the *whole ladder's* calibration by
a couple of points, not whether some cell looks green.

Design is deliberately the boring one, because 025-030 were all sampling accidents:

  * ONE decision bar per hour (minute 30). Dedup is then structural, not a post-hoc fix,
    and no rung can be counted for dwelling anywhere.
  * YES side only, and only rungs whose ask sits in [0.30, 0.70]. Taking both sides of
    a rung measures `yes_ask + no_ask - 1` -- the book's width, which is near
    deterministic and swamps any signal (it produced t=-162 on a first pass, which is
    what a spread measurement looks like, not an edge). One side of the ATM region is
    the directional quantity a conditioner would have to move.
  * clustered by event, both calendar halves, and a liquid-rungs-only row (ADR 029).
  * Bonferroni over all cells tested at once, printed as the bar rather than left to the
    reader.

    python3 research/study_conditioning.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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

ET = ZoneInfo("America/New_York")
DECISION_SECONDS = 1800.0
BAND_LO, BAND_HI = 0.30, 0.70  # the ATM region: where a directional edge would show


def session_of(close_ts: int) -> str:
    hour = datetime.fromtimestamp(close_ts, timezone.utc).astimezone(ET).hour
    if 4 <= hour < 9:
        return "B europe"
    if 9 <= hour < 13:
        return "C us open"
    if 13 <= hour < 18:
        return "D us late"
    return "A asia"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-n", type=int, default=250)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    by_close = {h.close_ts: h for h in hours}

    # vol terciles across hours, measured at the decision bar only
    vols: list[float] = []
    picked: dict[str, object] = {}
    for hour in hours:
        bar = min(hour.bars, key=lambda b: abs(b.seconds_left - DECISION_SECONDS))
        if abs(bar.seconds_left - DECISION_SECONDS) > 180:
            continue
        picked[hour.event_ticker] = bar
        vols.append(bar.annual_vol)
    if len(vols) < 30:
        print("not enough hours with a decision bar")
        return 0
    lo_cut, hi_cut = statistics.quantiles(vols, n=3)

    cells: dict[str, Bucket] = {}
    liquid: dict[str, Bucket] = {}

    for hour in hours:
        bar = picked.get(hour.event_ticker)
        if bar is None:
            continue
        # The label must come from settlements that are already public when this bar
        # trades: the PREVIOUS hour against the one before it. Comparing `hour.settle`
        # to the previous hour labels the bucket with the outcome being predicted, and
        # it shows up as a +20pp calibration edge that is purely the leak. Same folded
        # time axis as ADR 032, in this file, an hour after writing that ADR.
        prev = by_close.get(hour.close_ts - 3600)
        prev2 = by_close.get(hour.close_ts - 7200)
        if prev is None or prev2 is None or prev.settle is None or prev2.settle is None:
            prev_label = None
        else:
            prev_label = "A prev up" if prev.settle > prev2.settle else "A prev down"
        session = session_of(hour.close_ts)
        vol_label = ("C vol low" if bar.annual_vol <= lo_cut else
                     "C vol high" if bar.annual_vol >= hi_cut else "C vol mid")
        reference = rung_reference_volume(bar)

        for strike, quote in bar.quotes.items():
            if quote.yes_bid is None or quote.yes_ask is None or quote.volume < 1.0:
                continue
            cold = liquidity_tier(quote.volume, reference) == "cold"
            ask = quote.ask("yes")
            won = hour.won(strike, "yes")
            if ask is None or won is None or not (BAND_LO <= ask <= BAND_HI):
                continue
            cents = net_cents(ask, won)
            labels = ["ALL", session, vol_label]
            # the previous hour's own settlement is known before this bar opens
            if prev_label:
                labels.append(prev_label)
            for label in labels:
                cells.setdefault(label, Bucket(label)).add(
                    hour.event_ticker, cents, won, ask, hour.close_ts)
                if not cold:
                    liquid.setdefault(label, Bucket(label + " [liquid]")).add(
                        hour.event_ticker, cents, won, ask, hour.close_ts)

    tested = [k for k in cells if k != "ALL" and len(cells[k]) >= args.min_n]
    bar_t = 2.77 if len(tested) <= 10 else 3.0
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f"  one bar per hour at T-{DECISION_SECONDS / 60:.0f}m")
    print(f"# vol terciles: <= {lo_cut:.3f} / >= {hi_cut:.3f}"
          f"   YES side only, ask in [{BAND_LO:.2f}, {BAND_HI:.2f}]")
    print(f"# {len(tested)} cells tested -> Bonferroni bar is |t| > {bar_t:.2f},"
          f" not 1.96. Nothing below that bar is a finding.")

    for table, name in ((cells, "all rungs"), (liquid, "liquid rungs only")):
        print(f"\n## {name}")
        for label in sorted(table):
            bucket = table[label]
            if len(bucket) >= args.min_n:
                result = bucket.result(days)
                flag = "  <-- clears Bonferroni" if abs(result.t) > bar_t else ""
                print("   " + result.row() + flag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

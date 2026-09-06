"""Study 14 -- signed taker flow. The first genuinely new INPUT since the archive itself.

ADR 016-042 closed twelve families, all on the same inputs: 1-minute OHLC of the top of
book. `volume` was in there, and ADR 023/029 measured it -- but volume is UNSIGNED. It
says a rung traded; it cannot say whether someone lifted the offer or hit the bid. Those
are different questions and only the second was ever asked.

`/markets/trades` publishes `taker_side` per trade. Aggressor imbalance is the most
studied short-horizon predictor in microstructure, and it is the one input on this
exchange that a participant reading only candles does not have.

The question, stated so it can fail: at a decision bar, does trailing signed flow on a
rung predict that rung's settlement BEYOND what its own ask already implies? Calibration,
not direction -- the ask is allowed to be right.

Every guard this repo has learned, applied by construction rather than checked afterwards:

  * flow is summed strictly BEFORE the decision timestamp (modes 6/8, the folded time
    axis -- the defect that cost this repo ADR 032 and 039)
  * ONE decision bar per hour, so dedup is structural, not a post-hoc fix (modes 1/3/5)
  * liquid rungs filtered BEFORE the sample is formed, never after (modes 4/9)
  * YES side only, ATM band -- taking both sides measures the spread, which is near
    deterministic and swamped the first cut of ADR 035
  * no min/max over candidates (mode 9)
  * clustered SE by event, both calendar halves, Bonferroni printed in the header
  * ADR 035's tripwire: over 2pp of calibration edge on this ladder is a leak until
    proven otherwise, and section 10b says the whole ladder sits within +-0.15pp of its
    own fee bar

    python3 research/study_flow.py
    python3 research/study_flow.py --slice early
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
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

DECISION_SECONDS = 1800.0
LOOKBACK = 600.0                 # seconds of trailing flow summed before the decision
BAND_LO, BAND_HI = 0.30, 0.70    # the ATM region: where a directional edge would show
IMBALANCE_EDGES = [(-1.01, -0.5), (-0.5, -0.2), (-0.2, 0.2), (0.2, 0.5), (0.5, 1.01)]


def bucket_of(value, edges):
    for lo, hi in edges:
        if lo <= value < hi:
            return (lo, hi)
    return None


def load_flow(db: Path, windows: dict) -> dict:
    """(event, strike) -> (signed, gross) summed over that event's decision window.

    Aggregated in SQL, not in Python. The trades table holds 27.3M rows; materialising
    them as per-rung lists costs several GB and buys nothing, because every caller wants
    one window per event anyway. The window bounds come from the caller so the time filter
    stays in one place -- and so it stays strictly BEFORE the decision bar.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict = {}
    for event_ticker, (lo, hi) in windows.items():
        for strike, signed, gross in conn.execute(
            "SELECT strike,"
            "       SUM(CASE WHEN taker_side='yes' THEN count ELSE -count END),"
            "       SUM(ABS(count))"
            "  FROM trades"
            " WHERE event_ticker=? AND ts>=? AND ts<?"
            " GROUP BY strike",
            (event_ticker, int(lo), int(hi)),
        ):
            out[(event_ticker, float(strike))] = (float(signed or 0.0), float(gross or 0.0))
    conn.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-n", type=int, default=150)
    ap.add_argument("--lookback", type=float, default=LOOKBACK)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)

    # Pick each hour's decision bar first, so the flow window is defined by the bar we
    # will actually trade on -- strictly [bar.ts - lookback, bar.ts).
    decision: dict = {}
    for hour in hours:
        bar = min(hour.bars, key=lambda b: abs(b.seconds_left - DECISION_SECONDS))
        if abs(bar.seconds_left - DECISION_SECONDS) <= 180:
            decision[hour.event_ticker] = bar
    windows = {ev: (bar.ts - args.lookback, bar.ts) for ev, bar in decision.items()}
    flow = load_flow(args.db, windows)
    if not flow:
        print("no trades stored yet -- run research/pull_trades.py first")
        return 0

    cells: dict = {}
    rows = 0
    covered = 0
    imbalances: list[float] = []

    for hour in hours:
        bar = decision.get(hour.event_ticker)
        if bar is None:
            continue
        reference = rung_reference_volume(bar)
        for strike, quote in bar.quotes.items():
            if quote.yes_bid is None or quote.yes_ask is None or quote.volume < 1.0:
                continue
            if liquidity_tier(quote.volume, reference) == "cold":
                continue
            ask = quote.ask("yes")
            won = hour.won(strike, "yes")
            if ask is None or won is None or not (BAND_LO <= ask <= BAND_HI):
                continue
            rows += 1

            # summed strictly BEFORE the decision bar's timestamp, in SQL
            entry = flow.get((hour.event_ticker, strike))
            if entry is None:
                continue
            signed, gross = entry
            if gross <= 0:
                continue
            covered += 1
            imbalance = signed / gross
            imbalances.append(imbalance)
            key = bucket_of(imbalance, IMBALANCE_EDGES)
            if key is None:
                continue
            label = f"flow {key[0]:+.1f}..{key[1]:+.1f}"
            cells.setdefault(label, Bucket(label)).add(
                hour.event_ticker, net_cents(ask, won), won, ask, hour.close_ts)

    tested = [k for k in cells if len(cells[k]) >= args.min_n]
    bar_t = 2.77 if len(tested) <= 10 else 3.2
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f"  decision T-{DECISION_SECONDS/60:.0f}m, trailing flow {args.lookback/60:.0f}m")
    print(f"# YES side, ask in [{BAND_LO:.2f},{BAND_HI:.2f}], liquid rungs, one bar per hour")
    print(f"# rung-decisions {rows}, of which {covered} had trades in the window"
          f" ({covered/max(rows,1):.0%})")
    if imbalances:
        print(f"# imbalance: median {statistics.median(imbalances):+.3f}"
              f"  mean {statistics.fmean(imbalances):+.3f}")
    print(f"# {len(tested)} cells -> Bonferroni |t| > {bar_t:.2f}."
          f" Over 2pp calibration is a leak (035), not an edge.")

    print("\n## net per contract by trailing signed-flow imbalance")
    for label in sorted(cells):
        bucket = cells[label]
        if len(bucket) >= args.min_n:
            result = bucket.result(days)
            flag = "  <-- clears Bonferroni" if abs(result.t) > bar_t else ""
            print("   " + result.row() + flag)

    print("\n## the monotonicity check: does calibration ORDER with flow?")
    print("   (a real aggressor signal should be monotone, not one lucky cell)")
    ordered = []
    for lo, hi in IMBALANCE_EDGES:
        label = f"flow {lo:+.1f}..{hi:+.1f}"
        if label in cells and len(cells[label]) >= args.min_n:
            ordered.append((lo, cells[label].result(days).calib_pp))
    for lo, calib in ordered:
        print(f"   flow >= {lo:+.1f}: calib {calib:+.2f}pp")
    if len(ordered) >= 3:
        rises = sum(1 for a, b in zip(ordered, ordered[1:]) if b[1] > a[1])
        print(f"   monotone steps up: {rises} of {len(ordered)-1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

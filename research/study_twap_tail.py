"""Study 9 -- validate the branch of ADR 019 that was derived but never measured.

`model.twap_variance_seconds` has two branches. Settlement is the mean of the last
T = 60 seconds of BRTI, so for a Brownian path the variance of (settle - spot_now) is

    tau >= T :  tau - 2T/3          the TWAP shaves 2/3 of a window off the clock
    tau <  T :  tau^3 / (3 T^2)     inside the window, most of the average is already fixed

ADR 019 shipped both and said plainly that the second one is *derived, not validated*:
every study in research/ works off 1-minute candles, so the sample starts at tau=120s and
has nothing to say about tau < 60. That branch is what governs the model near
`flatten_seconds`, which is exactly where the loop makes its last decisions.

The `spot` table is a 10-second grid, six observations inside the final minute, so it can
test what the candles cannot. Settlement truth is Kalshi's own `expiration_value`, so no
TWAP proxy enters the measurement (the grid reproduces it to a mean of +$0.46, but that
is a sanity check, not an input).

Standardise the move and the branch is right if the spread is 1.0:

    z = (settle - spot(tau)) / (spot * vol * sqrt(variance_seconds(tau) / year))

Reported against the naive model that ignores the TWAP entirely (variance_seconds = tau),
which is what this repo used before 019.

    python3 research/study_twap_tail.py
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.model import TWAP_SECONDS, realized_annual_vol, variance_seconds  # noqa: E402
from research.hourly_lab import DEFAULT_DB, VOL_FLOOR  # noqa: E402

YEAR = 365.25 * 24 * 3600.0
# The last two grid points (close_ts and close_ts-10) are `expiration_value` itself in
# 300/300 events -- the live_data endpoint echoes the settlement instead of giving two
# more BRTI ticks. Anything inside 20s compares settlement to itself, so the grid is cut
# there and tau starts at 20. ADR 034.
SETTLE_ECHO_SECONDS = 20
TAUS = [20, 30, 40, 50, 60, 90, 120, 300, 600, 1200]


def spot_at(grid: dict[int, float], ts: int) -> float | None:
    bucket = (ts // 10) * 10
    for back in range(0, 7):
        value = grid.get(bucket - 10 * back)
        if value is not None:
            return value
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    events = conn.execute(
        "SELECT event_ticker, close_ts, settle_value FROM events"
        " WHERE settle_value IS NOT NULL ORDER BY close_ts"
    ).fetchall()
    if args.limit:
        events = events[-args.limit:]

    z_model: dict[int, list[float]] = {tau: [] for tau in TAUS}
    z_naive: dict[int, list[float]] = {tau: [] for tau in TAUS}

    for ev, close_ts, settle in events:
        close_ts, settle = int(close_ts), float(settle)
        grid = {int(t): float(v) for t, v in conn.execute(
            "SELECT ts, value FROM spot WHERE event_ticker=? AND ts<=? ORDER BY ts",
            (ev, close_ts - SETTLE_ECHO_SECONDS))}
        if len(grid) < 120:
            continue
        for tau in TAUS:
            at = close_ts - tau
            spot = spot_at(grid, at)
            if spot is None:
                continue
            # trailing 15 one-minute closes off the same grid, the lab's vol convention
            closes = [spot_at(grid, at - 60 * k) for k in range(15, -1, -1)]
            closes = [c for c in closes if c]
            if len(closes) < 8:
                continue
            vol = realized_annual_vol(closes, 60.0) or VOL_FLOOR
            minute = int(round((3600 - tau) / 60))
            move = settle - spot
            for table, seconds in ((z_model, variance_seconds(tau, minute)), (z_naive, float(tau))):
                sigma = spot * vol * math.sqrt(max(seconds, 1e-9) / YEAR)
                if sigma > 0:
                    table[tau].append(move / sigma)

    print(f"# events={len(events)}  TWAP window T={TWAP_SECONDS:.0f}s")
    print("# a correct variance model puts sd(z) at 1.00; below 1 means the model is too")
    print("# wide (it over-predicts the remaining move), above 1 means too narrow")
    print(f"   {'tau':>6} {'branch':>9} {'n':>6} {'sd(z) 019':>10} {'sd(z) naive':>12}"
          f" {'mean(z)':>9}")
    for tau in TAUS:
        rows = z_model[tau]
        if len(rows) < 100:
            continue
        branch = "tau^3/3T^2" if tau < TWAP_SECONDS else "tau-2T/3"
        print(f"   {tau:>5}s {branch:>9} {len(rows):>6} {statistics.pstdev(rows):>10.3f}"
              f" {statistics.pstdev(z_naive[tau]):>12.3f} {statistics.fmean(rows):>+9.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

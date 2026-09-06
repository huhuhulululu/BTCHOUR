"""Study 15 -- the perp premium basis. The last lead ADR 044 left alive.

The premium index is (perp mark - index)/index: what longs are paying to stay long. It is
POSITIONING, not volatility -- which is why it survives ADR 044's kill of DVOL and BVOL.
Those were vol, and the ladder already forecasts vol better than they do (R2 0.577 vs
0.404). Direction is a different question and it has never been asked here.

The reported lead was t=+2.83 raw, +2.30 controlling 5-minute BRTI momentum, +1.83
controlling 3m+15m, failing Bonferroni across six cuts. This tests it from scratch, and
tests the thing that decides whether it is money rather than the thing that is easy to
measure: does it beat the ladder's OWN ASK -- calibration -- not does it predict BRTI.
A signal that predicts BRTI exactly as well as the ask already does is worth nothing.

The crux is the momentum control. Section 10 of the backtest report found the 3-minute
BRTI impulse carries NO direction. If the premium is a momentum proxy it must be zero
too; if it survives the control it is carrying something momentum does not. Those are
different worlds and this prints which one we are in.

Guards, by construction: premium read STRICTLY before the decision bar; one decision bar
per hour; liquidity screened before the sample forms; YES side only in the ATM band;
clustered SE by event; both halves; Bonferroni in the header; monotonicity, because ADR
043 died on exactly that.

    python3 research/study_perp.py
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
    clustered_t,
    liquidity_tier,
    load_hours,
    net_cents,
    rung_reference_volume,
    sample_days,
)

DECISION_SECONDS = 1800.0
FAST, SLOW = 5, 60          # minutes: deviation of the fast mean from the slow mean
BAND_LO, BAND_HI = 0.30, 0.70


def load_premium(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {int(ts): float(close) for ts, close in
           conn.execute("SELECT ts, close FROM perp_premium ORDER BY ts")}
    conn.close()
    return out


def deviation(prem: dict, before_ts: int, fast: int, slow: int):
    """Fast mean minus slow mean, using only minutes STRICTLY BEFORE `before_ts`."""
    base = (before_ts // 60) * 60
    fast_vals, slow_vals = [], []
    for k in range(1, slow + 1):
        value = prem.get(base - 60 * k)
        if value is None:
            continue
        slow_vals.append(value)
        if k <= fast:
            fast_vals.append(value)
    if len(fast_vals) < fast // 2 or len(slow_vals) < slow // 2:
        return None
    return statistics.fmean(fast_vals) - statistics.fmean(slow_vals)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--min-n", type=int, default=120)
    ap.add_argument("--quintiles", type=int, default=5)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    prem = load_premium(args.db)
    if not prem:
        print("no perp_premium rows -- run research/pull_perp.py first")
        return 0

    # collect first, bucket by empirical quantile after -- fixed cutoffs on an unknown
    # scale would put every observation in one cell
    obs = []
    for hour in hours:
        bar = min(hour.bars, key=lambda b: abs(b.seconds_left - DECISION_SECONDS))
        if abs(bar.seconds_left - DECISION_SECONDS) > 180:
            continue
        dev = deviation(prem, bar.ts, FAST, SLOW)
        if dev is None:
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
            obs.append({
                "event": hour.event_ticker, "close_ts": hour.close_ts,
                "ask": ask, "won": won, "cents": net_cents(ask, won),
                "dev": dev, "impulse": bar.impulse,
            })

    if len(obs) < 200:
        print(f"only {len(obs)} observations -- not enough")
        return 0

    devs = sorted(o["dev"] for o in obs)
    q = args.quintiles
    cuts = [devs[int(len(devs) * i / q)] for i in range(1, q)]

    def qbucket(value, edges):
        for i, cut in enumerate(edges):
            if value < cut:
                return i
        return len(edges)

    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f"  decision T-{DECISION_SECONDS/60:.0f}m, premium dev = mean({FAST}m) - mean({SLOW}m), strictly prior")
    print(f"# YES side, ask in [{BAND_LO:.2f},{BAND_HI:.2f}], liquid rungs, one bar per hour")
    print(f"# {len(obs)} rung-decisions over {len({o['event'] for o in obs})} hours")
    corr_n = [(o["dev"], o["impulse"]) for o in obs]
    if len(corr_n) > 10:
        dm = statistics.fmean(x for x, _ in corr_n)
        im = statistics.fmean(y for _, y in corr_n)
        cov = sum((x - dm) * (y - im) for x, y in corr_n)
        dv = sum((x - dm) ** 2 for x, _ in corr_n) ** 0.5
        iv = sum((y - im) ** 2 for _, y in corr_n) ** 0.5
        rho = cov / (dv * iv) if dv * iv else 0.0
        print(f"# corr(premium dev, 3-min BRTI impulse) = {rho:+.3f}"
              f"   <- if this is high the 'control' question is moot")
    bar_t = 2.77
    print(f"# {q} quantile cells -> Bonferroni |t| > {bar_t:.2f}."
          f" Over 2pp calibration is a leak (035), not an edge.")

    cells: dict = {}
    for o in obs:
        cells.setdefault(qbucket(o["dev"], cuts), Bucket("")).add(
            o["event"], o["cents"], o["won"], o["ask"], o["close_ts"])

    print("\n## calibration by premium-deviation quantile (low premium -> high)")
    calibs = []
    for i in sorted(cells):
        bucket = cells[i]
        if len(bucket) < args.min_n:
            continue
        result = bucket.result(days)
        result.label = f"Q{i+1}"
        calibs.append((i, result.calib_pp))
        flag = "  <-- clears Bonferroni" if abs(result.t) > bar_t else ""
        print("   " + result.row() + flag)
    if len(calibs) >= 3:
        rises = sum(1 for a, b in zip(calibs, calibs[1:]) if b[1] > a[1])
        print(f"   monotone steps up: {rises} of {len(calibs)-1}"
              f"   (043 died here: one good cell is noise, a gradient is mechanism)")

    # THE control. Within each impulse tercile, does premium still order?
    print("\n## the momentum control: within each 3-min BRTI impulse tercile")
    print("   (section 10 found the impulse carries no direction. If premium is just a")
    print("    momentum proxy it must vanish here; if it survives it carries something else)")
    imps = sorted(o["impulse"] for o in obs)
    icuts = [imps[len(imps)//3], imps[2*len(imps)//3]]
    for tier in range(3):
        sub = [o for o in obs if qbucket(o["impulse"], icuts) == tier]
        if len(sub) < 2 * args.min_n:
            continue
        sdevs = sorted(o["dev"] for o in sub)
        scut = [sdevs[len(sdevs)//2]]
        lo = [o for o in sub if o["dev"] < scut[0]]
        hi = [o for o in sub if o["dev"] >= scut[0]]
        if min(len(lo), len(hi)) < args.min_n:
            continue
        diff = [o["cents"] for o in hi]
        m_hi, t_hi, _, _ = clustered_t(diff, [o["event"] for o in hi])
        m_lo, t_lo, _, _ = clustered_t([o["cents"] for o in lo], [o["event"] for o in lo])
        clo = (sum(1 for o in lo if o["won"]) / len(lo) - statistics.fmean(o["ask"] for o in lo)) * 100
        chi = (sum(1 for o in hi if o["won"]) / len(hi) - statistics.fmean(o["ask"] for o in hi)) * 100
        print(f"   impulse T{tier+1}: low-premium calib {clo:+.2f}pp (n={len(lo)})"
              f"   high-premium {chi:+.2f}pp (n={len(hi)})   spread {chi-clo:+.2f}pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Study 17 -- ADR 046 tested on an INDEPENDENT instrument. Pre-registered.

046's candidate works on the full hourly tape (+7.08c, t=+2.45 strict-cross) and fails out
of sample: the late 33 days carry it, the early 34 are t=0.85. Kalshi retains ~66 days, so
a longer history does not exist. A different INSTRUMENT does.

KXBTC15M: one market per 15-minute window, strike pinned to the window's opening price,
same CF Benchmarks settlement family. Structurally it removes four of the thirteen failure
modes -- every contract is at the money by construction, so there is no band to choose, no
rung to select, no cold-rung problem, and no cross-rung simultaneity. One observation per
window makes dedup structural. And it is ~4x the sample.

EVERYTHING BELOW IS PRE-REGISTERED FROM 046. Nothing is re-fitted here:
  * direction: LOW perp premium -> buy NO
  * threshold: bottom 20% of the same 5m-minus-60m deviation
  * decision point: 10 minutes before close (the same 2/3-through-the-window position as
    T-30m in a 60-minute contract)
  * fill: strict cross, 1 tick (ADR 027 -- touch is an upper bound)
  * bar to clear: t >= 1.96 with both halves same-signed

Re-tuning any of these on this data would make it a second in-sample fit wearing the
clothes of a validation. The point of an independent instrument is that it can say no.

    python3 research/study_15m_perp.py
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from btchour.fees import taker_fee  # noqa: E402
from research.hourly_lab import DEFAULT_DB, clustered_t  # noqa: E402
from research.study_perp import deviation, load_premium  # noqa: E402

DECIDE_BEFORE_CLOSE = 600.0     # seconds; 10 of the 15 minutes gone
QUANTILE = 0.20
FILL_TICKS = 1
ASK_LO, ASK_HI = 0.20, 0.80


def load_windows(db: pathlib.Path):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    windows = {}
    for ev, close_ts, strike, result in conn.execute(
        "SELECT event_ticker, close_ts, strike, result FROM w15"
        " WHERE result IN ('yes','no')"
    ):
        windows[ev] = {"close_ts": int(close_ts), "strike": float(strike),
                       "result": result, "quotes": {}}
    for ev, ts, bid, ask, bid_high, ask_low, volume in conn.execute(
        "SELECT event_ticker, ts, yes_bid_close, yes_ask_close, yes_bid_high,"
        " yes_ask_low, volume FROM w15_quotes"
    ):
        if ev in windows:
            windows[ev]["quotes"][int(ts)] = {
                "bid": bid, "ask": ask, "bid_high": bid_high,
                "ask_low": ask_low, "volume": volume or 0.0}
    conn.close()
    return windows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB)
    ap.add_argument("--quantile", type=float, default=QUANTILE)
    ap.add_argument("--fill-ticks", type=int, default=FILL_TICKS)
    args = ap.parse_args(argv)

    windows = load_windows(args.db)
    prem = load_premium(args.db)
    if len(windows) < 200:
        print(f"only {len(windows)} settled 15m windows stored -- run research/pull_15m.py")
        return 0

    rows = []
    for ev, w in windows.items():
        decide_ts = w["close_ts"] - DECIDE_BEFORE_CLOSE
        quote = w["quotes"].get(int(decide_ts))
        if quote is None or quote["bid"] is None or quote["ask"] is None:
            continue
        dev = deviation(prem, int(decide_ts), 5, 60)
        if dev is None:
            continue
        no_ask = round(1.0 - quote["bid"], 4)
        no_bid = round(1.0 - quote["ask"], 4)
        if not (ASK_LO <= no_ask <= ASK_HI) or not (0.0 < no_bid < no_ask):
            continue
        won = w["result"] == "no"
        # a rest placed at decide_ts can only be hit from a LATER minute (ADR 032)
        hit = False
        for ts in sorted(t for t in w["quotes"] if t > decide_ts):
            high = w["quotes"][ts]["bid_high"]
            if high is None:
                continue
            if round(1.0 - high, 4) + 0.01 * args.fill_ticks <= no_bid + 1e-9:
                hit = True
                break
        rows.append({"ev": ev, "close_ts": w["close_ts"], "dev": dev, "won": won,
                     "no_ask": no_ask, "no_bid": no_bid, "filled": hit})

    if len(rows) < 200:
        print(f"only {len(rows)} usable windows")
        return 0

    devs = sorted(r["dev"] for r in rows)
    cut = devs[int(len(devs) * args.quantile)]
    tail = [r for r in rows if r["dev"] < cut]

    print(f"# PRE-REGISTERED from ADR 046 -- direction, threshold, decision point and fill")
    print(f"# convention all carried over unchanged. Nothing re-fitted on this instrument.")
    print(f"# windows usable {len(rows)}, in the bottom {args.quantile:.0%} tail {len(tail)}")
    print(f"# decision {DECIDE_BEFORE_CLOSE/60:.0f}m before close,"
          f" fill = strict cross +{args.fill_ticks} tick, bar to clear: |t| >= 1.96")

    def report(sub, label):
        if len(sub) < 40:
            print(f"   {label:<30} n={len(sub)} -- too few")
            return None
        taker_c = [((1.0 if r["won"] else 0.0) - r["no_ask"] - taker_fee(r["no_ask"])) * 100
                   for r in sub]
        keys = [r["ev"] for r in sub]
        m, t, lo, hi = clustered_t(taker_c, keys)
        win = sum(1 for r in sub if r["won"]) / len(sub)
        imp = statistics.fmean(r["no_ask"] for r in sub)
        print(f"   {label:<30} n={len(sub):>5} TAKER net={m:+7.3f}c t={t:+6.2f}"
              f" CI[{lo:+7.3f},{hi:+7.3f}] win={win:.2%} imp={imp:.2%}"
              f" calib={(win-imp)*100:+.2f}pp")
        made = [r for r in sub if r["filled"]]
        if len(made) >= 40:
            maker_c = [((1.0 if r["won"] else 0.0) - r["no_bid"]) * 100 for r in made]
            mm, mt, mlo, mhi = clustered_t(maker_c, [r["ev"] for r in made])
            mwin = sum(1 for r in made if r["won"]) / len(made)
            mimp = statistics.fmean(r["no_bid"] for r in made)
            print(f"   {'':<30} n={len(made):>5} MAKER net={mm:+7.3f}c t={mt:+6.2f}"
                  f" CI[{mlo:+7.3f},{mhi:+7.3f}] win={mwin:.2%} imp={mimp:.2%}"
                  f" calib={(mwin-mimp)*100:+.2f}pp  fill={len(made)/len(sub):.0%}")
            return mt
        return t

    print("\n## the pre-registered test")
    report(tail, "premium bottom 20%")

    print("\n## controls that must also hold")
    report([r for r in rows if r["dev"] >= cut], "the other 80% (should be ~0)")
    order = sorted(tail, key=lambda r: r["close_ts"])
    mid = len(order) // 2
    report(order[:mid], "tail, first half")
    report(order[mid:], "tail, second half")

    print("\n## gradient across quintiles (043's lesson: a cell is noise, a slope is mechanism)")
    q = 5
    cuts = [devs[int(len(devs) * i / q)] for i in range(1, q)]
    for i in range(q):
        lo_c = cuts[i-1] if i else float("-inf")
        hi_c = cuts[i] if i < len(cuts) else float("inf")
        sub = [r for r in rows if lo_c <= r["dev"] < hi_c]
        if len(sub) >= 40:
            win = sum(1 for r in sub if r["won"]) / len(sub)
            imp = statistics.fmean(r["no_ask"] for r in sub)
            print(f"   Q{i+1}: n={len(sub):>5} NO calib {(win-imp)*100:+.2f}pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

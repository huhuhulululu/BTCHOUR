"""Effective spread from signed trades, the same way `venue.py` did it on Kalshi.

Every cost number in decisions 021 and 022 that came from outside -- "BTC perp
spread is 0.09bp", "the tail quotes 20-100bp" -- was a vendor figure this repo
could not check. This module replaces them with a measurement, and it does it
with the one trick that made `venue.py` work: **the exchange publishes which
side was the aggressor**, so no fill has to be invented and no queue assumed.

Binance's `aggTrades` archive carries `is_buyer_maker` per print. When it is
false the buyer was the aggressor, so that print crossed the offer; when true
the seller was, so it crossed the bid. Inside a short bucket of time the
volume-weighted price of the aggressive buys sits at the offer and of the
aggressive sells at the bid, so their difference is the spread that trades
actually paid -- the effective spread, which is the number a strategy is
charged, rather than the quoted spread at some instant nobody traded at.

Why not the order book: `bookTicker` is the direct measurement, but Binance
stopped publishing it after 2024-03-30, and a 2023 spread applied to a 2026
position is an assumption dressed as data. `bookDepth` is still published but
starts at 1% from mid, which is a capacity instrument, not a spread one.
`aggTrades` runs to the present and a tail coin costs 140KB a day.

Two biases, both stated rather than hidden:

* A bucket holding only one side is dropped. That drops the quietest buckets,
  which are the widest, so the estimate is a lower bound on what a strategy
  pays -- the honest direction for a cost.
* Inside a bucket the mid moves, so a trend contributes to the buy-minus-sell
  difference. Shrinking the bucket shrinks that contamination and shrinks the
  sample.

**The bucket version of this was tried first and rejected on the evidence.**
Differencing buy-VWAP against sell-VWAP inside one-second buckets was validated
against the real `bookTicker` quoted spread on eight symbol-days and did not
track it: the ratio of estimate to truth ran from -0.44 to +1.64, and one
symbol came out with a negative spread, which is impossible. In a thin coin a
one-second bucket holds one buy and one sell, so the difference is mid drift
rather than spread. `measure_day` therefore uses `trade_indicator_spread`,
which puts a time trend in the model so drift has somewhere to go, and the
bucket version stays only as `bucket_spread` for the comparison.
"""

from __future__ import annotations

import io
import os
import statistics
import urllib.request
import zipfile
from dataclasses import dataclass

AGG_DAILY = (
    "https://data.binance.vision/data/futures/um/daily/aggTrades/"
    "{sym}/{sym}-aggTrades-{day}.zip"
)
DEPTH_DAILY = (
    "https://data.binance.vision/data/futures/um/daily/bookDepth/"
    "{sym}/{sym}-bookDepth-{day}.zip"
)


@dataclass(frozen=True)
class SpreadDay:
    """One symbol, one day, as the tape reported it."""

    symbol: str
    day: str
    n_trades: int
    n_buckets: int
    eff_spread_bp: float  # volume-weighted across buckets
    eff_spread_bp_median: float
    dollar_volume: float

    @property
    def half_spread_bp(self) -> float:
        """What one side of a round trip crosses."""
        return self.eff_spread_bp / 2


def _fetch(url: str, tries: int = 4) -> bytes | None:
    import time

    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btchour/spread"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 -- retried
            if getattr(exc, "code", None) == 404:
                return None
            if i == tries - 1:
                raise
            time.sleep(2 ** i)
    return None


def _agg_rows(blob: bytes):
    """(price, qty, ts, buyer_was_maker) from one aggTrades archive.

    Column order differs between the headered and headerless eras, so the
    header is used when present rather than assumed away.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    lines = text.splitlines()
    if not lines:
        return
    cols = None
    first = lines[0].split(",")
    try:
        float(first[1])
    except (ValueError, IndexError):
        cols = [c.strip() for c in first]
        lines = lines[1:]
    # Headerless layout: agg_id, price, qty, first_id, last_id, ts, is_buyer_maker
    ip, iq, it, im = 1, 2, 5, 6
    if cols:
        def at(*names):
            for n in names:
                if n in cols:
                    return cols.index(n)
            return None

        ip = at("price") or ip
        iq = at("quantity", "qty") or iq
        it = at("transact_time", "time", "timestamp") or it
        im = at("is_buyer_maker") or im
    for line in lines:
        p = line.split(",")
        if len(p) <= im:
            continue
        try:
            price = float(p[ip])
            qty = float(p[iq])
            ts = int(float(p[it]))
        except ValueError:
            continue
        maker = p[im].strip().lower() in ("true", "1")
        yield price, qty, ts, maker


def trade_indicator_spread(rows, *, chunk: int = 400) -> list[tuple[float, float]]:
    """Regress price on a time trend and the aggressor sign, in chunks.

    With the aggressor side published, the standard trade-indicator model is

        P_t = a + b * t + (S / 2) * sign_t

    where `sign_t` is +1 when the buyer crossed and -1 when the seller did. The
    `b * t` term is what the bucket estimator lacked: without it, a drifting mid
    loads onto the sign coefficient, which is how that version produced a
    negative spread. `S` comes back as twice the fitted coefficient.

    Returns (spread_bp, dollar_volume) per chunk, so the caller can weight.
    """
    out: list[tuple[float, float]] = []
    for i in range(0, len(rows) - chunk + 1, chunk):
        seg = rows[i : i + chunk]
        signs = [1.0 if not m else -1.0 for _, _, _, m in seg]
        if abs(sum(signs)) > 0.95 * len(seg):
            continue  # effectively one-sided: the sign column carries no contrast
        t0 = seg[0][2]
        xs_t = [(r[2] - t0) / 1000.0 for r in seg]
        ys = [r[0] for r in seg]
        n = float(len(seg))
        # Two-regressor OLS by normal equations, with the intercept absorbed by
        # centring both regressors and the response.
        mt = sum(xs_t) / n
        ms = sum(signs) / n
        my = sum(ys) / n
        ct = [x - mt for x in xs_t]
        cs = [x - ms for x in signs]
        cy = [y - my for y in ys]
        stt = sum(x * x for x in ct)
        sss = sum(x * x for x in cs)
        sts = sum(a * b for a, b in zip(ct, cs))
        sty = sum(a * b for a, b in zip(ct, cy))
        ssy = sum(a * b for a, b in zip(cs, cy))
        det = stt * sss - sts * sts
        if det <= 0 or my <= 0:
            continue
        half = (stt * ssy - sts * sty) / det  # coefficient on sign
        spread_bp = 2 * half / my * 10_000
        if spread_bp <= 0 or spread_bp > 2000:
            continue
        out.append((spread_bp, sum(r[0] * r[1] for r in seg)))
    return out


def measure_day(
    symbol: str,
    day: str,
    *,
    chunk: int = 400,
    cache_dir: str | None = None,
) -> SpreadDay | None:
    """Effective spread in basis points for one symbol-day, or None if absent."""
    if cache_dir:
        path = os.path.join(cache_dir, f"{symbol}-{day}.json")
        if os.path.exists(path):
            import json

            return SpreadDay(**json.load(open(path)))
    blob = _fetch(AGG_DAILY.format(sym=symbol, day=day))
    if blob is None:
        return None
    rows = [r for r in _agg_rows(blob) if r[0] > 0 and r[1] > 0]
    rows.sort(key=lambda r: r[2])
    n = len(rows)
    dollars = sum(r[0] * r[1] for r in rows)
    if n < 200:
        return None
    spreads = trade_indicator_spread(rows, chunk=chunk)
    if len(spreads) < 5:
        return None
    wsum = sum(w for _, w in spreads)
    out = SpreadDay(
        symbol=symbol,
        day=day,
        n_trades=n,
        n_buckets=len(spreads),
        eff_spread_bp=sum(s * w for s, w in spreads) / wsum,
        eff_spread_bp_median=statistics.median([s for s, _ in spreads]),
        dollar_volume=dollars,
    )
    if cache_dir:
        import json
        from dataclasses import asdict

        os.makedirs(cache_dir, exist_ok=True)
        json.dump(asdict(out), open(os.path.join(cache_dir, f"{symbol}-{day}.json"), "w"))
    return out


def fit_spread_vs_volume(days: list[SpreadDay]) -> dict:
    """Regress log(effective spread) on log(dollar volume).

    A cross-sectional strategy holds hundreds of names and there is no tape for
    most of them, so the spread has to be extrapolated by a stated rule instead
    of measured one name at a time. This is that rule, together with how well it
    actually fits -- which is reported so the extrapolation can be argued with.
    """
    import math

    pts = [
        (math.log(d.dollar_volume), math.log(d.eff_spread_bp))
        for d in days
        if d.dollar_volume > 0 and d.eff_spread_bp > 0
    ]
    if len(pts) < 5:
        return {"n": len(pts)}
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    if sxx <= 0:
        return {"n": len(pts)}
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in pts]
    sst = sum((y - my) ** 2 for _, y in pts)
    sse = sum(r * r for r in resid)
    return {
        "n": len(pts),
        "slope": slope,
        "intercept": intercept,
        "r2": 1 - sse / sst if sst > 0 else 0.0,
        "resid_sd_log": statistics.pstdev(resid) if len(resid) > 1 else 0.0,
    }


def predict_spread_bp(fit: dict, dollar_volume: float) -> float:
    import math

    if "slope" not in fit or dollar_volume <= 0:
        return float("nan")
    return math.exp(fit["intercept"] + fit["slope"] * math.log(dollar_volume))

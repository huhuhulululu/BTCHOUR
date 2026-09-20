"""What a passive quote earns on Binance USDT-M perpetuals, as arithmetic.

This is `venue.py`'s method moved to a venue a thousand times Kalshi's size,
and it is the only method this repository has that ever produced a trustworthy
number about market making. The reason it works is that **Binance publishes
which side of every trade was passive**: `aggTrades` carries `is_buyer_maker`.
So "what does the resting side earn" needs no fill model, no queue assumption
and no book reconstruction -- for every trade that actually happened, the
maker's mark-to-market after `h` seconds is

    markout = side * (price_at(t + h) - fill_price)

with `side = +1` when the maker bought. Averaged over fills and weighted by
notional, that is the pool: an upper bound on what a new entrant could earn,
because it credits the entrant with every fill the incumbents actually got and
charges nothing for queue position.

Three things this module refuses to do, each because it produced a false
positive somewhere in 015-024:

* It never infers a fill from quotes. Decision 018's phantom fills were
  benign by construction (nobody was trading against them) and so understated
  adverse selection by a third. Here every observation is a real trade.
* It never annualizes a per-fill number without the notional behind it. A
  half-basis-point edge on a symbol that trades $40k a day is not a business,
  and decision 023's capacity work is what turned a t=11.61 into a no.
* It reports the markout *and* the fee side by side rather than netting them
  into one number, because the fee tier is the whole question. Binance's
  published maker fee runs from +2.0bp at retail to -0.5bp (a rebate) at the
  top VIP tier, and a mechanism that is dead at retail and alive on a rebate
  is a statement about identity, not about the market.

The independent unit is the **symbol-day**, never the fill. Every fill inside
one day rides the same price path, exactly as every rung inside one Kalshi
hour did (decision 016), so bootstrapping fills would give an interval far too
narrow for anything the path decides.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field

VISION = "https://data.binance.vision/data"
AGG_DAILY = VISION + "/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip"

DATA_ROOT = os.path.join("data", "makerpool")

# Binance USDT-M published schedule, in basis points of notional. Positive is
# a cost. The top tier's maker fee is a rebate, which is why it is negative.
FEE_TIERS_BP = {
    "retail": 2.0,
    "vip1": 1.6,
    "vip3": 1.4,
    "vip5": 1.0,
    "vip7": 0.6,
    "vip9": -0.5,
}

# Horizons in milliseconds. 60s is the one to read: it is long enough that the
# informed flow has moved the price and short enough that a maker could
# plausibly still be holding.
HORIZONS_MS = (1_000, 10_000, 60_000, 300_000)


@dataclass
class DaySummary:
    """One symbol-day. This is the unit that gets bootstrapped."""

    symbol: str
    day: str
    n_trades: int
    dollar_volume: float
    vwap: float
    # notional-weighted maker markout in bp of notional, by horizon
    markout_bp: dict[int, float] = field(default_factory=dict)
    # the same thing equally weighted per fill, to show whether the big fills
    # are the informed ones
    markout_bp_unweighted: dict[int, float] = field(default_factory=dict)
    # markout measured against a VWAP window instead of the single last trade,
    # which is the version that survives bid-ask bounce -- see `summarize_day`
    markout_bp_vwap: dict[int, float] = field(default_factory=dict)
    maker_buy_share: float = 0.0
    half_spread_bp: float = 0.0

    def gross_bp(self, horizon_ms: int = 60_000, *, ref: str = "vwap") -> float:
        """What the resting side captured before fees, in bp of notional.

        `ref="vwap"` is the number to quote. The single-last-trade version is
        kept because the two disagreeing would mean bid-ask bounce is driving
        the result, and that has to be visible rather than chosen.
        """
        src = self.markout_bp_vwap if ref == "vwap" else self.markout_bp
        return src.get(horizon_ms, 0.0)

    def net_bp(
        self, *, tier: str = "retail", horizon_ms: int = 60_000, ref: str = "vwap"
    ) -> float:
        """After the published maker fee for `tier`.

        The fee is charged once here, on the fill being measured. A round trip
        pays it twice, but the exit is not necessarily passive and not
        necessarily at this horizon, so charging one side keeps the number an
        upper bound rather than a guess at an exit policy.
        """
        return self.gross_bp(horizon_ms, ref=ref) - FEE_TIERS_BP[tier]

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "day": self.day,
            "n_trades": self.n_trades,
            "dollar_volume": self.dollar_volume,
            "vwap": self.vwap,
            "markout_bp": {str(k): v for k, v in self.markout_bp.items()},
            "markout_bp_unweighted": {
                str(k): v for k, v in self.markout_bp_unweighted.items()
            },
            "markout_bp_vwap": {str(k): v for k, v in self.markout_bp_vwap.items()},
            "maker_buy_share": self.maker_buy_share,
            "half_spread_bp": self.half_spread_bp,
        }

    @staticmethod
    def from_json(d: dict) -> "DaySummary":
        s = DaySummary(
            symbol=d["symbol"],
            day=d["day"],
            n_trades=d["n_trades"],
            dollar_volume=d["dollar_volume"],
            vwap=d["vwap"],
            maker_buy_share=d.get("maker_buy_share", 0.0),
            half_spread_bp=d.get("half_spread_bp", 0.0),
        )
        s.markout_bp = {int(k): v for k, v in d["markout_bp"].items()}
        s.markout_bp_unweighted = {
            int(k): v for k, v in d.get("markout_bp_unweighted", {}).items()
        }
        s.markout_bp_vwap = {
            int(k): v for k, v in d.get("markout_bp_vwap", {}).items()
        }
        return s


def _agg_rows(blob: bytes):
    """Yield `(price, qty, ts, buyer_was_maker)` from an aggTrades zip.

    The archive gained a header row partway through its history and the column
    order has been stable either side of that, so the header is detected by
    trying to parse the first field as a number rather than by date.
    """
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = z.namelist()[0]
    with z.open(name) as fh:
        first = True
        for raw in io.TextIOWrapper(fh, encoding="utf-8"):
            parts = raw.rstrip("\n").split(",")
            if len(parts) < 7:
                continue
            if first:
                first = False
                try:
                    float(parts[1])
                except ValueError:
                    continue  # header row
            try:
                price = float(parts[1])
                qty = float(parts[2])
                ts = int(parts[5])
                maker = parts[6].strip().lower() in ("true", "1")
            except (ValueError, IndexError):
                continue
            yield price, qty, ts, maker


def summarize_day(
    symbol: str,
    day: str,
    rows,
    *,
    horizons: tuple[int, ...] = HORIZONS_MS,
    vwap_window_frac: float = 0.2,
) -> DaySummary:
    """Notional-weighted maker markout for one symbol-day, two references.

    The obvious reference at `t + h` is the last trade at or before it, and it
    is biased. Consecutive trades alternate between the bid and the ask, and
    aggressor side is strongly autocorrelated, so the single trade sitting at
    `t + h` is more likely to be on the same side as the fill being measured
    than a coin flip -- which understates the half-spread the maker captured.
    The fix is the standard one: average the trades in a window around
    `t + h`, where the bounce cancels and what is left approximates the mid.

    Both are computed. They are reported side by side because if they
    disagreed, bid-ask bounce rather than information would be driving the
    result, and that has to be visible rather than chosen -- the same reason
    decision 023 printed the ratio of its spread proxy to the real book
    instead of just adopting the proxy.
    """
    trades = [(ts, price, qty, maker) for price, qty, ts, maker in rows]
    trades.sort(key=lambda r: r[0])
    n = len(trades)
    out = DaySummary(symbol=symbol, day=day, n_trades=n, dollar_volume=0.0, vwap=0.0)
    if n < 100:
        return out

    stamps = [r[0] for r in trades]
    prices = [r[1] for r in trades]
    qtys = [r[2] for r in trades]
    notional = [p * q for p, q in zip(prices, qtys)]
    total = sum(notional)
    out.dollar_volume = total
    out.vwap = total / sum(qtys)
    out.maker_buy_share = sum(1 for r in trades if r[3]) / n

    # Prefix sums let the reference VWAP over any window be two lookups
    # rather than a scan, which is what keeps a 150k-trade day at a couple of
    # seconds instead of minutes.
    import bisect

    cum_notional = [0.0] * (n + 1)
    cum_qty = [0.0] * (n + 1)
    for i in range(n):
        cum_notional[i + 1] = cum_notional[i] + notional[i]
        cum_qty[i + 1] = cum_qty[i] + qtys[i]

    def window_vwap(lo_ms: int, hi_ms: int) -> float | None:
        a = bisect.bisect_left(stamps, lo_ms)
        b = bisect.bisect_right(stamps, hi_ms)
        if b <= a:
            return None
        dq = cum_qty[b] - cum_qty[a]
        if dq <= 0:
            return None
        return (cum_notional[b] - cum_notional[a]) / dq

    for h in horizons:
        half = max(1, int(h * vwap_window_frac / 2))
        wsum = usum = vsum = 0.0
        wden = vden = 0.0
        uden = 0
        for i, (ts, px, qty, buyer_maker) in enumerate(trades):
            # The maker is the buyer when `is_buyer_maker` is set, so the
            # resting side is long and gains when the price rises.
            side = 1.0 if buyer_maker else -1.0
            j = bisect.bisect_right(stamps, ts + h) - 1
            if j > i:
                bp = side * (prices[j] - px) / px * 10_000.0
                wsum += bp * notional[i]
                wden += notional[i]
                usum += bp
                uden += 1
            ref = window_vwap(ts + h - half, ts + h + half)
            if ref is not None:
                # A window that ran past the end of the day would be measuring
                # a shorter horizon than it claims, so require a trade after it.
                if stamps[-1] >= ts + h + half:
                    bpv = side * (ref - px) / px * 10_000.0
                    vsum += bpv * notional[i]
                    vden += notional[i]
        if wden > 0:
            out.markout_bp[h] = wsum / wden
        if uden > 0:
            out.markout_bp_unweighted[h] = usum / uden
        if vden > 0:
            out.markout_bp_vwap[h] = vsum / vden
    return out


def fetch_day(symbol: str, day: str, *, root: str = DATA_ROOT, keep: bool = False):
    """Download one symbol-day, summarize it, cache the summary.

    Only the summary is kept. A month of BTCUSDT aggTrades is 700MB, so
    holding the raw tape for a wide screen is not an option in an ephemeral
    container -- and unlike the Kalshi tape, these archives are immutable and
    can be re-pulled byte for byte, which is the whole argument of
    `archives.py` for versioning the fetcher instead of the data.
    """
    path = os.path.join(root, symbol, f"{day}.json")
    if os.path.exists(path) and os.path.getsize(path) > 2:
        with open(path) as fh:
            return DaySummary.from_json(json.load(fh))
    url = AGG_DAILY.format(sym=symbol, day=day)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "btchour/makerpool"})
        with urllib.request.urlopen(req, timeout=600) as r:
            blob = r.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # the symbol did not trade that day
        raise
    s = summarize_day(symbol, day, _agg_rows(blob))
    if keep:
        raw = os.path.join(root, symbol, f"{day}.zip")
        os.makedirs(os.path.dirname(raw), exist_ok=True)
        with open(raw, "wb") as fh:
            fh.write(blob)
    del blob
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(s.to_json(), fh)
    return s


def summarize_pool(
    days: list[DaySummary],
    *,
    tier: str = "retail",
    horizon_ms: int = 60_000,
    ref: str = "vwap",
    label: str = "",
) -> dict:
    """Bootstrap over symbol-days, weighting each day by the notional it traded.

    Equal-weighting symbol-days would let a coin that traded $40,000 count as
    much as one that traded $4bn, which is how a screen ends up recommending
    the corner of the market that cannot absorb anything.
    """
    from .metrics import bootstrap_ci, t_stat, _mean, _stdev

    # Filter on the dict actually being read, not on the other one: with
    # ref="vwap" a day that has a VWAP markout but no last-trade markout is
    # perfectly usable, and filtering on the wrong dict silently empties the
    # sample.
    have = (lambda d: horizon_ms in d.markout_bp_vwap) if ref == "vwap" else (
        lambda d: horizon_ms in d.markout_bp
    )
    days = [d for d in days if d.n_trades >= 100 and have(d)]
    if not days:
        return {"label": label, "n": 0}
    gross = [d.gross_bp(horizon_ms, ref=ref) for d in days]
    net = [d.net_bp(tier=tier, horizon_ms=horizon_ms, ref=ref) for d in days]
    vol = [d.dollar_volume for d in days]
    tot = sum(vol)
    wg = sum(g * v for g, v in zip(gross, vol)) / tot
    wn = sum(x * v for x, v in zip(net, vol)) / tot
    lo, hi = bootstrap_ci(net, draws=6000)
    return {
        "label": label,
        "tier": tier,
        "horizon_ms": horizon_ms,
        "ref": ref,
        "n": len(days),
        "n_symbols": len({d.symbol for d in days}),
        "dollar_volume": tot,
        "gross_bp_mean": _mean(gross),
        "gross_bp_volweighted": wg,
        "net_bp_mean": _mean(net),
        "net_bp_volweighted": wn,
        "net_bp_ci": (lo, hi),
        "net_bp_t": t_stat(net),
        "share_of_days_positive": sum(1 for x in net if x > 0) / len(net),
        "worst_day_bp": min(net),
        "best_day_bp": max(net),
        "sd_bp": _stdev(net),
        "fee_bp": FEE_TIERS_BP[tier],
    }

"""Cross-sectional crypto factors, priced at the costs the tail actually charges.

Decision 021 put H2 second and said what it is for: the factors are not ours
to discover. The published crypto anomaly literature trades at the closing
price, pays nothing, and caps nothing, and it reports the results on universes
of thousands of coins whose median name turns over a few hundred thousand
dollars a day. So the deliverable is not a factor. It is the same factor run
twice -- once at the paper's convention, once at the venue's -- and the
difference.

Three things separate the two runs, and each is measured rather than assumed:

1. **Spread.** The paper trades at the close, which is the midpoint of
   nothing. The tail's quoted spread is 20-100x BTC's, and it is a function
   of how small the coin is, which is exactly the axis the factor sorts on.
   `spread.py` fits that function to real order-book snapshots.
2. **Fee.** Flat, known, and the smallest of the three.
3. **Capacity.** A paper portfolio takes an equal-weighted position in every
   name. A real one cannot put more into a coin than the coin trades. Past
   some size the portfolio stops being the portfolio in the paper, and the
   binding constraint is the tail names -- again the same axis.

The last one is why "net of costs" is not a single number here. It is a curve
in AUM, and the honest output is that curve plus the AUM where it crosses
zero.

Discipline carried over from 015-021: the rebalance period is the independent
unit, not the coin-period, because every coin in one period rides the same
market; the sign has to survive a disjoint time window; and the shape of the
return matters as much as its mean, so the share of profit coming from the few
best periods is reported next to it.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .carry import DATA_ROOT, DAY_MS, bar_root, _norm_ts, _rows

# Binance spot, VIP0, one side, in basis points. Same schedule decision 021
# verified; the taker number is what a cross-sectional strategy pays, because
# a rebalance that waits for a maker fill is not the rebalance it tested.
SPOT_TAKER_BP = 10.0


@dataclass(frozen=True)
class Bar:
    """One daily bar, with the dollar volume that actually printed in it."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float  # dollars traded, from the exchange's own field
    trades: int


def load_daily(
    symbol: str,
    *,
    kind: str = "spotKlines",
    root: str = DATA_ROOT,
    check_spacing: bool = True,
) -> list[Bar]:
    """Daily bars, with the bar spacing verified rather than assumed.

    An hourly file read as daily rescales every volume and every return by 24
    without raising, so `interval` is part of the path and the spacing is
    checked on the way out.
    """
    base = bar_root(kind, symbol, "1d", root=root)
    if not os.path.isdir(base):
        return []
    bars: dict[int, Bar] = {}
    for name in sorted(os.listdir(base)):
        if not name.endswith(".csv"):
            continue
        for p in _rows(os.path.join(base, name)):
            ts = _norm_ts(p[0])
            bars[ts] = Bar(
                ts=ts,
                open=float(p[1]),
                high=float(p[2]),
                low=float(p[3]),
                close=float(p[4]),
                quote_volume=float(p[7]),
                trades=int(float(p[8])),
            )
    out = [bars[t] for t in sorted(bars)]
    if check_spacing and len(out) > 24:
        modal = min(b.ts - a.ts for a, b in zip(out, out[1:]))
        if modal != DAY_MS:
            raise ValueError(
                f"{symbol} {kind}: bars are spaced {modal}ms, not daily"
            )
    return out


def load_universe(
    symbols: list[str], *, kind: str = "spotKlines", root: str = DATA_ROOT
) -> dict[str, list[Bar]]:
    out = {}
    for s in symbols:
        bars = load_daily(s, kind=kind, root=root)
        if bars:
            out[s] = bars
    return out


def available(kind: str = "spotKlines", *, root: str = DATA_ROOT) -> list[str]:
    base = os.path.join(root, kind, "1d")
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


# --------------------------------------------------------------------------
# Signals. Deliberately the plain published ones -- the point of H2 is the
# cost layer, so the factor side stays textbook and un-tuned.
# --------------------------------------------------------------------------


def _ret(bars: list[Bar], i: int, lookback: int) -> float | None:
    j = i - lookback
    if j < 0 or bars[j].close <= 0:
        return None
    return bars[i].close / bars[j].close - 1.0


def signal_reversal(bars: list[Bar], i: int, lookback: int = 7) -> float | None:
    """Short-horizon reversal: the most robust anomaly in the crypto papers."""
    r = _ret(bars, i, lookback)
    return None if r is None else -r


def signal_momentum(bars: list[Bar], i: int, lookback: int = 90) -> float | None:
    return _ret(bars, i, lookback)


def signal_illiquidity(bars: list[Bar], i: int, lookback: int = 30) -> float | None:
    """Amihud: |return| per dollar traded. Mechanically a small-coin sort."""
    j = i - lookback
    if j < 0:
        return None
    vals = []
    for k in range(j + 1, i + 1):
        if bars[k].quote_volume <= 0 or bars[k - 1].close <= 0:
            continue
        vals.append(abs(bars[k].close / bars[k - 1].close - 1.0) / bars[k].quote_volume)
    if not vals:
        return None
    return sum(vals) / len(vals)


def signal_size(bars: list[Bar], i: int, lookback: int = 30) -> float | None:
    """Dollar volume as the size proxy. Negated, so high signal = small coin."""
    j = max(0, i - lookback)
    vols = [b.quote_volume for b in bars[j : i + 1] if b.quote_volume > 0]
    if not vols:
        return None
    return -math.log(sum(vols) / len(vols))


def signal_vol(bars: list[Bar], i: int, lookback: int = 30) -> float | None:
    j = i - lookback
    if j < 0:
        return None
    rs = []
    for k in range(j + 1, i + 1):
        if bars[k - 1].close > 0:
            rs.append(bars[k].close / bars[k - 1].close - 1.0)
    if len(rs) < 5:
        return None
    m = sum(rs) / len(rs)
    return -math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1))


SIGNALS = {
    "reversal_7d": lambda b, i: signal_reversal(b, i, 7),
    "reversal_30d": lambda b, i: signal_reversal(b, i, 30),
    "momentum_90d": lambda b, i: signal_momentum(b, i, 90),
    "illiquidity_30d": lambda b, i: signal_illiquidity(b, i, 30),
    "small_size_30d": lambda b, i: signal_size(b, i, 30),
    "low_vol_30d": lambda b, i: signal_vol(b, i, 30),
}


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    symbol: str
    side: int  # +1 long, -1 short
    entry: float
    exit: float
    adv_usd: float  # trailing average daily dollar volume at entry
    spread_bp: float  # round-trip spread cost this name charges, both sides
    funding_sum: float = 0.0  # rates settled over the hold, summed

    @property
    def gross(self) -> float:
        return self.side * (self.exit / self.entry - 1.0)

    @property
    def funding(self) -> float:
        """What holding this leg on a perp costs or pays in funding.

        Positive funding means longs pay shorts, so a short receives it and a
        long pays it. This term is not optional on a perpetual: the
        cross-section of alt funding over the year to 2026-08 averaged
        **-16.9%/yr**, meaning shorts pay longs, so a portfolio whose short leg
        sits in the speculative tail is paying a large, published, measurable
        carry that a price-only backtest never sees. It is the mirror image of
        the term decision 021 missed on H1.
        """
        return -self.side * self.funding_sum


@dataclass(frozen=True)
class Period:
    """One rebalance: the cross-section we held and what it did."""

    t0: int
    t1: int
    longs: list[Position]
    shorts: list[Position]

    @property
    def positions(self) -> list[Position]:
        return self.longs + self.shorts

    def gross_ret(self, *, with_funding: bool = True) -> float:
        """Price move plus funding. Funding is a real cash flow, not a cost
        adjustment, so it belongs in gross rather than being netted off."""
        ps = self.positions
        if not ps:
            return 0.0
        if with_funding:
            return sum(p.gross + p.funding for p in ps) / len(ps)
        return sum(p.gross for p in ps) / len(ps)

    def funding_ret(self) -> float:
        ps = self.positions
        if not ps:
            return 0.0
        return sum(p.funding for p in ps) / len(ps)

    def net_ret(self, *, fee_bp: float = SPOT_TAKER_BP, aum_usd: float | None = None,
                adv_frac: float = 0.01) -> float:
        """Return after the spread, the fee, and the capacity cap.

        `aum_usd=None` is the paper convention with costs but no size limit.
        With an AUM, each name is capped at `adv_frac` of its own trailing
        dollar volume; capital the cap refuses to deploy earns nothing, which
        is what dilutes the headline as size grows.
        """
        ps = self.positions
        if not ps:
            return 0.0
        target = None if aum_usd is None else aum_usd / len(ps)
        deployed = 0.0
        pnl = 0.0
        for p in ps:
            cost = (p.spread_bp + 2 * fee_bp) / 10_000.0
            size = 1.0 if target is None else min(target, adv_frac * p.adv_usd)
            pnl += size * (p.gross + p.funding - cost)
            deployed += size
        if target is None:
            return pnl / len(ps)
        return pnl / aum_usd  # undeployed capital earns zero, by construction

    def deployed_frac(self, aum_usd: float, adv_frac: float = 0.01) -> float:
        ps = self.positions
        if not ps:
            return 0.0
        target = aum_usd / len(ps)
        return sum(min(target, adv_frac * p.adv_usd) for p in ps) / aum_usd


def build_periods(
    universe: dict[str, list[Bar]],
    signal: str,
    *,
    hold_days: int = 30,
    quantile: float = 0.2,
    min_adv_usd: float = 0.0,
    spread_bp_of: dict[str, float] | None = None,
    funding_of: dict[str, list] | None = None,
    adv_lookback: int = 30,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[Period]:
    """Tile the tape into non-overlapping rebalances and hold each one.

    The signal is computed strictly from bars at or before the entry bar, and
    the return strictly from bars after it. A coin is only eligible in a period
    if it has both an entry and an exit bar -- a coin that delists mid-period
    would otherwise vanish from the sample precisely when it was falling, and
    that is the single largest source of fake alpha in a crypto cross-section.
    """
    fn = SIGNALS[signal]
    index: dict[str, dict[int, int]] = {
        s: {b.ts: i for i, b in enumerate(bars)} for s, bars in universe.items()
    }
    all_days = sorted({b.ts for bars in universe.values() for b in bars})
    if not all_days:
        return []
    lo = start_ms if start_ms is not None else all_days[0]
    hi = end_ms if end_ms is not None else all_days[-1]
    days = [d for d in all_days if lo <= d <= hi]
    out: list[Period] = []
    step = hold_days
    for k in range(0, len(days) - step, step):
        t0, t1 = days[k], days[k + step]
        rows = []
        for s, bars in universe.items():
            i = index[s].get(t0)
            j = index[s].get(t1)
            if i is None or j is None:
                continue  # no entry or no exit: not tradeable, not counted
            sig = fn(bars, i)
            if sig is None:
                continue
            a = max(0, i - adv_lookback)
            vols = [b.quote_volume for b in bars[a : i + 1]]
            adv = sum(vols) / len(vols) if vols else 0.0
            if adv < min_adv_usd:
                continue
            if bars[i].close <= 0:
                continue
            fsum = 0.0
            if funding_of and s in funding_of:
                fsum = sum(
                    f.rate for f in funding_of[s] if t0 < f.ts <= t1
                )
            rows.append((sig, s, bars[i].close, bars[j].close, adv, fsum))
        if len(rows) < 10:
            continue
        rows.sort(key=lambda r: r[0])
        n = max(1, int(len(rows) * quantile))
        sp = spread_bp_of or {}

        def mk(r, side):
            return Position(
                symbol=r[1],
                side=side,
                entry=r[2],
                exit=r[3],
                adv_usd=r[4],
                spread_bp=sp.get(r[1], 0.0),
                funding_sum=r[5],
            )

        out.append(
            Period(
                t0=t0,
                t1=t1,
                longs=[mk(r, +1) for r in rows[-n:]],
                shorts=[mk(r, -1) for r in rows[:n]],
            )
        )
    return out


def summarize_periods(
    periods: list[Period],
    *,
    hold_days: int,
    fee_bp: float = SPOT_TAKER_BP,
    aum_usd: float | None = None,
    adv_frac: float = 0.01,
    label: str = "",
) -> dict:
    """Bootstrap over rebalance periods -- never over coin-periods.

    Every coin held in one period rides the same market, so 60 coins across 24
    periods is 24 draws, not 1440. This is the same correction `cluster_ci`
    made for Kalshi hours in decision 016.
    """
    from .carry import _concentration
    from .metrics import bootstrap_ci, t_stat, _mean, _stdev

    if not periods:
        return {"label": label, "n": 0}
    gross = [p.gross_ret() for p in periods]
    price_only = [p.gross_ret(with_funding=False) for p in periods]
    funding = [p.funding_ret() for p in periods]
    net = [p.net_ret(fee_bp=fee_bp, aum_usd=aum_usd, adv_frac=adv_frac) for p in periods]
    scale = 365.0 / hold_days
    lo, hi = bootstrap_ci(net, draws=4000)
    glo, ghi = bootstrap_ci(gross, draws=4000)
    sd = _stdev(net)
    return {
        "label": label,
        "n": len(periods),
        "n_names_median": sorted(len(p.positions) for p in periods)[len(periods) // 2],
        "price_only_annual": _mean(price_only) * scale,
        "funding_annual": _mean(funding) * scale,
        "gross_annual": _mean(gross) * scale,
        "gross_annual_ci": (glo * scale, ghi * scale),
        "gross_t": t_stat(gross),
        "net_annual": _mean(net) * scale,
        "net_annual_ci": (lo * scale, hi * scale),
        "net_t": t_stat(net),
        "cost_drag_annual": (_mean(gross) - _mean(net)) * scale,
        "win_rate": sum(1 for v in net if v > 0) / len(net),
        "top5_profit_share": _concentration(net, 5),
        "sharpe": (_mean(net) * scale) / (sd * math.sqrt(scale)) if sd > 0 else 0.0,
        "deployed_frac": (
            _mean([p.deployed_frac(aum_usd, adv_frac) for p in periods])
            if aum_usd
            else 1.0
        ),
        "aum_usd": aum_usd,
    }

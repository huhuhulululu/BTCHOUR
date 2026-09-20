"""The volatility risk premium, in two forms: model-free, then tradeable.

Decision 021's H3. It is the last of the four mechanisms still standing, and
the reason it was ranked third is that the textbook way to harvest a variance
premium -- sell options, delta-hedge -- needs a hedging model, and every time
this repo has let a model decide when it traded, the model paid it money the
exchange never would have (018, 020). So H3 is done in two layers that each
avoid that.

**Layer 1, model-free existence.** Compare Deribit's DVOL (a 30-day
forward-looking implied volatility, published) at time t against the volatility
actually realized over the following 30 days (published prices). If implied
systematically exceeds realized, the premium exists. Nothing is traded in this
layer, so nothing has to be filled, and the answer is a property of the market
rather than of a strategy.

**Layer 2, tradeable.** Sell the at-the-money straddle for a monthly expiry and
hold it to settlement. Deribit options are European and cash-settled on the
index, so

    pnl_in_coin = (call_premium + put_premium) - |S_T - K| / S_T

with the premia taken from bars that actually traded and `S_T` the settlement
index. Arithmetic again. What this layer gives up is that a held straddle is
short the *terminal* move rather than the path variance -- the two differ, and
layer 1 is what speaks to variance. What it gains is that it is a position a
person could actually have put on.

Both layers are read the way 015-023 read everything: the independent unit is
the window (or the expiry), never the day; the sign has to survive a disjoint
sub-period; and for a short-volatility strategy the left tail is not a footnote
but the entire question, so the worst outcome and the profit concentration are
reported next to the mean.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DAY_MS = 86_400_000
TBILL_ANNUAL = 0.0445  # 1-year Treasury, 2026-09-16, as in decision 023


def realized_vol(
    closes: dict[int, float],
    t0: int,
    t1: int,
    *,
    periods_per_year: float = 365.0,
    min_returns: int = 4,
) -> tuple[float, int]:
    """Annualized realized volatility of log returns over [t0, t1].

    Annualized in calendar time to match DVOL, which is a calendar-time index
    -- crypto trades every day, so there is no trading-day convention to get
    wrong here, but the factor has to be the same on both sides.

    `periods_per_year` follows the sampling of `closes`: 365 for daily bars,
    365*24 for hourly. Sampling frequency does not change what realized
    variance estimates, only how precisely it estimates it, and with only 30
    daily returns that imprecision is large enough to matter -- see the module
    note on why the variance-unit figure is the one to read.
    """
    ts = sorted(t for t in closes if t0 <= t <= t1)
    if len(ts) < min_returns + 1:
        return float("nan"), 0
    rets = []
    for a, b in zip(ts, ts[1:]):
        pa, pb = closes[a], closes[b]
        if pa > 0 and pb > 0:
            rets.append(math.log(pb / pa))
    if len(rets) < min_returns:
        return float("nan"), 0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * periods_per_year) * 100, len(rets)


@dataclass(frozen=True)
class VolWindow:
    """One non-overlapping 30-day window: implied going in, realized coming out."""

    currency: str
    t0: int
    t1: int
    implied: float  # DVOL close at t0, annualized percent
    realized: float  # annualized percent over (t0, t1]
    n_returns: int

    @property
    def vol_premium(self) -> float:
        """Implied minus realized, in volatility points."""
        return self.implied - self.realized

    @property
    def var_premium(self) -> float:
        """Implied minus realized in *variance* units, which is what a short
        variance position is actually paid. Scaled back to percent-squared/1e4
        so it reads on the same order as the vol premium."""
        return (self.implied ** 2 - self.realized ** 2) / 1e4

    @property
    def var_swap_ret(self) -> float:
        """Short variance-swap P&L per unit of vega notional, in vol points.

        The standard normalization: a variance swap struck at K pays
        (K^2 - RV^2) per unit variance notional, and dividing by 2K converts
        that to the more readable per-vega-notional figure.
        """
        if self.implied <= 0:
            return 0.0
        return (self.implied ** 2 - self.realized ** 2) / (2 * self.implied)


def build_windows(
    currency: str,
    dvol: dict[int, float],
    closes: dict[int, float],
    *,
    hold_days: int = 30,
    start_ms: int | None = None,
    end_ms: int | None = None,
    periods_per_year: float = 365.0,
    min_returns: int = 4,
) -> list[VolWindow]:
    """Tile the tape into back-to-back holding periods. No overlap, ever.

    Overlapping windows would multiply the apparent sample by 30 while adding
    almost no information -- the same mistake, in a different costume, as
    bootstrapping coin-periods instead of rebalances in decision 023.
    """
    # The grids need not be the same -- `closes` may be hourly while `dvol` is
    # daily -- so bound the tape by each series separately rather than by an
    # intersection that would be empty.
    if not dvol or not closes:
        return []
    lo = start_ms if start_ms is not None else max(min(dvol), min(closes))
    hi = end_ms if end_ms is not None else min(max(dvol), max(closes))
    span = hold_days * DAY_MS
    out: list[VolWindow] = []
    t0 = lo
    while t0 + span <= hi:
        t1 = t0 + span
        iv = dvol.get(t0)
        if iv is None or iv <= 0:
            # No index reading on that exact day: skip the window rather than
            # reach for a neighbouring day's value.
            t0 = t1
            continue
        rv, n = realized_vol(
            closes, t0, t1,
            periods_per_year=periods_per_year,
            min_returns=min_returns,
        )
        if not math.isnan(rv):
            out.append(
                VolWindow(
                    currency=currency,
                    t0=t0,
                    t1=t1,
                    implied=iv,
                    realized=rv,
                    n_returns=n,
                )
            )
        t0 = t1
    return out


def summarize(windows: list[VolWindow], *, label: str = "") -> dict:
    from .carry import _concentration
    from .metrics import bootstrap_ci, t_stat, _mean, _stdev

    if not windows:
        return {"label": label, "n": 0}
    vp = [w.vol_premium for w in windows]
    vs = [w.var_swap_ret for w in windows]
    lo, hi = bootstrap_ci(vp, draws=6000)
    slo, shi = bootstrap_ci(vs, draws=6000)
    return {
        "label": label,
        "n": len(windows),
        "implied_mean": _mean([w.implied for w in windows]),
        "realized_mean": _mean([w.realized for w in windows]),
        "vol_premium_mean": _mean(vp),
        "vol_premium_ci": (lo, hi),
        "vol_premium_t": t_stat(vp),
        "var_swap_mean": _mean(vs),
        "var_swap_ci": (slo, shi),
        "var_swap_t": t_stat(vs),
        "hit_rate": sum(1 for v in vp if v > 0) / len(vp),
        "worst_vol_premium": min(vp),
        "worst_var_swap": min(vs),
        "best_var_swap": max(vs),
        "top5_profit_share": _concentration(vs, 5),
        "sd": _stdev(vs),
    }


# --------------------------------------------------------------------------
# Layer 2: the short straddle, held to settlement.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Straddle:
    """One expiry: sold at a real traded price, settled on the real index."""

    currency: str
    expiry_ms: int
    entry_ms: int
    strike: int
    spot_entry: float
    settle: float
    call_premium: float  # in coin
    put_premium: float
    call_volume: float
    put_volume: float

    @property
    def premium(self) -> float:
        return self.call_premium + self.put_premium

    @property
    def payoff(self) -> float:
        """What the short pays at expiry, in coin. Inverse-settled, so the
        USD payoff is divided by the settlement price."""
        if self.settle <= 0:
            return 0.0
        return abs(self.settle - self.strike) / self.settle

    @property
    def pnl(self) -> float:
        """Coin per one straddle sold, before execution cost."""
        return self.premium - self.payoff

    @property
    def moneyness(self) -> float:
        return self.strike / self.spot_entry - 1.0

    def pnl_after_spread(self, spread_pct_of_mid: float) -> float:
        """Charge crossing half the quoted spread on each of the two legs.

        The recorded bar is a traded price, not necessarily one a seller could
        have hit, so this is where the option book's real width is charged.
        """
        haircut = spread_pct_of_mid / 100.0 / 2.0
        return self.premium * (1 - haircut) - self.payoff


def summarize_straddles(
    straddles: list[Straddle], *, spread_pct: float = 0.0, label: str = ""
) -> dict:
    from .carry import _concentration
    from .metrics import bootstrap_ci, t_stat, _mean, _stdev

    if not straddles:
        return {"label": label, "n": 0}
    pnl = [s.pnl_after_spread(spread_pct) for s in straddles]
    # Per unit of premium sold, which is the closest thing to a return that
    # does not require picking a margin convention.
    ratio = [
        s.pnl_after_spread(spread_pct) / s.premium for s in straddles if s.premium > 0
    ]
    lo, hi = bootstrap_ci(pnl, draws=6000)
    return {
        "label": label,
        "n": len(straddles),
        "premium_mean": _mean([s.premium for s in straddles]),
        "payoff_mean": _mean([s.payoff for s in straddles]),
        "pnl_mean": _mean(pnl),
        "pnl_ci": (lo, hi),
        "pnl_t": t_stat(pnl),
        "per_premium_mean": _mean(ratio) if ratio else 0.0,
        "hit_rate": sum(1 for v in pnl if v > 0) / len(pnl),
        "worst": min(pnl),
        "best": max(pnl),
        "top5_profit_share": _concentration(pnl, 5),
        "sd": _stdev(pnl),
        "spread_pct_charged": spread_pct,
    }


# --------------------------------------------------------------------------
# Reconciling the two layers.
# --------------------------------------------------------------------------


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def straddle_coin_price(sigma: float, years: float) -> float:
    """Black price of an at-the-forward straddle, in units of the underlying.

    Deribit options are inverse-settled, so dividing the USD price by spot
    leaves `2 * (2 * Phi(sigma * sqrt(T) / 2) - 1)` with no spot in it at all.
    """
    if sigma <= 0 or years <= 0:
        return 0.0
    return 2.0 * (2.0 * _phi(sigma * math.sqrt(years) / 2.0) - 1.0)


def atm_implied_vol(coin_premium: float, years: float) -> float:
    """Invert `straddle_coin_price`. Returns annualized vol in percent.

    This is the number that reconciles layers 1 and 2, and it is the reason
    they can disagree without either being wrong. DVOL is a variance-swap
    style index built from the whole smile, so whenever the smile has any
    convexity DVOL sits *above* the at-the-money implied vol. Measuring
    "implied minus realized" against DVOL therefore credits a single-strike
    seller with a premium they never receive. What the straddle seller is paid
    is this number minus realized.
    """
    if coin_premium <= 0 or years <= 0:
        return float("nan")
    lo, hi = 1e-6, 10.0
    if straddle_coin_price(hi, years) < coin_premium:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if straddle_coin_price(mid, years) < coin_premium:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 * 100.0

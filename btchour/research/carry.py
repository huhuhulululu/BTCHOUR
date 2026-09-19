"""Spot-perp funding carry, as arithmetic rather than as a paper estimate.

Decision 021 put this mechanism first for one reason: like `venue.py` on
Kalshi, it needs no fill model. The exchange publishes every funding
settlement, so a delta-neutral position's cash flows are determined. Nothing
here infers a fill, assumes a queue position, or consults a forecast of ours.

The construction is coin-matched, which is what delta neutral actually means:
buy 1 coin of spot, sell 1 coin of the perpetual. Then for one coin held from
t0 to t1, in quote currency,

    pnl = (S1 - S0) - (P1 - P0) + sum_i f_i * M_i - costs
        = (P0 - S0) - (P1 - S1) + sum_i f_i * M_i - costs
        = basis_0 - basis_1  +  funding  -  costs

with S spot last price, P perp last price, M the perp mark price at funding
stamp i, and f_i the rate the exchange settled. The second line is the point
of the module. **Decision 021's paper account had only the funding term and
the cost term.** It priced the trade as if the basis were zero going in and
zero coming out. It is neither: the basis is exactly the price of the thing
you are selling, so entering rich and exiting cheap is where the carry
actually gets paid, and entering cheap and exiting rich is how a positive
funding tape still loses money. That term is measured here, not assumed.

Windows are non-overlapping by construction. Funding is strongly
autocorrelated -- the rate is a slow function of the same basis the position
holds -- so resampling 8h stamps, or resampling overlapping windows, gives an
interval that is far too narrow. The independent unit is a whole holding
period, and that is the unit `research/experiments.py` bootstraps.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict

DATA_ROOT = os.path.join("data", "binance")

# Binance USDT-M perpetual, VIP0, verified against the published fee
# schedule in decision 021. Basis points of notional, one side.
SPOT_TAKER_BP = 10.0
SPOT_MAKER_BP = 10.0
PERP_TAKER_BP = 5.0
PERP_MAKER_BP = 2.0

# 1-year Treasury, 2026-09-16, the risk-free alternative the carry has to beat.
TBILL_ANNUAL = 0.0445

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def _norm_ts(raw: str) -> int:
    """Binance switched spot klines to microseconds partway through 2025.

    A 2025-01 spot file opens at 1735689600000000 and a 2024-12 one at
    1733011200000. Reading both as milliseconds puts half the sample in the
    year 56000, which silently produces an empty join rather than an error.
    """
    value = int(float(raw))
    if value > 1_000_000_000_000_000:  # microseconds
        return value // 1000
    return value


def _rows(path: str) -> list[list[str]]:
    """CSV rows with the header dropped if there is one.

    Newer archives carry a header line, older ones do not, in the same
    directory. Sniffing the first field is the only reliable test.
    """
    with open(path) as handle:
        out = []
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            try:
                float(parts[0])
            except ValueError:
                continue  # header
            out.append(parts)
        return out


def _months(root: str) -> list[str]:
    if not os.path.isdir(root):
        return []
    return sorted(f for f in os.listdir(root) if f.endswith(".csv"))


def bar_root(kind: str, symbol: str, interval: str, *, root: str = DATA_ROOT) -> str:
    """Where `archives.fetch` puts one (kind, interval, symbol).

    The interval is part of the path on purpose. When it was not, an hourly
    pull and a daily pull of the same symbol landed on the same filename and
    the second one was skipped as already cached, leaving hourly bars behind a
    daily-looking call.
    """
    slot = "8h" if kind == "fundingRate" else interval
    return os.path.join(root, kind, slot, symbol)


_SPACING_MS = {"1h": HOUR_MS, "1d": DAY_MS}


def load_closes(
    kind: str,
    symbol: str,
    *,
    interval: str = "1h",
    root: str = DATA_ROOT,
    check_spacing: bool = True,
) -> dict[int, float]:
    """Close price keyed by the bar's open time in milliseconds.

    Verifies that the bars really are spaced the way `interval` claims. A
    mislabelled interval is invisible in every downstream number -- it does not
    raise, it just rescales volumes and returns by 24 -- so it is checked here
    rather than trusted.
    """
    base = bar_root(kind, symbol, interval, root=root)
    out: dict[int, float] = {}
    for name in _months(base):
        for parts in _rows(os.path.join(base, name)):
            out[_norm_ts(parts[0])] = float(parts[4])
    if check_spacing and len(out) > 24:
        want = _SPACING_MS.get(interval)
        if want is not None:
            ts = sorted(out)
            gaps = [b - a for a, b in zip(ts, ts[1:])]
            modal = min(gaps)  # the smallest gap is the true bar size
            if modal != want:
                raise ValueError(
                    f"{symbol} {kind}: bars are spaced {modal}ms but "
                    f"interval={interval} expects {want}ms"
                )
    return out


@dataclass(frozen=True)
class Funding:
    ts: int  # settlement time, ms
    rate: float
    interval_hours: float


def load_funding(symbol: str, *, root: str = DATA_ROOT) -> list[Funding]:
    base = bar_root("fundingRate", symbol, "8h", root=root)
    out: list[Funding] = []
    for name in _months(base):
        for parts in _rows(os.path.join(base, name)):
            out.append(
                Funding(
                    ts=_norm_ts(parts[0]),
                    interval_hours=float(parts[1]),
                    rate=float(parts[2]),
                )
            )
    out.sort(key=lambda f: f.ts)
    # The archive repeats a stamp across month boundaries now and then.
    deduped: list[Funding] = []
    for f in out:
        if deduped and deduped[-1].ts == f.ts:
            continue
        deduped.append(f)
    return deduped


@dataclass(frozen=True)
class Costs:
    """Per-side fees in basis points, and which side of the book we use."""

    spot_bp: float = SPOT_TAKER_BP
    perp_bp: float = PERP_MAKER_BP

    def round_trip_bp(self) -> float:
        return 2 * (self.spot_bp + self.perp_bp)


@dataclass(frozen=True)
class Window:
    """One non-overlapping holding period, fully decomposed.

    Every field is quote currency per one coin of position, except the
    `*_ret` fields which divide by the spot price paid at entry -- the
    capital a fully funded spot leg ties up.
    """

    symbol: str
    t0: int
    t1: int
    hold_days: float
    spot_in: float
    spot_out: float
    perp_in: float
    perp_out: float
    basis_in: float  # perp - spot, quote currency
    basis_out: float
    funding_usd: float
    rate_sum: float  # plain sum of the settled rates, no price in it
    n_stamps: int
    cost_usd: float

    @property
    def rate_net(self) -> float:
        """The carry with the price path taken out of it.

        `funding_ret` divides a notional-weighted sum of payments by the entry
        spot price. When the coin appreciates during the window the perp
        notional -- and so each payment -- grows, while the denominator does
        not, and the ratio drifts up. On BTC 2020-2026 that drift is worth
        +1.6pp annualized at a 30-day hold and +4.0pp at 90 days, which is
        larger than the entire edge being argued about. It is a real cash flow,
        but it is a bet on the coin trending up, not on the carry, so the
        verdict is read off this field and the drift is reported separately.
        """
        return self.rate_sum - self.cost_ret

    @property
    def rate_annual(self) -> float:
        if self.hold_days <= 0:
            return 0.0
        return self.rate_net * 365.0 / self.hold_days

    @property
    def notional_drift(self) -> float:
        """How much of `funding_ret` came from the coin appreciating."""
        return self.funding_ret - self.rate_sum

    @property
    def basis_usd(self) -> float:
        """What the basis move paid. Positive when we entered rich."""
        return self.basis_in - self.basis_out

    @property
    def gross_usd(self) -> float:
        return self.funding_usd + self.basis_usd

    @property
    def net_usd(self) -> float:
        return self.gross_usd - self.cost_usd

    @property
    def net_ret(self) -> float:
        return self.net_usd / self.spot_in

    @property
    def funding_ret(self) -> float:
        return self.funding_usd / self.spot_in

    @property
    def basis_ret(self) -> float:
        return self.basis_usd / self.spot_in

    @property
    def cost_ret(self) -> float:
        return self.cost_usd / self.spot_in

    @property
    def net_annual(self) -> float:
        if self.hold_days <= 0:
            return 0.0
        return self.net_ret * 365.0 / self.hold_days

    def as_dict(self) -> dict:
        row = asdict(self)
        row.update(
            basis_usd=self.basis_usd,
            net_usd=self.net_usd,
            net_ret=self.net_ret,
            funding_ret=self.funding_ret,
            basis_ret=self.basis_ret,
            cost_ret=self.cost_ret,
            net_annual=self.net_annual,
            rate_net=self.rate_net,
            rate_annual=self.rate_annual,
            notional_drift=self.notional_drift,
        )
        return row


def build_windows(
    symbol: str,
    hold_days: float,
    *,
    spot: dict[int, float],
    perp: dict[int, float],
    mark: dict[int, float],
    funding: list[Funding],
    costs: Costs = Costs(),
    start_ms: int | None = None,
    end_ms: int | None = None,
    bar_ms: int = HOUR_MS,
) -> list[Window]:
    """Chop the tape into back-to-back holding periods and price each one.

    Entry and exit are hourly bars both legs actually printed. A window whose
    entry or exit hour is missing on either leg is dropped whole rather than
    filled forward: a carried price would invent a basis, and the basis is
    the term being measured.
    """
    if hold_days <= 0:
        raise ValueError("hold_days must be > 0")
    hours = sorted(set(spot) & set(perp))
    if not hours:
        return []
    lo = start_ms if start_ms is not None else hours[0]
    hi = end_ms if end_ms is not None else hours[-1]
    span = int(round(hold_days * DAY_MS))
    if span < bar_ms:
        raise ValueError("hold_days is shorter than one bar")

    stamps = [f for f in funding if lo <= f.ts <= hi]
    out: list[Window] = []
    t0 = ((lo + bar_ms - 1) // bar_ms) * bar_ms
    while t0 + span <= hi:
        t1 = t0 + span
        if t0 in spot and t0 in perp and t1 in spot and t1 in perp:
            paid = 0.0
            rate_sum = 0.0
            n = 0
            for f in stamps:
                if not (t0 < f.ts <= t1):
                    continue
                m = mark.get((f.ts // bar_ms) * bar_ms)
                if m is None:
                    m = perp.get((f.ts // bar_ms) * bar_ms)
                if m is None:
                    continue
                paid += f.rate * m  # short perp receives when rate > 0
                rate_sum += f.rate
                n += 1
            s0, s1 = spot[t0], spot[t1]
            p0, p1 = perp[t0], perp[t1]
            cost = (
                (s0 + s1) * costs.spot_bp + (p0 + p1) * costs.perp_bp
            ) / 10_000.0
            out.append(
                Window(
                    symbol=symbol,
                    t0=t0,
                    t1=t1,
                    hold_days=hold_days,
                    spot_in=s0,
                    spot_out=s1,
                    perp_in=p0,
                    perp_out=p1,
                    basis_in=p0 - s0,
                    basis_out=p1 - s1,
                    funding_usd=paid,
                    rate_sum=rate_sum,
                    n_stamps=n,
                    cost_usd=cost,
                )
            )
        t0 = t1
    return out


def load_symbol(
    symbol: str, *, interval: str = "1h", root: str = DATA_ROOT
) -> dict:
    return {
        "spot": load_closes("spotKlines", symbol, interval=interval, root=root),
        "perp": load_closes("klines", symbol, interval=interval, root=root),
        "mark": load_closes("markPriceKlines", symbol, interval=interval, root=root),
        "funding": load_funding(symbol, root=root),
    }


# --------------------------------------------------------------------------
# Verdict layer. Everything below reads windows and never touches prices, so
# the decomposition above stays the single source of the arithmetic.
# --------------------------------------------------------------------------


def _concentration(values: list[float], k: int = 5) -> float:
    """Share of total profit contributed by the k best windows.

    Decision 020's habit: a mean that is one good week wearing a suit is not
    a mechanism. Returns 1.0 when the total is non-positive, which reads as
    "the shape question does not arise, it already lost".
    """
    gains = sorted((v for v in values if v > 0), reverse=True)
    total = sum(values)
    if total <= 0 or not gains:
        return 1.0
    return sum(gains[:k]) / total


def summarize(windows: list[Window], *, label: str = "") -> dict:
    """The whole verdict for one holding period, on the window as the unit."""
    from .metrics import bootstrap_ci, t_stat, _mean, _stdev

    if not windows:
        return {"label": label, "n": 0}
    net = [w.net_ret for w in windows]
    ann = [w.net_annual for w in windows]
    rate = [w.rate_net for w in windows]
    lo, hi = bootstrap_ci(net, draws=4000)
    rlo, rhi = bootstrap_ci(rate, draws=4000)
    hold = windows[0].hold_days
    scale = 365.0 / hold if hold > 0 else 0.0
    # Volatility of the annualized carry, from the per-window spread.
    sd = _stdev(net)
    sharpe = 0.0
    if sd > 0:
        sharpe = (_mean(net) * scale - TBILL_ANNUAL) / (sd * (scale ** 0.5))
    return {
        "label": label,
        "symbol": windows[0].symbol,
        "hold_days": hold,
        "n": len(windows),
        "net_ret_mean": _mean(net),
        "net_ret_ci": (lo, hi),
        "net_annual_mean": _mean(net) * scale,
        "net_annual_ci": (lo * scale, hi * scale),
        "t": t_stat(net),
        "rate_annual_mean": _mean(rate) * scale,
        "rate_annual_ci": (rlo * scale, rhi * scale),
        "rate_t": t_stat(rate),
        "rate_excess_annual": _mean(rate) * scale - TBILL_ANNUAL,
        "rate_excess_ci": (rlo * scale - TBILL_ANNUAL, rhi * scale - TBILL_ANNUAL),
        "rate_win_rate": sum(1 for v in rate if v > 0) / len(rate),
        "notional_drift_annual": _mean([w.notional_drift for w in windows]) * scale,
        "rate_top5_share": _concentration(rate, 5),
        "win_rate": sum(1 for v in net if v > 0) / len(net),
        "funding_ret_mean": _mean([w.funding_ret for w in windows]),
        "basis_ret_mean": _mean([w.basis_ret for w in windows]),
        "cost_ret_mean": _mean([w.cost_ret for w in windows]),
        "funding_annual": _mean([w.funding_ret for w in windows]) * scale,
        "basis_annual": _mean([w.basis_ret for w in windows]) * scale,
        "cost_annual": _mean([w.cost_ret for w in windows]) * scale,
        "excess_annual": _mean(net) * scale - TBILL_ANNUAL,
        "excess_annual_ci": (lo * scale - TBILL_ANNUAL, hi * scale - TBILL_ANNUAL),
        "sharpe_vs_tbill": sharpe,
        "worst_ret": min(net),
        "p05_ret": sorted(net)[max(0, int(0.05 * len(net)) - 1)],
        "best_ret": max(net),
        "top5_profit_share": _concentration(net, 5),
        "ann_spread": (min(ann), max(ann)),
    }

"""Does providing liquidity pay in the minutes when flow is most one-sided?

`makerpool.py` answers "what does a passive quote earn on average". This asks
the narrower and more interesting question: **is there a subset of moments when
it pays, even though the average does not?** The story would be forced or
urgent flow -- a liquidation cascade, a stop run, a large order worked
impatiently. That flow is not informed about value, it is compelled about
timing, so whoever absorbs it should be paid for the inventory risk and the
price should partly revert.

Binance publishes what this needs inside every kline: `taker_buy_quote_volume`
against `quote_volume` gives the share of the minute's notional that was
bought by aggressors, so

    imbalance = 2 * taker_buy_share - 1

runs from -1 (every aggressor selling) to +1. Combined with the minute's own
return it separates the two cases that matter: a big move *with* one-sided
aggression is flow pushing price, a big move *without* it is a repricing.

This is a conditional-return study, which is the shape that has produced every
false positive in this repository, so it is built with the conditioning under
control:

* **The threshold is swept, never picked.** A single cherry-picked percentile
  is how a reversal appears out of noise. The whole curve across thresholds
  gets reported, and a mechanism that exists only at one cut is noise.
* **Costs first, not last.** The reversal has to clear a round trip -- the
  3.99bp median effective spread from decision 023 plus two taker fees --
  before it is worth discussing. Both the gross and net figures are returned
  so the cost is never hidden inside a single number.
* **The independent unit is the symbol-day.** Bursts cluster: one volatile
  hour produces dozens of them, all on one price path, and all twelve symbols
  share crypto beta. Decision 023's t=8.89 came from exactly this mistake.
* **Entry is the next bar's open, never the burst bar's close.** A fill at the
  extreme of the bar that defined the signal is the wick-fill model that paid
  decision 018 imaginary money.
"""

from __future__ import annotations

from dataclasses import dataclass

MINUTE_MS = 60_000

# Same cost constants as `fundingflow`: decision 023's measured median
# effective spread, plus the published per-side taker fee.
ROUND_TRIP_SPREAD_BP = 3.99
TAKER_FEE_BP = 5.0


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    close: float
    quote_volume: float
    taker_buy_quote: float

    @property
    def ret_bp(self) -> float:
        if self.open <= 0:
            return 0.0
        return (self.close / self.open - 1.0) * 10_000.0

    @property
    def taker_buy_share(self) -> float:
        if self.quote_volume <= 0:
            return 0.5
        return self.taker_buy_quote / self.quote_volume

    @property
    def imbalance(self) -> float:
        """-1 when every aggressor sold, +1 when every aggressor bought."""
        return 2.0 * self.taker_buy_share - 1.0


@dataclass(frozen=True)
class Burst:
    """One minute of extreme one-sided aggression, and what happened next."""

    symbol: str
    ts: int
    imbalance: float
    burst_ret_bp: float
    quote_volume: float
    entry: float  # next bar's open
    exit: float  # close `horizon` bars later

    @property
    def side(self) -> int:
        """The side that absorbs the flow, which is the opposite of the crowd.

        Aggressors buying (`imbalance > 0`) pushed the price up, so absorbing
        that flow means selling, and the absorber profits if the price comes
        back down. Getting this backwards does not produce a small error: it
        measures the continuation trade instead of the absorption trade, which
        is a different mechanism with the opposite sign.
        """
        return 1 if self.imbalance < 0 else -1

    @property
    def gross_bp(self) -> float:
        """Reversal captured by taking the other side, in bp."""
        if self.entry <= 0:
            return 0.0
        return self.side * (self.exit / self.entry - 1.0) * 10_000.0

    @property
    def net_bp(self) -> float:
        return self.gross_bp - ROUND_TRIP_SPREAD_BP - 2 * TAKER_FEE_BP


def load_bars(path: str) -> list[Bar]:
    """Read a monthly 1m kline CSV, tolerating the header-or-not eras."""
    out: list[Bar] = []
    with open(path) as fh:
        for raw in fh:
            p = raw.rstrip("\n").split(",")
            if len(p) < 11:
                continue
            try:
                out.append(
                    Bar(
                        ts=int(p[0]),
                        open=float(p[1]),
                        close=float(p[4]),
                        quote_volume=float(p[7]),
                        taker_buy_quote=float(p[10]),
                    )
                )
            except ValueError:
                continue  # header row
    out.sort(key=lambda b: b.ts)
    return out


def find_bursts(
    symbol: str,
    bars: list[Bar],
    *,
    imbalance_min: float = 0.6,
    move_min_bp: float = 20.0,
    horizon: int = 10,
    volume_lookback: int = 60,
    volume_mult: float = 3.0,
) -> list[Burst]:
    """Minutes that were one-sided, large, and busy, with the following move.

    All three conditions are required together because any one alone picks up
    something else: one-sided aggression alone is common and uninformative, a
    large move alone is usually a repricing, and high volume alone is the open
    of a session. Urgent flow is all three at once.

    `volume_mult` is measured against the trailing median rather than the mean
    so a single earlier burst does not raise its own bar.
    """
    out: list[Burst] = []
    n = len(bars)
    for i in range(volume_lookback, n - horizon - 1):
        b = bars[i]
        if b.quote_volume <= 0:
            continue
        if abs(b.imbalance) < imbalance_min:
            continue
        if abs(b.ret_bp) < move_min_bp:
            continue
        window = [bars[j].quote_volume for j in range(i - volume_lookback, i)]
        window.sort()
        med = window[len(window) // 2]
        if med <= 0 or b.quote_volume < volume_mult * med:
            continue
        # The move and the aggression have to point the same way: that is what
        # makes it flow pushing price rather than a quote repricing.
        if (b.ret_bp > 0) != (b.imbalance > 0):
            continue
        entry_bar = bars[i + 1]
        exit_bar = bars[i + 1 + horizon]
        if entry_bar.ts != b.ts + MINUTE_MS:
            continue  # a gap in the tape; never step over one
        out.append(
            Burst(
                symbol=symbol,
                ts=b.ts,
                imbalance=b.imbalance,
                burst_ret_bp=b.ret_bp,
                quote_volume=b.quote_volume,
                entry=entry_bar.open,
                exit=exit_bar.close,
            )
        )
    return out


def by_symbol_day(bursts: list[Burst]) -> list[list[Burst]]:
    DAY = 86_400_000
    buckets: dict[tuple[str, int], list[Burst]] = {}
    for b in bursts:
        buckets.setdefault((b.symbol, b.ts // DAY), []).append(b)
    return list(buckets.values())


def summarize(bursts: list[Burst], *, label: str = "") -> dict:
    from .metrics import cluster_ci, t_stat, _mean, _stdev
    from .carry import _concentration

    if not bursts:
        return {"label": label, "n": 0}
    groups = by_symbol_day(bursts)
    gross = [[b.gross_bp for b in g] for g in groups]
    net = [[b.net_bp for b in g] for g in groups]
    flat_gross = [v for g in gross for v in g]
    flat_net = [v for g in net for v in g]
    day_means = [_mean(g) for g in net]
    return {
        "label": label,
        "n": len(bursts),
        "n_symbol_days": len(groups),
        "n_symbols": len({b.symbol for b in bursts}),
        "burst_move_bp_mean": _mean([abs(b.burst_ret_bp) for b in bursts]),
        "imbalance_abs_mean": _mean([abs(b.imbalance) for b in bursts]),
        "gross_bp": _mean(flat_gross),
        "gross_ci": cluster_ci(gross, draws=4000),
        "net_bp": _mean(flat_net),
        "net_ci": cluster_ci(net, draws=4000),
        "net_t_by_day": t_stat(day_means),
        "cost_floor_bp": ROUND_TRIP_SPREAD_BP + 2 * TAKER_FEE_BP,
        "share_positive": sum(1 for v in flat_net if v > 0) / len(flat_net),
        "worst_bp": min(flat_net),
        "best_bp": max(flat_net),
        "top5_profit_share": _concentration(flat_net, 5),
        "sd_bp": _stdev(day_means),
    }

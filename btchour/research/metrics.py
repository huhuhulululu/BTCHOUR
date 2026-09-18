"""Statistics for a set of replayed takes.

`EV = p * b - (1 - p)` scores one bet before it happens. These score what a
rule actually did across many bets, with an interval wide enough to admit how
few of them there were. A mean with no interval is the shape most overfitting
arrives in.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, asdict


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def bootstrap_ci(
    values: list[float],
    *,
    alpha: float = 0.05,
    draws: int = 4000,
    seed: int = 7,
) -> tuple[float, float]:
    """Percentile bootstrap on the mean. Returns (lo, hi); (0, 0) when empty."""
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(draws):
        means.append(_mean([values[rng.randrange(n)] for _ in range(n)]))
    means.sort()
    lo = means[max(0, int(draws * alpha / 2) - 1)]
    hi = means[min(draws - 1, int(draws * (1 - alpha / 2)))]
    return (lo, hi)


def t_stat(values: list[float]) -> float:
    """Mean over its own standard error. |t| < 2 is noise, whatever the sign."""
    if len(values) < 2:
        return 0.0
    sd = _stdev(values)
    if sd <= 0:
        return 0.0
    return _mean(values) / (sd / math.sqrt(len(values)))


def max_drawdown(pnls: list[float]) -> float:
    """Worst peak-to-trough of the cumulative curve, as a positive number."""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


@dataclass(frozen=True)
class TakeSummary:
    trades: int
    hours: int
    trades_per_hour: float
    wins: int
    win_rate: float
    total_pnl: float
    pnl_per_trade: float
    pnl_ci95: tuple[float, float]
    t_stat: float
    roi_per_trade: float
    max_drawdown: float
    exit_reasons: dict[str, int]

    def as_dict(self) -> dict:
        return asdict(self)


def summarize_takes(takes: list[dict], hours: int) -> TakeSummary:
    pnls = [float(t.get("pnl") or 0.0) for t in takes if t.get("pnl") is not None]
    rois = [float(t.get("roi")) for t in takes if t.get("roi") is not None]
    reasons: dict[str, int] = {}
    for take in takes:
        key = str(take.get("exit_reason") or "unknown")
        reasons[key] = reasons.get(key, 0) + 1
    return TakeSummary(
        trades=len(takes),
        hours=hours,
        trades_per_hour=(len(takes) / hours) if hours else 0.0,
        wins=sum(1 for p in pnls if p > 0),
        win_rate=(sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else 0.0,
        total_pnl=sum(pnls),
        pnl_per_trade=_mean(pnls),
        pnl_ci95=bootstrap_ci(pnls),
        t_stat=t_stat(pnls),
        roi_per_trade=_mean(rois),
        max_drawdown=max_drawdown(pnls),
        exit_reasons=reasons,
    )


def fill_adverse_selection(takes: list[dict]) -> dict:
    """How far the book moved against a maker rest between hang and fill.

    `entry.ask` is what the rung showed when the rest went in; `entry.model_p`
    is the model's read at the same moment. A maker who only fills when the
    offer walks down to the rest is buying what the market has just marked
    lower -- this measures that discount in cents, per fill.
    """
    rows = [t for t in takes if (t.get("play") == "impulse_wait")]
    gaps = []
    for take in rows:
        ask = take.get("ask")
        price = take.get("ask") if take.get("entry_price") is None else take.get("entry_price")
        rest = take.get("rest")
        if ask is None or rest is None:
            continue
        gaps.append(float(ask) - float(rest))
    return {
        "fills": len(rows),
        "mean_seen_minus_rest": _mean(gaps),
        "note": "positive = the rest sat under the book and only filled after it walked down",
    }

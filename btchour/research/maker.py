"""Does providing liquidity at the touch pay, on this ladder?

Everything that needed our model to beat the book is dead (017). Static
arbitrage across the ladder is dead too: one 1c crossing in 47,293 rung pairs
over 92 hours, and the taker fee eats it. What is left is the one claim that
needs no forecast at all -- that a resting quote earns the spread from the
flow that crosses it, faster than informed flow takes it away.

This measures exactly that, and nothing else:

* rest at the rung's own best quote as of the close of minute t;
* fill if minute t+1 actually printed a trade through that price, with volume;
* value the fill two ways -- what the rung settled at, and the mid one minute
  later (the standard markout, which isolates how fast the quote moved
  against us).

No entry gate, no impulse, no exit rule. If a touch quote does not pay here,
nothing built on top of one will.

Two ways this is deliberately generous: it ignores queue position, so it fills
whenever the tape reached our price, and it ignores the size we would actually
get. A negative result under those assumptions is therefore strong; a positive
one is an upper bound, not a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass

from btchour.replay import EventTape
from btchour.research.metrics import cluster_ci, mean_ci, t_stat


@dataclass(frozen=True)
class MakerFill:
    event_ticker: str
    strike: float
    side: str  # "buy_yes" (rested bid) or "sell_yes" (rested ask)
    price: float
    settle_pnl: float
    markout_pnl: float | None
    seconds_left: float

    @property
    def cost(self) -> float:
        """What the position costs, which is not the number we quoted.

        Resting an ask at 0.10 sells YES and leaves us long NO at 0.90. Bucket
        by the quoted price and that trade lands in the "cheap" bucket beside
        a 0.10 lottery ticket it is the opposite of. Everything downstream
        buckets on this instead.
        """
        return self.price if self.side == "buy_yes" else 1.0 - self.price


def _px(stick: dict, key: str, field: str) -> float | None:
    raw = (stick.get(key) or {}).get(field)
    if raw in (None, ""):
        return None
    value = float(raw)
    return value if 0.0 < value < 1.0 else None


def _volume(stick: dict) -> float:
    raw = stick.get("volume_fp")
    if raw in (None, ""):
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _mid(stick: dict) -> float | None:
    bid = _px(stick, "yes_bid", "close_dollars")
    ask = _px(stick, "yes_ask", "close_dollars")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def scan_tape(
    tape: EventTape,
    *,
    reach: float = 600.0,
    min_seconds: float = 180.0,
    fill_rule: str = "touch",
    min_volume: float = 0.0,
) -> list[MakerFill]:
    """`fill_rule` decides how generous the fill assumption is.

    `touch` fills whenever the tape reached our price. That ignores the queue
    ahead of us, so it is an upper bound, not a forecast.

    `through` fills only when the tape traded *past* our price, which is the
    cheapest available evidence that the resting queue at our level was
    actually consumed. On a thin rung -- and the near-certain rungs are thin --
    the gap between the two is the whole question.
    """
    if fill_rule not in {"touch", "through"}:
        raise ValueError(f"unknown fill_rule {fill_rule!r}")
    if tape.error or not tape.maturity_ms or not tape.spots:
        return []
    maturity = tape.maturity_ms / 1000.0
    spots = {(minute // 1000) + 60: price for minute, price in tape.spots.items()}
    fills: list[MakerFill] = []
    for strike, sticks in tape.candles.items():
        if not isinstance(sticks, dict):
            continue
        strike = float(strike)
        result = tape.results.get(strike)
        if result not in {"yes", "no"}:
            continue
        settle_yes = 1.0 if result == "yes" else 0.0
        stamps = sorted(ts for ts in sticks if isinstance(sticks.get(ts), dict))
        for index, end_ts in enumerate(stamps[:-1]):
            left = maturity - end_ts
            if left < min_seconds:
                continue
            spot = spots.get(int(end_ts))
            if spot is None or abs(strike - spot) > reach:
                continue
            quote = sticks[end_ts]
            nxt = sticks[stamps[index + 1]]
            volume = _volume(nxt)
            if volume <= 0 or volume < min_volume:
                continue
            bid = _px(quote, "yes_bid", "close_dollars")
            ask = _px(quote, "yes_ask", "close_dollars")
            traded_low = _px(nxt, "price", "low_dollars")
            traded_high = _px(nxt, "price", "high_dollars")
            later = sticks[stamps[index + 2]] if index + 2 < len(stamps) else None
            mark = _mid(later) if later else None
            hit_bid = (
                traded_low < bid - 1e-9 if fill_rule == "through" else traded_low <= bid + 1e-9
            ) if (bid is not None and traded_low is not None) else False
            hit_ask = (
                traded_high > ask + 1e-9 if fill_rule == "through" else traded_high >= ask - 1e-9
            ) if (ask is not None and traded_high is not None) else False
            if bid is not None and hit_bid:
                fills.append(
                    MakerFill(
                        tape.event_ticker, strike, "buy_yes", bid,
                        settle_yes - bid,
                        (mark - bid) if mark is not None else None,
                        left,
                    )
                )
            if ask is not None and hit_ask:
                fills.append(
                    MakerFill(
                        tape.event_ticker, strike, "sell_yes", ask,
                        ask - settle_yes,
                        (ask - mark) if mark is not None else None,
                        left,
                    )
                )
    return fills


def _summary(values: list[float]) -> dict:
    if not values:
        return {"fills": 0}
    lo, hi = mean_ci(values)
    return {
        "fills": len(values),
        "mean": sum(values) / len(values),
        "ci95": (lo, hi),
        "t": t_stat(values),
    }


#: Bucket on what the position costs, never on the number we quoted.
BANDS = (
    (0.0, 0.15, "便宜彩票 <0.15"),
    (0.15, 0.85, "中段 0.15-0.85"),
    (0.85, 0.95, "高尾 0.85-0.95"),
    (0.95, 1.01, "极高尾 >0.95"),
)


def _clustered(by_hour: list[list[float]]) -> dict:
    """Summarise with an interval that resamples hours, not fills."""
    groups = [g for g in by_hour if g]
    flat = [v for g in groups for v in g]
    if not flat:
        return {"fills": 0}
    lo, hi = cluster_ci(groups)
    hourly = [sum(g) for g in groups]
    return {
        "fills": len(flat),
        "hours": len(groups),
        "mean": sum(flat) / len(flat),
        "ci95": (lo, hi),
        "t": t_stat(flat),
        "loss_rate": sum(1 for v in flat if v < 0) / len(flat),
        "worst_fill": min(flat),
        "hourly_mean": sum(hourly) / len(hourly),
        "worst_hour": min(hourly),
        "total": sum(flat),
        "losing_hours": sum(1 for v in hourly if v < 0),
    }


def scan_tapes(tapes: list[EventTape], **kwargs) -> dict:
    fills: list[MakerFill] = []
    per_hour: list[list[MakerFill]] = []
    hours = 0
    for tape in tapes:
        if tape.error:
            continue
        hours += 1
        rows = scan_tape(tape, **kwargs)
        per_hour.append(rows)
        fills.extend(rows)
    report: dict = {
        "hours": hours,
        "fills": len(fills),
        "fill_rule": kwargs.get("fill_rule", "touch"),
        "sides": {},
        "bands": {},
    }
    for side in ("buy_yes", "sell_yes", "both"):
        rows = fills if side == "both" else [f for f in fills if f.side == side]
        report["sides"][side] = {
            "settle": _summary([f.settle_pnl for f in rows]),
            "markout": _summary([f.markout_pnl for f in rows if f.markout_pnl is not None]),
        }
    for low, high, label in BANDS:
        report["bands"][label] = _clustered(
            [[f.settle_pnl for f in hour if low <= f.cost < high] for hour in per_hour]
        )
    report["bands"]["高尾合计 >0.85"] = _clustered(
        [[f.settle_pnl for f in hour if f.cost >= 0.85] for hour in per_hour]
    )
    return report


def render(report: dict) -> str:
    lines = [
        f"贴价挂单（{report['hours']} 小时真 tape，{report['fills']} 次成交，"
        f"成交判定 {report.get('fill_rule', 'touch')}）",
        "",
        "不设门、不设出场：挂在该档自己的买一/卖一，下一分钟真有成交才算成交，maker 费 0。",
        "touch = 摸到就成交（忽略排队，是上界）；through = 打穿才成交（排队被吃掉的证据）。",
        "",
        f"{'口径':<14}{'方向':<12}{'成交':>8}{'每张':>10}{'95%区间':>22}{'t':>8}",
    ]
    names = {"buy_yes": "挂买一买入", "sell_yes": "挂卖一卖出", "both": "合计"}
    for measure, label in (("settle", "持到结算"), ("markout", "1 分钟 markout")):
        for side in ("buy_yes", "sell_yes", "both"):
            row = report["sides"][side][measure]
            if not row.get("fills"):
                continue
            lo, hi = row["ci95"]
            lines.append(
                f"{label:<14}{names[side]:<12}{row['fills']:>8}{row['mean']:>+10.4f}"
                f"{f'[{lo:+.4f}, {hi:+.4f}]':>22}{row['t']:>8.2f}"
            )
    if report.get("bands"):
        lines += [
            "",
            "按持仓成本分档，持到结算，区间按小时聚类（一小时的各档共用同一条 BTC 路径）：",
            "",
            f"{'档':<18}{'成交':>8}{'每张':>10}{'95%区间':>22}{'亏损率':>9}{'最坏一张':>10}{'最坏一小时':>11}",
        ]
        for label, row in report["bands"].items():
            if not row.get("fills"):
                continue
            lo, hi = row["ci95"]
            lines.append(
                f"{label:<18}{row['fills']:>8}{row['mean']:>+10.4f}"
                f"{f'[{lo:+.4f}, {hi:+.4f}]':>22}{row['loss_rate']:>8.1%}"
                f"{row['worst_fill']:>+10.3f}{row['worst_hour']:>+11.2f}"
            )
    return "\n".join(lines)

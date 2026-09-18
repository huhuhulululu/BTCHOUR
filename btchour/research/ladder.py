"""Static arbitrage inside one hour's ladder. No model, no view, no forecast.

017 settled that `digital_prob` loses to the book, so every rule that needs
our fair value to beat the mid is dead. This asks a question that needs no
fair value at all.

`KXBTCD` lists many strikes on the same expiry, and YES(K) must be
non-increasing in K: a contract paying on `BTC >= 78000` is worth at least
one paying on `BTC >= 78100`. So for K1 < K2, buying YES(K1) and NO(K2)
together pays 1 in every world and 2 when settlement lands between them:

    S < K1        YES(K1)=0  NO(K2)=1   ->  1
    K1 <= S < K2  YES(K1)=1  NO(K2)=1   ->  2
    S >= K2       YES(K1)=1  NO(K2)=0   ->  1

NO(K2) costs `1 - yes_bid(K2)`, so the pair costs
`yes_ask(K1) + 1 - yes_bid(K2)` and the locked profit is

    yes_bid(K2) - yes_ask(K1) - fees

which is positive exactly when the ladder is crossed: a bid at a HIGHER
strike above an ask at a LOWER one. That is a pricing error the exchange
itself guarantees, not an opinion about Bitcoin.

Two prices are reported per instance:

* **close** -- both legs at the minute's closing quote. Optimistic: a
  one-minute candle does not promise the two rungs were touchable together.
* **worst** -- buy at the minute's highest ask, sell at the minute's lowest
  bid. If the edge survives that, the minute contained no ordering of events
  that removes it.

Only `worst` is evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from btchour.fees import taker_fee
from btchour.replay import EventTape


@dataclass(frozen=True)
class Cross:
    event_ticker: str
    end_ts: int
    low_strike: float
    high_strike: float
    buy_ask: float
    sell_bid: float
    edge: float
    fees: float
    low_volume: float
    high_volume: float
    seconds_left: float


def _price(stick: dict, key: str, field: str) -> float | None:
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


def pair_edge(buy_ask: float, sell_bid: float) -> tuple[float, float]:
    """Locked profit and fees for one YES(K1) + NO(K2) pair, per contract."""
    fees = taker_fee(buy_ask, 1.0) + taker_fee(round(1.0 - sell_bid, 4), 1.0)
    return sell_bid - buy_ask - fees, fees


def scan_tape(
    tape: EventTape,
    *,
    conservative: bool = True,
    require_volume: bool = True,
    min_seconds: float = 60.0,
) -> list[Cross]:
    """Every crossed rung pair in one hour.

    `conservative` buys at the minute's ask high and sells at its bid low.
    `require_volume` drops a minute where either rung printed nothing, since a
    quote nobody traded against is not evidence that it was reachable.
    """
    if tape.error or not tape.maturity_ms:
        return []
    maturity = tape.maturity_ms / 1000.0
    by_minute: dict[int, dict[float, dict]] = {}
    for strike, sticks in tape.candles.items():
        if not isinstance(sticks, dict):
            continue
        for end_ts, stick in sticks.items():
            if not isinstance(stick, dict):
                continue
            by_minute.setdefault(int(end_ts), {})[float(strike)] = stick

    ask_field = "high_dollars" if conservative else "close_dollars"
    bid_field = "low_dollars" if conservative else "close_dollars"
    found: list[Cross] = []
    for end_ts, rungs in sorted(by_minute.items()):
        left = maturity - end_ts
        if left < min_seconds:
            continue
        rows = []
        for strike in sorted(rungs):
            stick = rungs[strike]
            ask = _price(stick, "yes_ask", ask_field)
            bid = _price(stick, "yes_bid", bid_field)
            if ask is None or bid is None:
                continue
            rows.append((strike, ask, bid, _volume(stick)))
        best_ask = None  # cheapest YES seen at a lower strike
        for strike, ask, bid, volume in rows:
            if best_ask is not None:
                low_strike, low_ask, low_volume = best_ask
                if require_volume and (volume <= 0 or low_volume <= 0):
                    pass
                else:
                    edge, fees = pair_edge(low_ask, bid)
                    if edge > 0:
                        found.append(
                            Cross(
                                event_ticker=tape.event_ticker,
                                end_ts=end_ts,
                                low_strike=low_strike,
                                high_strike=strike,
                                buy_ask=low_ask,
                                sell_bid=bid,
                                edge=edge,
                                fees=fees,
                                low_volume=low_volume,
                                high_volume=volume,
                                seconds_left=left,
                            )
                        )
            if best_ask is None or ask < best_ask[1]:
                best_ask = (strike, ask, volume)
    return found


def scan_tapes(tapes: list[EventTape], **kwargs) -> dict:
    crosses: list[Cross] = []
    hours = 0
    for tape in tapes:
        if tape.error:
            continue
        hours += 1
        crosses.extend(scan_tape(tape, **kwargs))
    edges = [c.edge for c in crosses]
    events = {c.event_ticker for c in crosses}
    return {
        "hours": hours,
        "crosses": len(crosses),
        "hours_with_a_cross": len(events),
        "total_edge": sum(edges),
        "mean_edge": (sum(edges) / len(edges)) if edges else 0.0,
        "max_edge": max(edges) if edges else 0.0,
        "rows": sorted(crosses, key=lambda c: -c.edge),
    }


def render(close: dict, worst: dict) -> str:
    lines = [
        f"阶梯交叉扫描（{worst['hours']} 小时真 tape）",
        "",
        f"{'口径':<26}{'交叉数':>8}{'涉及小时':>10}{'均幅':>9}{'最大':>9}{'合计':>10}",
    ]
    for label, report in (("close（乐观）", close), ("worst（分钟内最差）", worst)):
        lines.append(
            f"{label:<26}{report['crosses']:>8}{report['hours_with_a_cross']:>10}"
            f"{report['mean_edge']:>9.4f}{report['max_edge']:>9.4f}{report['total_edge']:>10.2f}"
        )
    lines.append("")
    if not worst["crosses"]:
        lines.append(
            "worst 口径 0 条。阶梯没有可执行的静态套利 —— 这是干净的否定结果，"
            "不是「再调调门」。"
        )
    else:
        lines.append("worst 口径前几条：")
        lines.append(
            f"{'小时':<20}{'买 K1':>11}{'卖 K2':>11}{'ask':>7}{'bid':>7}{'净幅':>8}{'剩余秒':>8}"
        )
        for row in worst["rows"][:10]:
            lines.append(
                f"{row.event_ticker:<20}{row.low_strike:>11.2f}{row.high_strike:>11.2f}"
                f"{row.buy_ask:>7.2f}{row.sell_bid:>7.2f}{row.edge:>8.4f}{row.seconds_left:>8.0f}"
            )
    return "\n".join(lines)

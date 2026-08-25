from __future__ import annotations

from dataclasses import dataclass

from btchour.config import Settings
from btchour.fees import lock_exit_price
from btchour.kalshi import Market


@dataclass(frozen=True)
class OpenPosition:
    ticker: str
    event_ticker: str
    side: str
    cost: float
    count: float


@dataclass(frozen=True)
class ExitAction:
    reason: str
    price: float
    note: str


def side_bid(market: Market, side: str) -> float | None:
    if side == "yes":
        return market.yes_bid_effective
    return market.no_bid_effective


def evaluate_exit(
    position: OpenPosition,
    market: Market,
    model_p: float,
    seconds_left: float,
    settings: Settings,
) -> ExitAction | None:
    if not settings.allow_early_exit:
        return None
    if position.count <= 0 or position.cost <= 0:
        return None
    bid = side_bid(market, position.side)
    lock = lock_exit_price(position.cost, position.count, settings.target_profit)
    if bid is not None and lock is not None and bid + 1e-12 >= lock:
        return ExitAction(
            reason="lock_on_book",
            price=bid,
            note=f"bid {bid:.2f} locks >= {settings.target_profit:.0%} after exit fees (need {lock:.2f})",
        )
    if model_p + 1e-12 < settings.invalidate_p and bid is not None:
        return ExitAction(
            reason="invalidate",
            price=bid,
            note=f"model p={model_p:.1%} < {settings.invalidate_p:.0%}; flatten at bid {bid:.2f}",
        )
    if seconds_left <= settings.flatten_seconds and bid is not None:
        return ExitAction(
            reason="flatten_time",
            price=bid,
            note=f"{seconds_left:.0f}s left <= {settings.flatten_seconds:.0f}s TWAP window; flatten at bid {bid:.2f}",
        )
    return None

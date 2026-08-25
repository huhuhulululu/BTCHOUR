from __future__ import annotations

from dataclasses import dataclass

from btchour.config import Settings
from btchour.fees import lock_exit_price, round_trip_roi
from btchour.kalshi import Market


@dataclass(frozen=True)
class OpenPosition:
    ticker: str
    event_ticker: str
    side: str
    cost: float
    count: float
    peak_bid: float | None = None
    play: str = ""
    entry_p: float | None = None


@dataclass(frozen=True)
class ExitAction:
    reason: str
    price: float
    note: str


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction | None
    peak_bid: float | None


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
) -> ExitDecision:
    bid = side_bid(market, position.side)
    peak = position.peak_bid
    if bid is not None:
        peak = bid if peak is None else max(peak, bid)

    if not settings.allow_early_exit:
        return ExitDecision(None, peak)
    if position.count <= 0 or position.cost <= 0:
        return ExitDecision(None, peak)

    play = position.play or ""
    locked = play.startswith("lock")
    do_t = (play == "swing_t" or settings.playbook == "swing") and not locked

    lock20 = lock_exit_price(position.cost, position.count, settings.target_profit)
    if bid is not None and lock20 is not None and bid + 1e-12 >= lock20:
        return ExitDecision(
            ExitAction(
                reason="lock_on_book",
                price=bid,
                note=f"bid {bid:.2f} locks >= {settings.target_profit:.0%} after exit fees (need {lock20:.2f})",
            ),
            peak,
        )

    if locked:
        return ExitDecision(None, peak)

    clip = lock_exit_price(position.cost, position.count, settings.swing_target) if do_t else None
    if do_t and bid is not None and peak is not None and clip is not None and peak + 1e-12 >= clip:
        if peak - bid + 1e-12 >= settings.swing_trail:
            roi = round_trip_roi(position.cost, bid, position.count)
            return ExitDecision(
                ExitAction(
                    reason="t_trail",
                    price=bid,
                    note=f"trail {settings.swing_trail:.2f} from peak {peak:.2f} → {bid:.2f} (roi {roi:.1%})",
                ),
                peak,
            )

    if do_t and bid is not None and clip is not None and bid + 1e-12 >= clip:
        gap = model_p - bid
        if gap + 1e-12 < settings.swing_runner_gap:
            roi = round_trip_roi(position.cost, bid, position.count)
            return ExitDecision(
                ExitAction(
                    reason="t_clip",
                    price=bid,
                    note=f"做T clip {roi:.1%} at bid {bid:.2f} (target {settings.swing_target:.0%}); gap {gap:.1%} too small to runner",
                ),
                peak,
            )

    if (
        do_t
        and position.entry_p is not None
        and bid is not None
        and position.entry_p - model_p + 1e-12 >= settings.swing_fade
    ):
        return ExitDecision(
            ExitAction(
                reason="t_fade",
                price=bid,
                note=f"p faded {position.entry_p:.1%} → {model_p:.1%}; cut at bid {bid:.2f}",
            ),
            peak,
        )

    if model_p + 1e-12 < settings.invalidate_p and bid is not None:
        return ExitDecision(
            ExitAction(
                reason="invalidate",
                price=bid,
                note=f"model p={model_p:.1%} < {settings.invalidate_p:.0%}; flatten at bid {bid:.2f}",
            ),
            peak,
        )
    if seconds_left <= settings.flatten_seconds and bid is not None:
        return ExitDecision(
            ExitAction(
                reason="flatten_time",
                price=bid,
                note=f"{seconds_left:.0f}s left <= {settings.flatten_seconds:.0f}s TWAP window; flatten at bid {bid:.2f}",
            ),
            peak,
        )
    return ExitDecision(None, peak)

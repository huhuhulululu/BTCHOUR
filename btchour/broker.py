from __future__ import annotations

import uuid

from btchour.fees import fill_cost
from btchour.kalshi import KalshiClient
from btchour.strategy import Opportunity


def yes_book_quote(side: str, price: float) -> tuple[str, float]:
    """V2 quotes the YES leg only: bid=buy YES, ask=sell YES (= buy NO at 1-price)."""
    if side == "yes":
        return "bid", float(price)
    if side == "no":
        return "ask", round(max(0.01, min(0.99, 1.0 - float(price))), 4)
    raise ValueError(f"side must be yes or no, got {side!r}")


def yes_book_exit(side: str, exit_price: float) -> tuple[str, float]:
    """Flatten a long: sell YES, or sell NO by buying YES at 1 - no_price."""
    if side == "yes":
        return "ask", float(exit_price)
    if side == "no":
        return "bid", round(max(0.01, min(0.99, 1.0 - float(exit_price))), 4)
    raise ValueError(f"side must be yes or no, got {side!r}")


def live_submit(client: KalshiClient, opportunity: Opportunity) -> dict:
    client_order_id = str(uuid.uuid4())
    tif = "immediate_or_cancel" if opportunity.taker else "good_till_canceled"
    book_side, yes_price = yes_book_quote(opportunity.side, opportunity.limit_price)
    response = client.create_order(
        ticker=opportunity.ticker,
        side=book_side,
        price=yes_price,
        count=opportunity.count,
        time_in_force=tif,
        client_order_id=client_order_id,
        post_only=not opportunity.taker,
    )
    cost = fill_cost(opportunity.limit_price, opportunity.count, taker=opportunity.taker)
    return {
        "ticker": opportunity.ticker,
        "event_ticker": opportunity.event_ticker,
        "side": opportunity.side,
        "price": opportunity.limit_price,
        "count": opportunity.count,
        "fee": cost.fee,
        "cost": cost.cost,
        "mode": "live",
        "taker": opportunity.taker,
        "model_p": opportunity.model_p,
        "if_win_roi": cost.if_win_roi,
        "expected_roi": cost.expected_roi(opportunity.model_p),
        "status": "open",
        "raw": {
            "client_order_id": client_order_id,
            "response": response,
            "reason": opportunity.reason,
            "play": opportunity.play,
            "lock_price": opportunity.lock_price,
        },
    }


def live_flatten(client: KalshiClient, trade: dict, exit_price: float) -> dict:
    """Close a long: sell YES (book ask) or sell NO (book bid at 1 - no_price)."""
    client_order_id = str(uuid.uuid4())
    book_side, price = yes_book_exit(trade["side"], exit_price)
    response = client.create_order(
        ticker=trade["ticker"],
        side=book_side,
        price=price,
        count=trade["count"],
        time_in_force="immediate_or_cancel",
        client_order_id=client_order_id,
    )
    return {"client_order_id": client_order_id, "response": response, "book_side": book_side, "price": price}

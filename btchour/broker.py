from __future__ import annotations

import uuid

from btchour.fees import fill_cost
from btchour.kalshi import CRYPTO_EXCHANGE_INDEX, KalshiClient
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


def order_id_from_response(response: dict | None) -> str | None:
    if not response:
        return None
    nested = response.get("order")
    if isinstance(nested, dict) and nested.get("order_id"):
        return str(nested["order_id"])
    if response.get("order_id"):
        return str(response["order_id"])
    return None


def exchange_index_from_response(response: dict | None) -> int | None:
    if not response:
        return None
    nested = response.get("order")
    src = nested if isinstance(nested, dict) else response
    if isinstance(src, dict) and src.get("exchange_index") is not None:
        try:
            return int(src["exchange_index"])
        except (TypeError, ValueError):
            return None
    return None


def order_fill_count(order: dict | None) -> float:
    if not order:
        return 0.0
    for key in ("fill_count_fp", "fill_count"):
        raw = order.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def live_rest_one(client: KalshiClient, opportunity: Opportunity) -> dict:
    """One live post-only rest. Loop stays paper; this is the only real size."""
    one = opportunity
    if abs(float(opportunity.count) - 1.0) > 1e-12 or opportunity.taker:
        one = Opportunity(**{**opportunity.__dict__, "count": 1.0, "taker": False})
    trade = live_submit(client, one)
    trade["status"] = "working"
    trade["count"] = 1.0
    trade["mode"] = "paper"
    raw = trade.setdefault("raw", {})
    raw["live_one"] = True
    raw["live_order_id"] = order_id_from_response(raw.get("response") or {})
    raw["exchange_index"] = exchange_index_from_response(raw.get("response") or {})
    if raw["exchange_index"] is None and str(one.ticker).startswith("KXBTCD"):
        raw["exchange_index"] = CRYPTO_EXCHANGE_INDEX
    raw["rest"] = one.limit_price
    raw["ask"] = one.ask
    raw["play"] = one.play
    raw["lock_price"] = one.lock_price
    raw["reason"] = one.reason
    return trade


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

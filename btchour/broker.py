from __future__ import annotations

import uuid

from btchour.fees import fill_cost
from btchour.kalshi import KalshiClient
from btchour.strategy import Opportunity


def live_submit(client: KalshiClient, opportunity: Opportunity) -> dict:
    client_order_id = str(uuid.uuid4())
    tif = "immediate_or_cancel" if opportunity.taker else "good_till_canceled"
    response = client.create_order(
        ticker=opportunity.ticker,
        side=opportunity.book_side,
        price=opportunity.limit_price,
        count=opportunity.count,
        time_in_force=tif,
        client_order_id=client_order_id,
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
        "raw": {"client_order_id": client_order_id, "response": response, "reason": opportunity.reason},
    }

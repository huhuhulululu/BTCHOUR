from __future__ import annotations

from btchour.fees import fill_cost
from btchour.strategy import Opportunity


def paper_fill(opportunity: Opportunity) -> dict:
    cost = fill_cost(opportunity.limit_price, opportunity.count, taker=opportunity.taker)
    return {
        "ticker": opportunity.ticker,
        "event_ticker": opportunity.event_ticker,
        "side": opportunity.side,
        "price": opportunity.limit_price,
        "count": opportunity.count,
        "fee": cost.fee,
        "cost": cost.cost,
        "mode": "paper",
        "taker": opportunity.taker,
        "model_p": opportunity.model_p,
        "if_win_roi": cost.if_win_roi,
        "expected_roi": cost.expected_roi(opportunity.model_p),
        "status": "open" if opportunity.taker else "working",
        "raw": {"reason": opportunity.reason, "ask": opportunity.ask},
    }


def paper_settle(cost: float, count: float, side: str, result: str) -> float:
    won = (side == "yes" and result == "yes") or (side == "no" and result == "no")
    return (count - cost) if won else -cost

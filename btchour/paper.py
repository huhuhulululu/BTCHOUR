from __future__ import annotations

from btchour.fees import exit_proceeds, fill_cost, round_trip_roi
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
        "play": opportunity.play,
        "raw": {
            "reason": opportunity.reason,
            "ask": opportunity.ask,
            "play": opportunity.play,
            "lock_price": opportunity.lock_price,
        },
    }


def paper_settle(cost: float, count: float, side: str, result: str) -> float:
    won = (side == "yes" and result == "yes") or (side == "no" and result == "no")
    return (count - cost) if won else -cost


def paper_close(trade: dict, exit_price: float, reason: str) -> dict:
    count = float(trade["count"])
    entry_cost = float(trade["cost"])
    proceeds, exit_fee = exit_proceeds(exit_price, count, taker=True)
    pnl = proceeds - entry_cost
    return {
        "id": trade.get("id"),
        "ticker": trade["ticker"],
        "event_ticker": trade["event_ticker"],
        "side": trade["side"],
        "exit_price": exit_price,
        "exit_fee": exit_fee,
        "pnl": pnl,
        "roi": round_trip_roi(entry_cost, exit_price, count),
        "status": "closed",
        "result": reason,
        "reason": reason,
    }

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from btchour.broker import live_submit, yes_book_quote
from btchour.config import DATA_DIR, Settings, load_settings
from btchour.engine import make_client, scan_once
from btchour.kalshi import KalshiClient, KalshiError, read_exchange_status
from btchour.strategy import Opportunity, coupon_in_band


def _slim_order(row: dict) -> dict:
    keep = (
        "order_id",
        "client_order_id",
        "ticker",
        "side",
        "status",
        "remaining_count",
        "remaining_count_fp",
        "fill_count",
        "initial_count",
        "yes_price_dollars",
        "no_price_dollars",
        "price",
        "created_time",
    )
    return {key: row.get(key) for key in keep if row.get(key) is not None}


def _pick_wait(scan: dict, settings: Settings, ticker: str | None, side: str | None) -> Opportunity:
    waits = [
        Opportunity(**row) if isinstance(row, dict) else row
        for row in (scan.get("opportunities") or [])
        if (row["play"] if isinstance(row, dict) else row.play) == "impulse_wait"
    ]
    if ticker:
        waits = [row for row in waits if row.ticker == ticker]
    if side:
        waits = [row for row in waits if row.side == side]
    if not waits:
        raise RuntimeError("no impulse_wait coupon to hang; pass --ticker and --side")
    in_band = [row for row in waits if coupon_in_band(row.ask, settings)]
    chosen = (in_band or waits)[0]
    return Opportunity(**{**chosen.__dict__, "count": 1.0, "taker": False})


def hang_one(
    client: KalshiClient | None = None,
    settings: Settings | None = None,
    *,
    count: float = 1.0,
    ticker: str | None = None,
    side: str | None = None,
    cancel: bool = False,
) -> dict:
    """Place one live maker rest. Does not switch the paper loop to live."""
    settings = settings or load_settings()
    if count + 1e-12 < 1 or count > 1 + 1e-12:
        raise ValueError("hang test is one contract")
    if not settings.can_sign:
        return {"ok": False, "error": "signed hang needs a local key"}
    client = client or make_client(settings)
    exchange = read_exchange_status(client)
    if not exchange.get("can_trade"):
        return {"ok": False, "error": "exchange_not_trading", "exchange": exchange}
    scan = scan_once(client, settings, persist=False)
    opp = _pick_wait(scan, settings, ticker, side)
    book_side, yes_price = yes_book_quote(opp.side, opp.limit_price)
    try:
        trade = live_submit(client, opp)
    except KalshiError as exc:
        return {
            "ok": False,
            "error": str(exc)[:240],
            "status_code": exc.status,
            "ticker": opp.ticker,
            "side": opp.side,
            "rest": opp.limit_price,
            "book_side": book_side,
            "yes_price": yes_price,
            "ask": opp.ask,
            "mode": "live_hang",
            "loop_mode": settings.mode,
        }
    raw = trade.get("raw") or {}
    response = raw.get("response") or {}
    order_id = response.get("order_id")
    resting = []
    try:
        resting = [_slim_order(row) for row in client.orders(status="resting", ticker=opp.ticker)]
    except KalshiError:
        resting = []
    cancelled = None
    if cancel and order_id:
        try:
            cancelled = client.cancel_order(order_id, market_ticker=opp.ticker)
        except KalshiError as exc:
            cancelled = {"error": str(exc)[:240], "status_code": exc.status}
    report = {
        "ok": True,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live_hang",
        "loop_mode": settings.mode,
        "ticker": opp.ticker,
        "side": opp.side,
        "count": 1,
        "rest": opp.limit_price,
        "book_side": book_side,
        "yes_price": yes_price,
        "ask": opp.ask,
        "reason": opp.reason,
        "order_id": order_id,
        "client_order_id": raw.get("client_order_id"),
        "fill_count": response.get("fill_count"),
        "remaining_count": response.get("remaining_count"),
        "resting": resting,
        "cancelled": cancelled,
        "kept": cancelled is None,
        "note": "One live maker rest. Paper loop stays paper. 10%–50% is the clip band, not a guarantee.",
    }
    dest = DATA_DIR / "hang-probe.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, default=str) + "\n")
    report["wrote"] = str(Path("data") / dest.name)
    return report

from __future__ import annotations

import time
from datetime import datetime, timezone

from btchour.broker import live_submit
from btchour.catalog import current_hourly_events, sync_catalog
from btchour.config import Settings, load_settings
from btchour.kalshi import KalshiClient, Market, market_from_api
from btchour.model import SpotQuote
from btchour.paper import paper_fill, paper_settle
from btchour.spot import fetch_spot
from btchour.store import Store
from btchour.strategy import Opportunity, scan_markets


def make_client(settings: Settings) -> KalshiClient:
    return KalshiClient(
        base=settings.kalshi_base,
        user_agent=settings.user_agent,
        api_key_id=settings.api_key_id,
        private_key_pem=settings.private_key_pem,
    )


def _markets_from_snapshot(snapshot: dict) -> list[Market]:
    rows = ((snapshot.get("current_hour") or {}).get("markets")) or []
    markets: list[Market] = []
    event = ((snapshot.get("current_hour") or {}).get("event") or {}).get("event_ticker") or ""
    for row in rows:
        markets.append(
            market_from_api(
                {
                    "ticker": row["ticker"],
                    "event_ticker": event,
                    "title": "Bitcoin price",
                    "subtitle": row.get("subtitle") or "",
                    "status": row.get("status") or "active",
                    "floor_strike": row.get("strike"),
                    "strike_type": row.get("strike_type") or "greater",
                    "yes_bid_dollars": row.get("yes_bid"),
                    "yes_ask_dollars": row.get("yes_ask"),
                    "no_bid_dollars": row.get("no_bid"),
                    "no_ask_dollars": row.get("no_ask"),
                    "last_price_dollars": row.get("last"),
                    "volume_fp": row.get("volume"),
                    "open_interest_fp": row.get("open_interest"),
                    "open_time": row.get("open_time"),
                    "close_time": row.get("close_time"),
                    "result": row.get("result") or "",
                }
            )
        )
    return markets


def scan_once(client: KalshiClient, settings: Settings | None = None, persist: bool = True) -> dict:
    settings = settings or load_settings()
    snapshot = sync_catalog(client, settings)
    spot_info = snapshot["spot"]
    spot = SpotQuote(
        price=spot_info["price"],
        source=spot_info["source"],
        twap60=spot_info.get("twap60"),
        annual_vol=spot_info.get("annual_vol") or settings.annual_vol,
        ts_ms=spot_info.get("ts_ms"),
    )
    markets = _markets_from_snapshot(snapshot)
    opportunities = scan_markets(markets, spot, settings)
    payload = {
        "synced_at": snapshot["synced_at"],
        "spot": spot_info,
        "event": (snapshot.get("current_hour") or {}).get("event"),
        "market_count": len(markets),
        "opportunities": [item.as_dict() for item in opportunities],
    }
    if persist:
        store = Store()
        event_ticker = (payload["event"] or {}).get("event_ticker")
        store.record_scan(event_ticker, spot.price, payload["opportunities"])
    return payload


def _execute(opportunity: Opportunity, client: KalshiClient, settings: Settings, store: Store) -> dict:
    if store.has_open(opportunity.ticker, opportunity.side):
        return {"skipped": True, "reason": "already open", "ticker": opportunity.ticker}
    if settings.live:
        if not settings.can_sign:
            raise RuntimeError("live mode needs KALSHI_API_KEY_ID and a private key")
        trade = live_submit(client, opportunity)
    else:
        trade = paper_fill(opportunity)
    trade_id = store.record_trade(trade)
    trade["id"] = trade_id
    return trade


def settle_open(client: KalshiClient, store: Store) -> list[dict]:
    updates = []
    for row in store.open_trades():
        markets = client.markets_by_event(row["event_ticker"])
        match = next((m for m in markets if m.ticker == row["ticker"]), None)
        if not match or match.result not in {"yes", "no"}:
            continue
        pnl = paper_settle(row["cost"], row["count"], row["side"], match.result)
        store.settle_trade(row["id"], match.result, pnl)
        updates.append({"id": row["id"], "ticker": row["ticker"], "result": match.result, "pnl": pnl})
    return updates


def run_cycle(client: KalshiClient | None = None, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    client = client or make_client(settings)
    store = Store()
    settlements = settle_open(client, store)
    scan = scan_once(client, settings, persist=True)
    taken = []
    for item in scan["opportunities"][:1]:
        opp = Opportunity(**item)
        taken.append(_execute(opp, client, settings, store))
    return {
        "mode": settings.mode,
        "scan": scan,
        "taken": taken,
        "settlements": settlements,
        "summary": store.summary(),
    }


def run_loop(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    client = make_client(settings)
    while True:
        cycle = run_cycle(client, settings)
        event = (cycle["scan"].get("event") or {}).get("event_ticker")
        n = len(cycle["scan"]["opportunities"])
        print(
            f"{datetime.now(timezone.utc).isoformat()} mode={settings.mode} event={event} "
            f"spot={cycle['scan']['spot']['price']:.2f} opps={n} taken={len(cycle['taken'])} "
            f"settled_now={len(cycle['settlements'])} pnl={cycle['summary']['realized_pnl']:.4f}",
            flush=True,
        )
        for opp in cycle["scan"]["opportunities"][:5]:
            print(f"  {opp['reason']}", flush=True)
        time.sleep(max(2, settings.poll_seconds))

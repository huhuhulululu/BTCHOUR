from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from btchour.broker import live_flatten, live_submit
from btchour.catalog import sync_catalog
from btchour.config import Settings, load_settings
from btchour.exits import OpenPosition, evaluate_exit
from btchour.fees import fill_cost
from btchour.kalshi import KalshiClient, Market, market_from_api
from btchour.model import SpotQuote, digital_prob, effective_vol, sigma_cushion
from btchour.paper import paper_close, paper_fill, paper_settle
from btchour.score import score_market
from btchour.store import Store
from btchour.learn import diagnose_impulse, merge_impulse, tape_impulse
from btchour.strategy import (
    Opportunity,
    _seconds_left,
    apply_swing_memory,
    refresh_session,
    scan_markets,
)

_LAST_FULL_SYNC = 0.0


def make_client(settings: Settings) -> KalshiClient:
    return KalshiClient(
        base=settings.kalshi_base,
        user_agent=settings.user_agent,
        api_key_id=settings.api_key_id,
        private_key_pem=settings.private_key_pem,
    )


def _market_from_row(row: dict, event_ticker: str) -> Market:
    return market_from_api(
        {
            "ticker": row["ticker"],
            "event_ticker": row.get("event_ticker") or event_ticker,
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


def _markets_from_snapshot(snapshot: dict) -> list[Market]:
    blocks = snapshot.get("tradable") or []
    if blocks:
        markets: list[Market] = []
        for block in blocks:
            event = ((block.get("event") or {}).get("event_ticker")) or ""
            for row in block.get("markets") or []:
                markets.append(_market_from_row(row, event))
        return markets
    event = ((snapshot.get("current_hour") or {}).get("event") or {}).get("event_ticker") or ""
    return [_market_from_row(row, event) for row in ((snapshot.get("current_hour") or {}).get("markets")) or []]


def scan_once(client: KalshiClient, settings: Settings | None = None, persist: bool = True) -> dict:
    global _LAST_FULL_SYNC
    settings = settings or load_settings()
    now_mono = time.time()
    light = now_mono - _LAST_FULL_SYNC < 45
    if not light:
        _LAST_FULL_SYNC = now_mono
    snapshot = sync_catalog(client, settings, light=light)
    spot_info = dict(snapshot["spot"])
    store = Store()
    now = datetime.now(timezone.utc)
    event_hint = ((snapshot.get("current_hour") or {}).get("event") or {}).get("event_ticker")
    tape = tape_impulse(store.tape_points(event_hint), now, float(spot_info["price"]))
    merged = merge_impulse(float(spot_info.get("impulse") or 0.0), tape)
    spot_info["impulse"] = merged
    spot_info["tape_impulse"] = tape
    snapshot["spot"] = spot_info
    spot = SpotQuote(
        price=spot_info["price"],
        source=spot_info["source"],
        twap60=spot_info.get("twap60"),
        annual_vol=spot_info.get("annual_vol") or settings.annual_vol,
        ts_ms=spot_info.get("ts_ms"),
        impulse=merged,
    )
    markets = _markets_from_snapshot(snapshot)
    session = refresh_session(store.session_memory(), event_hint)
    opportunities = apply_swing_memory(
        scan_markets(markets, spot, settings),
        store.swing_memories(),
        session,
    )
    diagnosis = diagnose_impulse(markets, spot, settings, now)
    scored = []
    for market in markets:
        seconds = _seconds_left(market.close_time, now)
        scored.extend(
            score_market(
                market,
                spot,
                seconds,
                effective_vol(spot.annual_vol, settings.annual_vol),
                settings.target_profit,
                settings.min_win_prob,
                settings.min_expected_roi,
            )
        )
    scored.sort(key=lambda row: row.ev, reverse=True)
    payload = {
        "synced_at": snapshot["synced_at"],
        "spot": spot_info,
        "event": (snapshot.get("current_hour") or {}).get("event"),
        "market_count": len(markets),
        "playbook": settings.playbook,
        "opportunities": [item.as_dict() for item in opportunities],
        "formula": "EV = p * b - (1 - p)",
        "best_ev": [row.as_dict() for row in scored[:8]],
        "diagnosis": diagnosis,
        "light": light,
        "snapshot": snapshot,
    }
    if persist:
        event_ticker = (payload["event"] or {}).get("event_ticker")
        store.record_scan(event_ticker, spot.price, payload["opportunities"])
        if abs(merged) >= 40 or diagnosis.get("status") != "no_impulse":
            top = (diagnosis.get("candidates") or [{}])[:1]
            reject = ""
            if top:
                row = top[0]
                reject = (
                    f"{row.get('ticker')} ask={row.get('ask')} p={row.get('p')} "
                    f"{','.join(row.get('reasons') or [])}"
                )
            store.record_journal(event_ticker, spot.price, merged, tape, str(diagnosis.get("status")), reject)
    return payload


def _execute(opportunity: Opportunity, client: KalshiClient, settings: Settings, store: Store) -> dict:
    if store.has_open(opportunity.ticker, opportunity.side):
        return {"skipped": True, "reason": "already open", "ticker": opportunity.ticker}
    if store.open_trades() and opportunity.taker:
        return {"skipped": True, "reason": "already have an open fill"}
    if not opportunity.taker:
        if opportunity.play != "lock_wait":
            return {
                "skipped": True,
                "reason": "maker rest is not a fill",
                "ticker": opportunity.ticker,
                "play": opportunity.play,
            }
        if len(store.working_trades()) >= 3:
            return {"skipped": True, "reason": "enough working waits"}
        if settings.live:
            if not settings.can_sign:
                raise RuntimeError("live mode needs KALSHI_API_KEY_ID and a private key")
            trade = live_submit(client, opportunity)
            trade["status"] = "working"
        else:
            trade = paper_fill(opportunity)
        trade_id = store.record_trade(trade)
        trade["id"] = trade_id
        return trade
    if settings.live:
        if not settings.can_sign:
            raise RuntimeError("live mode needs KALSHI_API_KEY_ID and a private key")
        trade = live_submit(client, opportunity)
    else:
        trade = paper_fill(opportunity)
    if trade.get("status") != "open":
        return {**trade, "skipped": True, "reason": "not a fill"}
    for row in store.working_trades():
        store.cancel_trade(row["id"], "taken_elsewhere")
    trade_id = store.record_trade(trade)
    trade["id"] = trade_id
    return trade


def _lookup_market(client: KalshiClient, trade: dict, markets: list[Market]) -> Market | None:
    match = next((item for item in markets if item.ticker == trade["ticker"]), None)
    if match:
        return match
    try:
        found = client.markets_by_event(trade["event_ticker"])
    except Exception:
        return None
    return next((item for item in found if item.ticker == trade["ticker"]), None)


def _close_position(row, action, client: KalshiClient, settings: Settings, store: Store) -> dict:
    trade = dict(row)
    raw = {}
    try:
        raw = json.loads(row["raw"] or "{}")
    except Exception:
        raw = {}
    if settings.live:
        if not settings.can_sign:
            raise RuntimeError("live mode needs KALSHI_API_KEY_ID and a private key")
        try:
            raw["flatten"] = live_flatten(client, trade, action.price)
        except Exception as exc:
            return {"id": row["id"], "ticker": trade["ticker"], "skipped": True, "error": str(exc)}
    closed = paper_close(trade, action.price, action.reason)
    raw.update(
        {
            "exit_reason": action.reason,
            "exit_price": action.price,
            "exit_note": action.note,
            "exit_fee": closed["exit_fee"],
        }
    )
    store.close_trade(row["id"], action.reason, closed["pnl"], raw)
    closed["id"] = row["id"]
    closed["note"] = action.note
    return closed


def refresh_working(
    store: Store,
    settings: Settings,
    markets: list[Market],
    spot: SpotQuote,
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    by_ticker = {item.ticker: item for item in markets}
    updates = []
    for row in store.working_trades():
        market = by_ticker.get(row["ticker"])
        if market is None or market.strike is None:
            continue
        ask = market.yes_ask_effective if row["side"] == "yes" else market.no_ask_effective
        if ask is not None and ask + 1e-12 <= float(row["price"]):
            filled = fill_cost(ask, float(row["count"]), taker=True)
            store.promote_working(row["id"], ask, filled.fee, filled.cost, filled.if_win_roi)
            updates.append({"id": row["id"], "ticker": row["ticker"], "status": "open", "price": ask, "reason": "wait_crossed"})
            continue
        seconds = _seconds_left(market.close_time, now)
        vol = effective_vol(spot.annual_vol, settings.annual_vol)
        p_yes = digital_prob(spot.price, market.strike, max(seconds, 1.0), vol)
        model_p = p_yes if row["side"] == "yes" else 1.0 - p_yes
        sigma = sigma_cushion(spot.price, market.strike, max(seconds, 1.0), vol)
        if model_p + 1e-12 < settings.lock_min_p or sigma + 1e-12 < settings.min_sigma or seconds < 8:
            store.cancel_trade(row["id"], "wait_invalid")
            updates.append({"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": "wait_invalid"})
    return updates


def manage_open(
    client: KalshiClient,
    store: Store,
    settings: Settings,
    markets: list[Market],
    spot: SpotQuote,
    now: datetime | None = None,
) -> list[dict]:
    if not settings.allow_early_exit:
        return []
    now = now or datetime.now(timezone.utc)
    updates = []
    for row in store.open_trades():
        market = _lookup_market(client, dict(row), markets)
        if market is None or market.strike is None:
            continue
        if market.result in {"yes", "no"}:
            continue
        seconds = _seconds_left(market.close_time, now)
        vol = effective_vol(spot.annual_vol, settings.annual_vol)
        p_yes = digital_prob(spot.price, market.strike, max(seconds, 1.0), vol)
        model_p = p_yes if row["side"] == "yes" else 1.0 - p_yes
        raw = {}
        try:
            raw = json.loads(row["raw"] or "{}")
        except Exception:
            raw = {}
        decision = evaluate_exit(
            OpenPosition(
                ticker=row["ticker"],
                event_ticker=row["event_ticker"],
                side=row["side"],
                cost=float(row["cost"]),
                count=float(row["count"]),
                peak_bid=raw.get("peak_bid"),
                play=raw.get("play") or "",
                entry_p=float(row["model_p"]),
            ),
            market,
            model_p,
            seconds,
            settings,
        )
        if decision.peak_bid != raw.get("peak_bid"):
            raw["peak_bid"] = decision.peak_bid
            store.update_raw(row["id"], raw)
        if decision.action:
            updates.append(_close_position(row, decision.action, client, settings, store))
    return updates


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
    spot_info = scan["spot"]
    spot = SpotQuote(
        price=spot_info["price"],
        source=spot_info["source"],
        twap60=spot_info.get("twap60"),
        annual_vol=spot_info.get("annual_vol") or settings.annual_vol,
        ts_ms=spot_info.get("ts_ms"),
        impulse=float(spot_info.get("impulse") or 0.0),
    )
    markets = _markets_from_snapshot(scan.get("snapshot") or {})
    waits = refresh_working(store, settings, markets, spot)
    exits = manage_open(client, store, settings, markets, spot)
    taken = []
    if not store.open_trades():
        opps = scan["opportunities"]
        takers = [item for item in opps if item.get("taker")]
        rest = [item for item in opps if not item.get("taker") and item.get("play") == "lock_wait"]
        chosen = takers[:1] or rest[:3]
        for item in chosen:
            taken.append(_execute(Opportunity(**item), client, settings, store))
            if store.open_trades():
                break
    scan.pop("snapshot", None)
    return {
        "mode": settings.mode,
        "playbook": settings.playbook,
        "scan": scan,
        "taken": taken,
        "waits": waits,
        "exits": exits,
        "settlements": settlements,
        "summary": store.summary(),
    }


def run_loop(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    client = make_client(settings)
    while True:
        try:
            cycle = run_cycle(client, settings)
        except Exception as exc:
            print(f"{datetime.now(timezone.utc).isoformat()} loop_error {exc}", flush=True)
            time.sleep(max(2, settings.poll_seconds))
            continue
        event = (cycle["scan"].get("event") or {}).get("event_ticker")
        n = len(cycle["scan"]["opportunities"])
        spot = cycle["scan"]["spot"]
        diagnosis = cycle["scan"].get("diagnosis") or {}
        print(
            f"{datetime.now(timezone.utc).isoformat()} mode={settings.mode} playbook={settings.playbook} "
            f"event={event} spot={spot['price']:.2f} impulse={float(spot.get('impulse') or 0):+.0f} "
            f"tape={float(spot.get('tape_impulse') or 0):+.0f} diag={diagnosis.get('status')} "
            f"opps={n} taken={len(cycle['taken'])} exits={len(cycle['exits'])} "
            f"settled_now={len(cycle['settlements'])} pnl={cycle['summary']['realized_pnl']:.4f}",
            flush=True,
        )
        for opp in cycle["scan"]["opportunities"][:5]:
            print(f"  {opp['reason']}", flush=True)
        if diagnosis.get("status") == "blocked":
            for row in (diagnosis.get("candidates") or [])[:2]:
                print(f"  reject {row.get('side')} {row.get('ticker')} ask={row.get('ask')} p={row.get('p')} {row.get('reasons')}", flush=True)
        for item in cycle["exits"]:
            print(f"  exit {item.get('ticker')} {item.get('result')} pnl={item.get('pnl')}", flush=True)
        time.sleep(max(2, settings.poll_seconds))

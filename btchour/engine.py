from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from btchour.broker import (
    flatten_contract_exit,
    live_flatten,
    live_rest_one,
    live_submit,
    market_position_map,
    order_fill_count,
)
from btchour.catalog import sync_catalog
from btchour.config import ROOT, Settings, load_settings
from btchour.exits import OpenPosition, evaluate_exit
from btchour.fees import TICK, fill_cost
from btchour.kalshi import (
    KalshiClient,
    Market,
    market_from_api,
    market_minute_extremes,
    read_exchange_status,
)
from btchour.model import SpotQuote, digital_prob, effective_vol, sigma_cushion
from btchour.paper import paper_close, paper_fill, paper_settle
from btchour.score import score_market
from btchour.store import Store
from btchour.learn import diagnose_impulse, journal_line, merge_impulse, tape_impulse
from btchour.tickers import format_et, next_session_event_ticker
from btchour.strategy import (
    hour_minute,
    WAIT_PLAYS,
    Opportunity,
    _seconds_left,
    apply_swing_memory,
    coupon_in_band,
    coupon_rest_ready,
    is_next_session_book,
    pick_flex_entries,
    impulse_wait_wrong_side,
    refresh_session,
    tape_at_rest,
    wait_book_crossed,
    scan_markets,
)

_LAST_FULL_SYNC = 0.0
CYCLE_TIMEOUT_SECONDS = 25
STALL_SECONDS = 60
_EXTREME_CACHE: dict[str, tuple[float, dict]] = {}
_EXTREME_TTL = 15.0
_TAPE_CACHE: dict[str, tuple[float, list]] = {}
_TAPE_TTL = 8.0


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
            store.record_journal(
                event_ticker,
                spot.price,
                merged,
                tape,
                str(diagnosis.get("status")),
                journal_line(diagnosis),
            )
    return payload


def _execute(
    opportunity: Opportunity,
    client: KalshiClient,
    settings: Settings,
    store: Store,
    can_trade: bool = True,
) -> dict:
    if not can_trade:
        return {"skipped": True, "reason": "exchange_not_trading", "ticker": opportunity.ticker}
    if store.has_open(opportunity.ticker, opportunity.side):
        return {"skipped": True, "reason": "already open", "ticker": opportunity.ticker}
    if store.open_trades() and opportunity.taker:
        return {"skipped": True, "reason": "already have an open fill"}
    if not opportunity.taker:
        if opportunity.play not in WAIT_PLAYS:
            return {
                "skipped": True,
                "reason": "maker rest is not a fill",
                "ticker": opportunity.ticker,
                "play": opportunity.play,
            }
        working = store.working_trades()
        if opportunity.play == "impulse_wait":
            coupons = [row for row in working if _row_play(row) == "impulse_wait"]
            if len(coupons) >= 3:
                pads = [row for row in coupons if not coupon_in_band(_row_ask(row), settings)]
                if coupon_in_band(opportunity.ask, settings) and pads:
                    worst = max(pads, key=_row_ask)
                    _cancel_working(store, worst, "wait_replace", client)
                else:
                    return {"skipped": True, "reason": "enough working coupons", "ticker": opportunity.ticker}
            if any(
                row["ticker"] == opportunity.ticker and row["side"] == opportunity.side
                for row in working
            ):
                return {"skipped": True, "reason": "ticker already working", "ticker": opportunity.ticker}
        elif len(working) >= 3:
            return {"skipped": True, "reason": "enough working waits"}
        if settings.live:
            if not settings.can_sign:
                raise RuntimeError("live mode needs KALSHI_API_KEY_ID and a private key")
            trade = live_submit(client, opportunity)
            trade["status"] = "working"
        elif (
            settings.live_one
            and settings.can_sign
            and opportunity.play == "impulse_wait"
        ):
            if not coupon_in_band(opportunity.ask, settings):
                return {
                    "skipped": True,
                    "reason": "pad_not_live",
                    "ticker": opportunity.ticker,
                    "ask": opportunity.ask,
                }
            if any(_is_live_one(row) for row in working):
                return {"skipped": True, "reason": "already_one_live", "ticker": opportunity.ticker}
            leftover = leftover_live_one_positions(client, store)
            if leftover:
                return {
                    "skipped": True,
                    "reason": "leftover_live",
                    "ticker": opportunity.ticker,
                    "leftover": leftover,
                }
            if _live_resting(client, opportunity.event_ticker):
                return {"skipped": True, "reason": "already_one_live", "ticker": opportunity.ticker}
            if any(_row_play(row) == "impulse_wait" for row in working):
                return {"skipped": True, "reason": "one_at_a_time", "ticker": opportunity.ticker}
            trade = live_rest_one(client, opportunity)
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
        _cancel_working(store, row, "taken_elsewhere", client)
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


def _close_position(
    row,
    action,
    client: KalshiClient,
    settings: Settings,
    store: Store,
    can_trade: bool = True,
    market: Market | None = None,
) -> dict:
    trade = dict(row)
    raw = {}
    try:
        raw = json.loads(row["raw"] or "{}")
    except Exception:
        raw = {}
    if not can_trade:
        return {"id": row["id"], "ticker": trade["ticker"], "skipped": True, "reason": "exchange_not_trading"}
    exit_price = float(action.price)
    if settings.live or raw.get("live_one"):
        if not settings.can_sign:
            raise RuntimeError("live flatten needs KALSHI_API_KEY_ID and a private key")
        if market is None:
            market = _lookup_market(client, trade, [])
        if market is None:
            return {"id": row["id"], "ticker": trade["ticker"], "skipped": True, "reason": "flatten_no_book"}
        flatten, filled, actual = _live_flatten_until_fill(client, trade, exit_price, market)
        raw["flatten"] = flatten
        if filled <= 0 or actual is None:
            store.update_raw(row["id"], raw)
            return {
                "id": row["id"],
                "ticker": trade["ticker"],
                "skipped": True,
                "reason": "flatten_unfilled",
            }
        exit_price = actual
    closed = paper_close(trade, exit_price, action.reason)
    raw.update(
        {
            "exit_reason": action.reason,
            "exit_price": exit_price,
            "exit_note": action.note,
            "exit_fee": closed["exit_fee"],
        }
    )
    store.close_trade(row["id"], action.reason, closed["pnl"], raw)
    closed["id"] = row["id"]
    closed["note"] = action.note
    return closed


def _live_flatten_until_fill(
    client: KalshiClient,
    trade: dict,
    exit_price: float,
    market: Market,
) -> tuple[dict, float, float | None]:
    """Cross the book, retry once harder. Fill count 0 is not a close."""
    last = {}
    for slip in (2, 8):
        try:
            last = live_flatten(client, trade, exit_price, market=market, slip_ticks=slip)
        except Exception as exc:
            last = {"error": str(exc), "slip_ticks": slip}
            continue
        response = last.get("response") or {}
        filled = order_fill_count(response)
        if filled <= 0:
            continue
        actual = flatten_contract_exit(trade["side"], response)
        if actual is None:
            yes_px = last.get("price")
            try:
                yes_px = float(yes_px)
            except (TypeError, ValueError):
                continue
            actual = yes_px if trade["side"] == "yes" else round(max(0.01, min(0.99, 1.0 - yes_px)), 4)
        return last, filled, actual
    return last, 0.0, None


def leftover_live_one_positions(client: KalshiClient, store: Store) -> dict[str, float]:
    """Exchange inventory on any of our live_one tickers.

    Used to block a second ticket. Reconcile flatten is narrower: only
    sqlite-closed leftovers (378). A working/open fill is not leftover.
    """
    ours = {row["ticker"] for row in store.live_one_rows()}
    if not ours:
        return {}
    held = market_position_map(client)
    return {ticker: size for ticker, size in held.items() if ticker in ours}


def reconcile_live_one(
    client: KalshiClient,
    store: Store,
    settings: Settings,
    markets: list[Market] | None = None,
    can_trade: bool = True,
) -> list[dict]:
    """If sqlite is already closed but the exchange still holds our live_one, flatten it."""
    if not can_trade or not settings.live_one or not settings.can_sign:
        return []
    leftover = leftover_live_one_positions(client, store)
    if not leftover:
        return []
    markets = markets or []
    updates = []
    for ticker, size in leftover.items():
        row = next((item for item in store.live_one_rows() if item["ticker"] == ticker), None)
        if row is None:
            continue
        if str(row["status"] or "") != "closed":
            # 379: working just filled. refresh_working / manage_open own it.
            continue
        trade = dict(row)
        market = _lookup_market(client, trade, markets)
        if market is None:
            updates.append({"ticker": ticker, "skipped": True, "reason": "flatten_no_book", "size": size})
            continue
        mark = 0.50 if trade["side"] == "yes" else 0.50
        if trade["side"] == "yes" and market.yes_bid_effective is not None:
            mark = float(market.yes_bid_effective)
        if trade["side"] == "no" and market.no_bid_effective is not None:
            mark = float(market.no_bid_effective)
        flatten, filled, actual = _live_flatten_until_fill(client, trade, mark, market)
        raw = _row_raw(row)
        raw["flatten_reconcile"] = flatten
        raw["leftover_size"] = size
        if filled <= 0 or actual is None:
            store.update_raw(row["id"], raw)
            updates.append({"id": row["id"], "ticker": ticker, "skipped": True, "reason": "flatten_unfilled"})
            continue
        closed = paper_close(trade, actual, row["result"] or "flatten_reconcile")
        raw["exit_price"] = actual
        raw["exit_fee"] = closed["exit_fee"]
        raw["exit_note"] = f"reconcile leftover {size:+.2f} at {actual:.2f}"
        if row["status"] == "closed":
            store.update_pnl(row["id"], closed["pnl"], raw)
        else:
            store.close_trade(row["id"], row["result"] or "flatten_reconcile", closed["pnl"], raw)
        updates.append(
            {
                "id": row["id"],
                "ticker": ticker,
                "result": "flatten_reconcile",
                "pnl": closed["pnl"],
                "exit_price": actual,
            }
        )
    return updates


def _row_play(row) -> str:
    try:
        raw = json.loads(row["raw"] or "{}")
    except Exception:
        raw = {}
    return str(raw.get("play") or "")


def _row_raw(row) -> dict:
    try:
        raw = json.loads(row["raw"] or "{}")
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _is_live_one(row) -> bool:
    raw = _row_raw(row)
    return bool(raw.get("live_one") and raw.get("live_order_id"))


def _wait_cancel_reason(row, impulse: float, settings: Settings, seconds: float) -> str | None:
    """Wrong-side and ATM-pad live rests come off. Paper fade on the dump stays.

    014: a live_one rest that is no longer a same-way $100 dump/rally comes off
    before the exchange can fill the noise (382–388). Paper waits still sit.
    """
    raw = _row_raw(row)
    if settings.live_one and _is_live_one(row):
        hung_ask = raw.get("ask")
        if hung_ask is not None and not coupon_in_band(float(hung_ask), settings):
            return "pad_not_live"
    if impulse_wait_wrong_side(row["side"], impulse, settings):
        return "wait_wrong_side"
    if settings.live_one and _is_live_one(row) and not coupon_rest_ready(row["side"], impulse, settings):
        return "wait_fade"
    if seconds + 1e-12 < settings.swing_min_seconds:
        return "wait_invalid"
    return None


def _live_resting(client: KalshiClient, event_ticker: str | None = None) -> list:
    try:
        rows = client.orders(status="resting")
    except Exception:
        return []
    if event_ticker:
        prefix = str(event_ticker)
        rows = [row for row in rows if str(row.get("ticker") or "").startswith(prefix)]
    return rows


def _cancel_working(store: Store, row, reason: str, client: KalshiClient | None = None) -> None:
    raw = _row_raw(row)
    order_id = raw.get("live_order_id")
    if order_id and client is not None:
        idx = raw.get("exchange_index")
        try:
            client.cancel_order(
                str(order_id),
                market_ticker=row["ticker"],
                exchange_index=int(idx) if idx is not None else None,
            )
        except Exception as exc:
            print(f"cancel_order {order_id} {row['ticker']} failed: {exc}", flush=True)
    store.cancel_trade(row["id"], reason)


def clear_paper_bulk_waits(store: Store) -> list[int]:
    """Paper 10-lot hangs are not a test. One live contract at a time."""
    cancelled = []
    for row in store.working_trades():
        if _row_play(row) == "impulse_wait" and not _is_live_one(row):
            store.cancel_trade(row["id"], "paper_bulk")
            cancelled.append(int(row["id"]))
    return cancelled


def cancel_stale_live_rests(client: KalshiClient, now: datetime | None = None) -> list[dict]:
    """A leftover hang on a closed hour must not block the next one-contract test."""
    now = now or datetime.now(timezone.utc)
    current = next_session_event_ticker(now)
    cancelled = []
    for row in _live_resting(client):
        ticker = str(row.get("ticker") or "")
        event = ticker.rsplit("-T", 1)[0] if "-T" in ticker else ""
        if event and event != current and row.get("order_id"):
            try:
                idx = row.get("exchange_index")
                client.cancel_order(
                    str(row["order_id"]),
                    market_ticker=ticker,
                    exchange_index=int(idx) if idx is not None else None,
                )
                cancelled.append({"ticker": ticker, "order_id": row["order_id"]})
            except Exception as exc:
                print(f"cancel_stale {row.get('order_id')} {ticker} failed: {exc}", flush=True)
                continue
    return cancelled


def _find_live_order(client: KalshiClient, row) -> dict | None:
    raw = _row_raw(row)
    order_id = raw.get("live_order_id")
    if not order_id:
        return None
    try:
        rows = client.orders(ticker=row["ticker"])
    except Exception:
        return None
    return next((item for item in rows if str(item.get("order_id")) == str(order_id)), None)


def _row_ask(row) -> float:
    try:
        raw = json.loads(row["raw"] or "{}")
    except Exception:
        raw = {}
    try:
        return float(raw.get("ask") or 1.0)
    except (TypeError, ValueError):
        return 1.0


def _row_created_at(row) -> datetime | None:
    raw = row["created_at"] if "created_at" in row.keys() else None
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def market_prints(client: KalshiClient, ticker: str, min_ts: int | None) -> list:
    """Recent public prints. Cached a few seconds so three rests share one pull."""
    now_s = time.time()
    hit = _TAPE_CACHE.get(ticker)
    if hit and hit[0] > now_s:
        return hit[1]
    try:
        rows = client.market_trades(ticker, min_ts=min_ts, limit=1000)
    except Exception:
        rows = []
    _TAPE_CACHE[ticker] = (now_s + _TAPE_TTL, rows)
    return rows


def paper_rest_tape(
    row,
    rest: float,
    *,
    client: KalshiClient | None = None,
    tapes: dict | None = None,
) -> float | None:
    """Observed size at the rest. None means the caller has no tape (unit tests)."""
    ticker = row["ticker"]
    created = _row_created_at(row)
    if tapes is not None:
        trades = tapes.get(ticker) or []
    elif client is not None:
        min_ts = int(created.timestamp()) if created is not None else None
        trades = market_prints(client, ticker, min_ts)
    else:
        return None
    return tape_at_rest(trades, row["side"], rest, since=created)


def working_extremes(client: KalshiClient, tickers: list[str], now: datetime) -> dict[str, dict]:
    """Cached minute wicks for working coupons. A miss must not stall the cycle."""
    now_s = time.time()
    out: dict[str, dict] = {}
    for ticker in tickers:
        hit = _EXTREME_CACHE.get(ticker)
        if hit and hit[0] > now_s:
            out[ticker] = hit[1]
            continue
        data = market_minute_extremes(client, ticker, now)
        _EXTREME_CACHE[ticker] = (now_s + _EXTREME_TTL, data)
        out[ticker] = data
    return out


def refresh_working(
    store: Store,
    settings: Settings,
    markets: list[Market],
    spot: SpotQuote,
    now: datetime | None = None,
    can_trade: bool = True,
    *,
    extremes: dict | None = None,
    tapes: dict | None = None,
    client: KalshiClient | None = None,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    by_ticker = {item.ticker: item for item in markets}
    if extremes is None and client is not None:
        tickers = [
            row["ticker"]
            for row in store.working_trades()
            if _row_play(row) == "impulse_wait"
        ]
        extremes = working_extremes(client, tickers, now) if tickers else {}
    extremes = extremes or {}
    updates = []
    for row in store.working_trades():
        market = by_ticker.get(row["ticker"])
        play = _row_play(row)
        if (
            settings.playbook == "flex"
            and play in {"lock_wait", "impulse_wait"}
            and (market is None or not is_next_session_book(market, now))
        ):
            _cancel_working(store, row, "wait_invalid", client)
            updates.append(
                {"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": "wait_invalid"}
            )
            continue
        if market is None or market.strike is None:
            continue
        if settings.live and not can_trade:
            continue
        rest = float(row["price"])
        ask = market.yes_ask_effective if row["side"] == "yes" else market.no_ask_effective
        seconds = _seconds_left(market.close_time, now)
        if play == "impulse_wait":
            if _is_live_one(row) and client is not None:
                order = _find_live_order(client, row)
                raw = _row_raw(row)
                if order:
                    fill = order_fill_count(order)
                    raw["live_status"] = order.get("status")
                    raw["live_fill"] = fill
                    store.update_raw(row["id"], raw)
                    status = str(order.get("status") or "").lower()
                    if can_trade and fill > 0:
                        fill_count = min(float(row["count"]), fill)
                        filled = fill_cost(rest, fill_count, taker=False)
                        store.promote_working(
                            row["id"],
                            rest,
                            filled.fee,
                            filled.cost,
                            filled.if_win_roi,
                            taker=False,
                            count=fill_count,
                        )
                        raw["filled_at"] = now.isoformat()
                        raw["filled_count"] = fill_count
                        store.update_raw(row["id"], raw)
                        updates.append(
                            {
                                "id": row["id"],
                                "ticker": row["ticker"],
                                "status": "open",
                                "price": rest,
                                "count": fill_count,
                                "reason": "live_fill",
                            }
                        )
                        continue
                    if status in {"canceled", "cancelled", "expired"} and fill <= 0:
                        store.cancel_trade(row["id"], "wait_invalid")
                        updates.append(
                            {
                                "id": row["id"],
                                "ticker": row["ticker"],
                                "status": "cancelled",
                                "reason": "wait_invalid",
                            }
                        )
                        continue
                if can_trade and ask is not None and ask < rest - TICK - 1e-12:
                    _cancel_working(store, row, "wait_through", client)
                    updates.append(
                        {"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": "wait_through"}
                    )
                    continue
                reason = _wait_cancel_reason(row, spot.impulse, settings, seconds)
                if reason:
                    _cancel_working(store, row, reason, client)
                    updates.append(
                        {"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": reason}
                    )
                continue
            wick = extremes.get(row["ticker"]) or {}
            if can_trade and wait_book_crossed(
                row["side"],
                rest,
                ask,
                yes_bid_high=wick.get("yes_bid_high"),
                yes_ask_low=wick.get("yes_ask_low"),
                impulse=spot.impulse,
                min_impulse=settings.impulse_min if play == "impulse_wait" else None,
            ):
                raw = {}
                try:
                    raw = json.loads(row["raw"] or "{}")
                except Exception:
                    raw = {}
                tape_size = None
                if not settings.live:
                    tape_size = paper_rest_tape(row, rest, client=client, tapes=tapes)
                    if tape_size is not None:
                        raw["tape_at_rest"] = tape_size
                if tape_size is not None and tape_size <= 0:
                    store.update_raw(row["id"], raw)
                    # Quote/wick touched rest, but nobody printed. Stay working.
                else:
                    fill_count = float(row["count"])
                    if tape_size is not None:
                        fill_count = min(fill_count, tape_size)
                    filled = fill_cost(rest, fill_count, taker=False)
                    store.promote_working(
                        row["id"],
                        rest,
                        filled.fee,
                        filled.cost,
                        filled.if_win_roi,
                        taker=False,
                        count=fill_count,
                    )
                    raw["filled_at"] = now.isoformat()
                    raw["filled_count"] = fill_count
                    store.update_raw(row["id"], raw)
                    updates.append(
                        {
                            "id": row["id"],
                            "ticker": row["ticker"],
                            "status": "open",
                            "price": rest,
                            "count": fill_count,
                            "reason": "wait_crossed",
                        }
                    )
                    continue
            if can_trade and ask is not None and ask < rest - TICK - 1e-12:
                _cancel_working(store, row, "wait_through", client)
                updates.append(
                    {"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": "wait_through"}
                )
                continue
            reason = _wait_cancel_reason(row, spot.impulse, settings, seconds)
            if reason:
                _cancel_working(store, row, reason, client)
                updates.append(
                    {"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": reason}
                )
            continue
        if can_trade and ask is not None and ask <= rest + 1e-12:
            filled = fill_cost(ask, float(row["count"]), taker=True)
            store.promote_working(row["id"], ask, filled.fee, filled.cost, filled.if_win_roi)
            updates.append({"id": row["id"], "ticker": row["ticker"], "status": "open", "price": ask, "reason": "wait_crossed"})
            continue
        vol = effective_vol(spot.annual_vol, settings.annual_vol)
        p_yes = digital_prob(spot.price, market.strike, max(seconds, 1.0), vol,
                             minute=hour_minute(market, seconds))
        model_p = p_yes if row["side"] == "yes" else 1.0 - p_yes
        sigma = sigma_cushion(spot.price, market.strike, max(seconds, 1.0), vol,
                              minute=hour_minute(market, seconds))
        if model_p + 1e-12 < settings.lock_min_p or sigma + 1e-12 < settings.min_sigma or seconds < 8:
            _cancel_working(store, row, "wait_invalid", client)
            updates.append({"id": row["id"], "ticker": row["ticker"], "status": "cancelled", "reason": "wait_invalid"})
    return updates


def manage_open(
    client: KalshiClient,
    store: Store,
    settings: Settings,
    markets: list[Market],
    spot: SpotQuote,
    now: datetime | None = None,
    can_trade: bool = True,
) -> list[dict]:
    if not settings.allow_early_exit or not can_trade:
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
        p_yes = digital_prob(spot.price, market.strike, max(seconds, 1.0), vol,
                             minute=hour_minute(market, seconds))
        model_p = p_yes if row["side"] == "yes" else 1.0 - p_yes
        raw = {}
        try:
            raw = json.loads(row["raw"] or "{}")
        except Exception:
            raw = {}
        filled_raw = raw.get("filled_at") or row["created_at"]
        held = None
        if filled_raw:
            filled_at = datetime.fromisoformat(str(filled_raw).replace("Z", "+00:00"))
            held = (now - filled_at).total_seconds()
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
                held_seconds=held,
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
            updates.append(
                _close_position(
                    row, decision.action, client, settings, store, market=market
                )
            )
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


def _entries_after_exits(store: Store, opps: list, event_ticker: str | None) -> list:
    """Re-filter scan rows after manage_open so a coupon clip cannot hop."""
    session = refresh_session(store.session_memory(), event_ticker)
    rows = []
    for item in opps:
        rows.append(item if isinstance(item, Opportunity) else Opportunity(**item))
    filtered = apply_swing_memory(rows, store.swing_memories(), session)
    working_plays = {_row_play(row) for row in store.working_trades()}
    return pick_flex_entries(filtered, working_plays=working_plays)


def run_cycle(client: KalshiClient | None = None, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    client = client or make_client(settings)
    store = Store()
    exchange = read_exchange_status(client)
    can_trade = bool(exchange.get("can_trade"))
    reconciled = []
    if settings.live_one and settings.can_sign:
        cancel_stale_live_rests(client)
        clear_paper_bulk_waits(store)
        reconciled = reconcile_live_one(client, store, settings, can_trade=can_trade)
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
    waits = refresh_working(
        store, settings, markets, spot, can_trade=can_trade, client=client
    )
    exits = manage_open(client, store, settings, markets, spot, can_trade=can_trade)
    taken = []
    event_ticker = (scan.get("event") or {}).get("event_ticker")
    if can_trade and not store.open_trades():
        # Scan ran before fills/exits. A same-cycle coupon clip must stay
        # dead — AUG2618 T78699 t_clip then hopped the same ticker 34s later.
        chosen = _entries_after_exits(store, scan.get("opportunities") or [], event_ticker)
        for item in chosen:
            taken.append(
                _execute(
                    item if isinstance(item, Opportunity) else Opportunity(**item),
                    client,
                    settings,
                    store,
                    can_trade=can_trade,
                )
            )
            if store.open_trades():
                break
    scan.pop("snapshot", None)
    return {
        "mode": settings.mode,
        "playbook": settings.playbook,
        "exchange": exchange,
        "scan": scan,
        "taken": taken,
        "waits": waits,
        "exits": exits,
        "settlements": settlements,
        "reconciled": reconciled,
        "summary": store.summary(),
    }


def _bounded_cycle(client: KalshiClient, settings: Settings, seconds: int = CYCLE_TIMEOUT_SECONDS) -> dict:
    """Run one cycle with a hard deadline. urllib can hang past its own timeout."""
    box: dict = {}

    def worker() -> None:
        try:
            box["cycle"] = run_cycle(client, settings)
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True, name="btchour-cycle")
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"run_cycle exceeded {seconds}s")
    if "error" in box:
        raise box["error"]
    return box["cycle"]


def run_loop(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    client = make_client(settings)
    while True:
        try:
            cycle = _bounded_cycle(client, settings)
        except Exception as exc:
            print(f"{format_et()} loop_error {exc}", flush=True)
            client = make_client(settings)
            time.sleep(max(2, settings.poll_seconds))
            continue
        event = (cycle["scan"].get("event") or {}).get("event_ticker")
        n = len(cycle["scan"]["opportunities"])
        spot = cycle["scan"]["spot"]
        diagnosis = cycle["scan"].get("diagnosis") or {}
        exchange = cycle.get("exchange") or {}
        print(
            f"{format_et()} mode={settings.mode} playbook={settings.playbook} "
            f"trading={1 if exchange.get('can_trade') else 0} "
            f"event={event} spot={spot['price']:.2f} impulse={float(spot.get('impulse') or 0):+.0f} "
            f"tape={float(spot.get('tape_impulse') or 0):+.0f} diag={diagnosis.get('status')} "
            f"opps={n} taken={len(cycle['taken'])} exits={len(cycle['exits'])} "
            f"settled_now={len(cycle['settlements'])} pnl={cycle['summary']['realized_pnl']:.4f}",
            flush=True,
        )
        if not exchange.get("can_trade"):
            print(
                f"  exchange_hold active={exchange.get('exchange_active')} "
                f"trading={exchange.get('trading_active')} "
                f"index={exchange.get('description')} "
                f"{exchange.get('error') or exchange.get('resume_time') or ''}",
                flush=True,
            )
        for opp in cycle["scan"]["opportunities"][:5]:
            print(f"  {opp['reason']}", flush=True)
        if diagnosis.get("status") == "wait":
            print(f"  {journal_line(diagnosis)}", flush=True)
        if diagnosis.get("status") == "blocked":
            for row in (diagnosis.get("candidates") or [])[:2]:
                print(f"  reject {row.get('side')} {row.get('ticker')} ask={row.get('ask')} p={row.get('p')} {row.get('reasons')}", flush=True)
        for item in cycle["exits"]:
            print(f"  exit {item.get('ticker')} {item.get('result')} pnl={item.get('pnl')}", flush=True)
        for item in cycle.get("reconciled") or []:
            print(f"  reconcile {item.get('ticker')} {item.get('result') or item.get('reason')}", flush=True)
        time.sleep(max(2, settings.poll_seconds))


def supervise_run(settings: Settings | None = None) -> None:
    """Keep `btchour run` alive. Restart if scans stall or the child dies."""
    settings = settings or load_settings()
    cmd = [sys.executable, "-m", "btchour", "run", "--playbook", settings.playbook]
    while True:
        print(
            f"{format_et()} supervisor start {' '.join(cmd)} "
            f"stall>{STALL_SECONDS}s",
            flush=True,
        )
        proc = subprocess.Popen(cmd, cwd=str(ROOT))
        while proc.poll() is None:
            time.sleep(10)
            try:
                age = Store().scan_age_seconds()
            except Exception as exc:
                print(f"{format_et()} supervisor store_error {exc}", flush=True)
                continue
            if age is not None and age > STALL_SECONDS:
                print(
                    f"{format_et()} supervisor stall age={age:.0f}s, restarting",
                    flush=True,
                )
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except Exception:
                    proc.kill()
                    proc.wait(timeout=5)
                break
        print(
            f"{format_et()} supervisor child exit {proc.returncode}, restart in 2s",
            flush=True,
        )
        time.sleep(2)

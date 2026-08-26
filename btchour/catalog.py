from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from btchour.config import CATALOG_DIR, DATA_DIR, Settings
from btchour.kalshi import KalshiClient, Market
from btchour.spot import fetch_spot
from btchour.tickers import is_hourly_window, next_session_event_ticker, parse_event_ticker


RELATED_SERIES = ("KXBTCD", "KXBTC", "KXBTC15M")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _compact_market(market: Market) -> dict:
    return {
        "ticker": market.ticker,
        "event_ticker": market.event_ticker,
        "subtitle": market.subtitle,
        "strike": market.strike,
        "strike_type": market.strike_type,
        "status": market.status,
        "yes_bid": market.yes_bid,
        "yes_ask": market.yes_ask,
        "no_bid": market.no_bid,
        "no_ask": market.no_ask,
        "last": market.last,
        "volume": market.volume,
        "open_interest": market.open_interest,
        "open_time": market.open_time,
        "close_time": market.close_time,
        "result": market.result,
    }


def _event_summary(event: dict) -> dict:
    parsed = None
    ticker = event.get("event_ticker") or ""
    try:
        parsed = parse_event_ticker(ticker)
    except ValueError:
        pass
    return {
        "event_ticker": ticker,
        "title": event.get("title"),
        "sub_title": event.get("sub_title"),
        "strike_date": event.get("strike_date"),
        "status": event.get("status"),
        "cadence": (event.get("product_metadata") or {}).get("cadence"),
        "close_et": parsed["close_et"].isoformat() if parsed else None,
    }


def current_hourly_events(events: list[dict], now: datetime | None = None) -> list[dict]:
    """Prefer the next whole-hour close. 16:13 ET → the 17:00 ET book.

    Kalshi may tag that 5pm print `cadence=daily` and keep a just-closed
    event `open` through TWAP. Neither changes which book we trade.
    """
    now = now or datetime.now(timezone.utc)
    target = next_session_event_ticker(now)
    live: list[tuple[datetime, dict]] = []
    closed: list[tuple[datetime, dict]] = []
    by_ticker: dict[str, dict] = {}
    for event in events:
        ticker = event.get("event_ticker") or ""
        if ticker:
            by_ticker[ticker] = event
        try:
            parsed = parse_event_ticker(ticker)
        except (KeyError, ValueError):
            continue
        close = parsed["close_utc"]
        if close > now:
            live.append((close, event))
        else:
            closed.append((close, event))
    live.sort(key=lambda row: row[0])
    if target in by_ticker:
        rest = [event for _, event in live if event.get("event_ticker") != target]
        return [by_ticker[target]] + rest
    if live:
        return [event for _, event in live]
    closed.sort(key=lambda row: row[0], reverse=True)
    return [event for _, event in closed]


def _event_from_payload(payload: dict | None, ticker: str) -> dict | None:
    if not payload:
        return None
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    if not isinstance(event, dict):
        return None
    if event.get("event_ticker"):
        return event
    if payload.get("event_ticker"):
        return payload
    return {"event_ticker": ticker, "title": event.get("title"), "status": event.get("status"), "product_metadata": event.get("product_metadata") or {}}


def sync_catalog(client: KalshiClient, settings: Settings, *, light: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    series_docs = {}
    if not light:
        for ticker in RELATED_SERIES:
            series_docs[ticker] = client.series(ticker)
            _write_json(CATALOG_DIR / "series" / f"{ticker}.json", series_docs[ticker])

    open_events = client.events(settings.series_ticker, "open")
    unopened_events = [] if light else client.events(settings.series_ticker, "unopened")
    open_markets = client.markets(settings.series_ticker, "open")
    by_event: dict[str, list[Market]] = {}
    for market in open_markets:
        by_event.setdefault(market.event_ticker, []).append(market)

    target = next_session_event_ticker(now)
    hourly_open = []
    for event in open_events:
        markets = by_event.get(event["event_ticker"], [])
        sample = markets[0] if markets else None
        cadence = (event.get("product_metadata") or {}).get("cadence")
        hourly = cadence == "hourly" if cadence else True
        if sample and cadence is None:
            hourly = is_hourly_window(sample.open_time, sample.close_time)
        if event.get("event_ticker") == target:
            hourly_open.append(event)
            continue
        if settings.hourly_only and not hourly:
            continue
        hourly_open.append(event)

    focus_pool = list(hourly_open or open_events)
    if target not in {event.get("event_ticker") for event in focus_pool}:
        for event in unopened_events:
            if event.get("event_ticker") == target:
                focus_pool.append(event)
                break
    if target not in {event.get("event_ticker") for event in focus_pool}:
        try:
            fetched_event = _event_from_payload(client.get(f"/events/{target}"), target)
            if fetched_event:
                focus_pool.append(fetched_event)
        except Exception:
            pass
    if target not in by_event:
        try:
            fetched_markets = client.markets_by_event(target)
            if fetched_markets:
                by_event[target] = fetched_markets
        except Exception:
            pass

    focus = current_hourly_events(focus_pool, now)
    focus_event = next((event for event in focus_pool if event.get("event_ticker") == target), None)
    if focus_event is None:
        focus_event = focus[0] if focus else None
    focus_markets = by_event.get(focus_event["event_ticker"], []) if focus_event else []
    spot = fetch_spot(client, focus_event["event_ticker"] if focus_event else None)

    tradable = []
    tradable_tickers: set[str] = set()
    for event in open_events:
        cadence = (event.get("product_metadata") or {}).get("cadence")
        ticker = event.get("event_ticker") or ""
        if ticker == target:
            include = True
        elif cadence == "hourly":
            include = True
        elif cadence == "daily":
            include = settings.scan_daily
        elif cadence == "weekly":
            include = settings.scan_weekly
        else:
            include = not settings.hourly_only
        if not include:
            continue
        markets = by_event.get(ticker, [])
        tradable_tickers.add(ticker)
        tradable.append(
            {
                "event": _event_summary(event),
                "market_count": len(markets),
                "markets": [_compact_market(m) for m in sorted(markets, key=lambda m: m.strike or 0)],
            }
        )
    if focus_event and focus_event.get("event_ticker") not in tradable_tickers:
        ticker = focus_event["event_ticker"]
        markets = by_event.get(ticker, [])
        tradable.insert(
            0,
            {
                "event": _event_summary(focus_event),
                "market_count": len(markets),
                "markets": [_compact_market(m) for m in sorted(markets, key=lambda m: m.strike or 0)],
            },
        )
    if settings.scan_15m and not light:
        try:
            for event in client.events("KXBTC15M", "open"):
                markets = client.markets_by_event(event["event_ticker"])
                tradable.append(
                    {
                        "event": _event_summary(event),
                        "market_count": len(markets),
                        "markets": [_compact_market(m) for m in markets],
                    }
                )
        except Exception:
            pass

    snapshot = {
        "synced_at": now.isoformat(),
        "source": "kalshi",
        "base": client.base,
        "spot": {
            "price": spot.price,
            "source": spot.source,
            "twap60": spot.twap60,
            "annual_vol": spot.annual_vol,
            "ts_ms": spot.ts_ms,
            "impulse": spot.impulse,
        },
        "light": light,
        "series": {
            ticker: {
                "ticker": doc.get("ticker"),
                "title": doc.get("title"),
                "frequency": doc.get("frequency"),
                "category": doc.get("category"),
                "tags": doc.get("tags"),
                "fee_type": doc.get("fee_type"),
                "fee_multiplier": doc.get("fee_multiplier"),
                "settlement_sources": doc.get("settlement_sources"),
                "product_metadata": doc.get("product_metadata"),
            }
            for ticker, doc in series_docs.items()
        },
        "open_events": [_event_summary(event) for event in open_events],
        "unopened_hours": [_event_summary(event) for event in unopened_events[:48]],
        "current_hour": {
            "event": _event_summary(focus_event) if focus_event else None,
            "market_count": len(focus_markets),
            "markets": [_compact_market(m) for m in sorted(focus_markets, key=lambda m: m.strike or 0)],
        },
        "tradable": tradable,
        "rules_primary_sample": focus_markets[0].rules_primary if focus_markets else "",
    }

    if not light:
        _write_json(DATA_DIR / "catalog" / "latest.json", snapshot)
        _write_json(CATALOG_DIR / "snapshot" / "latest.json", snapshot)
        _write_json(
            CATALOG_DIR / "snapshot" / "index.json",
            {
                "synced_at": snapshot["synced_at"],
                "spot": snapshot["spot"],
                "open_events": snapshot["open_events"],
                "unopened_hours": snapshot["unopened_hours"],
                "current_hour": snapshot["current_hour"]["event"],
                "current_hour_markets": snapshot["current_hour"]["market_count"],
                "tradable": [block["event"] for block in snapshot.get("tradable") or []],
            },
        )
    return snapshot

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from btchour.config import CATALOG_DIR, DATA_DIR, Settings
from btchour.kalshi import KalshiClient, Market
from btchour.spot import fetch_spot
from btchour.tickers import is_hourly_window, parse_event_ticker


RELATED_SERIES = ("KXBTCD", "KXBTC", "KXBTC15M")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _compact_market(market: Market) -> dict:
    return {
        "ticker": market.ticker,
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
    now = now or datetime.now(timezone.utc)
    ranked = []
    for event in events:
        try:
            parsed = parse_event_ticker(event["event_ticker"])
        except (KeyError, ValueError):
            continue
        ranked.append((abs((parsed["close_utc"] - now).total_seconds()), parsed["close_utc"], event))
    ranked.sort(key=lambda row: row[0])
    return [row[2] for row in ranked]


def sync_catalog(client: KalshiClient, settings: Settings) -> dict:
    now = datetime.now(timezone.utc)
    series_docs = {}
    for ticker in RELATED_SERIES:
        series_docs[ticker] = client.series(ticker)
        _write_json(CATALOG_DIR / "series" / f"{ticker}.json", series_docs[ticker])

    open_events = client.events(settings.series_ticker, "open")
    unopened_events = client.events(settings.series_ticker, "unopened")
    open_markets = client.markets(settings.series_ticker, "open")
    by_event: dict[str, list[Market]] = {}
    for market in open_markets:
        by_event.setdefault(market.event_ticker, []).append(market)

    hourly_open = []
    for event in open_events:
        markets = by_event.get(event["event_ticker"], [])
        sample = markets[0] if markets else None
        cadence = (event.get("product_metadata") or {}).get("cadence")
        hourly = cadence == "hourly" if cadence else True
        if sample and cadence is None:
            hourly = is_hourly_window(sample.open_time, sample.close_time)
        if settings.hourly_only and not hourly:
            continue
        hourly_open.append(event)

    focus = current_hourly_events(hourly_open or open_events, now)
    focus_event = focus[0] if focus else None
    focus_markets = by_event.get(focus_event["event_ticker"], []) if focus_event else []
    spot = fetch_spot(client, focus_event["event_ticker"] if focus_event else None)

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
        },
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
        "rules_primary_sample": focus_markets[0].rules_primary if focus_markets else "",
    }

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
        },
    )
    return snapshot

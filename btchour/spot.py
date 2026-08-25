from __future__ import annotations

import json
import urllib.request
from statistics import mean

from btchour.kalshi import KalshiClient
from btchour.model import SpotQuote, realized_annual_vol


def _last_from_live(payload: dict) -> SpotQuote | None:
    live = payload.get("live_data") or payload
    details = live.get("details") or {}
    series = details.get("timeseries") or []
    price = None
    ts_ms = None
    twap60 = None
    if series:
        last = series[-1]
        price = float(last["v"])
        ts_ms = int(last["t"])
        window = [float(p["v"]) for p in series if ts_ms - int(p["t"]) <= 60_000]
        if window:
            twap60 = mean(window)
    candles = ((details.get("candlesticks") or {}).get("1M")) or []
    if price is None and candles:
        price = float(candles[-1]["close"])
        ts_ms = int(candles[-1]["open_ts_ms"])
    if price is None:
        return None
    closes = [float(c["close"]) for c in candles if c.get("close")]
    vol = realized_annual_vol(closes, 60.0)
    impulse = 0.0
    if series and ts_ms is not None:
        target = ts_ms - 180_000
        nearest = min(series, key=lambda point: abs(int(point["t"]) - target))
        if abs(int(nearest["t"]) - target) <= 90_000:
            impulse = price - float(nearest["v"])
    return SpotQuote(
        price=price,
        source="kalshi_brti_live",
        twap60=twap60,
        annual_vol=vol,
        ts_ms=ts_ms,
        impulse=impulse,
    )


def coinbase_spot(user_agent: str = "BTCHOUR/0.1") -> SpotQuote:
    req = urllib.request.Request(
        "https://api.coinbase.com/v2/prices/BTC-USD/spot",
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode())
    return SpotQuote(price=float(payload["data"]["amount"]), source="coinbase")


def fetch_spot(client: KalshiClient, event_ticker: str | None) -> SpotQuote:
    if event_ticker:
        try:
            quote = _last_from_live(client.live_data(event_ticker, "1h"))
            if quote:
                return quote
        except Exception:
            pass
    return coinbase_spot(client.user_agent)

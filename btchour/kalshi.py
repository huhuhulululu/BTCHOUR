from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class KalshiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    subtitle: str
    status: str
    strike: float | None
    strike_type: str
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    last: float | None
    volume: float | None
    open_interest: float | None
    open_time: str | None
    close_time: str | None
    rules_primary: str
    result: str
    raw: dict

    @property
    def no_ask_effective(self) -> float | None:
        if self.no_ask is not None:
            return self.no_ask
        if self.yes_bid is not None:
            return round(1.0 - self.yes_bid, 4)
        return None

    @property
    def yes_ask_effective(self) -> float | None:
        return self.yes_ask

    @property
    def yes_bid_effective(self) -> float | None:
        return self.yes_bid

    @property
    def no_bid_effective(self) -> float | None:
        if self.no_bid is not None:
            return self.no_bid
        if self.yes_ask is not None:
            return round(1.0 - self.yes_ask, 4)
        return None


def market_from_api(item: dict) -> Market:
    strike = item.get("floor_strike")
    if strike is None:
        strike = item.get("cap_strike")
    return Market(
        ticker=item["ticker"],
        event_ticker=item.get("event_ticker") or "",
        title=item.get("title") or "",
        subtitle=item.get("subtitle") or item.get("yes_sub_title") or "",
        status=item.get("status") or "",
        strike=float(strike) if strike is not None else None,
        strike_type=item.get("strike_type") or "",
        yes_bid=_money(item.get("yes_bid_dollars")),
        yes_ask=_money(item.get("yes_ask_dollars")),
        no_bid=_money(item.get("no_bid_dollars")),
        no_ask=_money(item.get("no_ask_dollars")),
        last=_money(item.get("last_price_dollars")),
        volume=_money(item.get("volume_fp")),
        open_interest=_money(item.get("open_interest_fp")),
        open_time=item.get("open_time"),
        close_time=item.get("close_time"),
        rules_primary=item.get("rules_primary") or "",
        result=(item.get("result") or "").lower(),
        raw=item,
    )


class KalshiClient:
    def __init__(
        self,
        base: str = "https://external-api.kalshi.com/trade-api/v2",
        user_agent: str = "BTCHOUR/0.1",
        api_key_id: str = "",
        private_key_pem: str = "",
        timeout: int = 15,
    ):
        self.base = base.rstrip("/")
        self.user_agent = user_agent
        self.api_key_id = api_key_id
        self.private_key_pem = private_key_pem
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None, signed: bool = False) -> Any:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.base + path + (("?" + query) if query else "")
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if signed:
            headers.update(self._sign_headers("GET", path))
        return self._request("GET", url, headers)

    def post(self, path: str, payload: dict) -> Any:
        url = self.base + path
        body = json.dumps(payload).encode()
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self._sign_headers("POST", path),
        }
        return self._request("POST", url, headers, body)

    def delete(self, path: str) -> Any:
        url = self.base + path
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            **self._sign_headers("DELETE", path),
        }
        return self._request("DELETE", url, headers)

    def _request(self, method: str, url: str, headers: dict, body: bytes | None = None) -> Any:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode() if exc.fp else ""
            raise KalshiError(f"{method} {url} -> {exc.code}: {text[:400]}", exc.code, text) from exc
        except urllib.error.URLError as exc:
            raise KalshiError(f"{method} {url} failed: {exc}") from exc
        except TimeoutError as exc:
            raise KalshiError(f"{method} {url} timed out: {exc}") from exc

    def _sign_headers(self, method: str, path: str) -> dict:
        if not self.api_key_id or not self.private_key_pem:
            raise KalshiError("Kalshi API key and private key are required for signed requests")
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp = str(int(time.time() * 1000))
        sign_path = urlparse(self.base + path).path.split("?")[0]
        message = (timestamp + method.upper() + sign_path).encode()
        key = serialization.load_pem_private_key(self.private_key_pem.encode(), password=None)
        signature = key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        import base64

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    def paginate(
        self,
        path: str,
        list_key: str,
        params: dict | None = None,
        limit: int = 200,
        signed: bool = False,
    ) -> list:
        items: list = []
        cursor = None
        while True:
            query = dict(params or {})
            query["limit"] = limit
            if cursor:
                query["cursor"] = cursor
            payload = self.get(path, query, signed=signed)
            items.extend(payload.get(list_key) or [])
            cursor = payload.get("cursor")
            if not cursor:
                break
        return items

    def series(self, ticker: str) -> dict:
        return self.get(f"/series/{ticker}").get("series") or {}

    def events(self, series_ticker: str, status: str | None = None) -> list[dict]:
        return self.paginate("/events", "events", {"series_ticker": series_ticker, "status": status})

    def markets(self, series_ticker: str, status: str | None = None) -> list[Market]:
        raw = self.paginate("/markets", "markets", {"series_ticker": series_ticker, "status": status})
        return [market_from_api(item) for item in raw]

    def markets_by_event(self, event_ticker: str) -> list[Market]:
        payload = self.get(f"/events/{event_ticker}")
        markets = payload.get("markets") or []
        return [market_from_api(item) for item in markets]

    def live_data(self, event_ticker: str, range_hint: str = "1h") -> dict:
        return self.get(f"/live_data/events/{event_ticker}", {"range": range_hint})

    def exchange_status(self) -> dict:
        return self.get("/exchange/status")

    def create_order(
        self,
        ticker: str,
        side: str,
        price: float,
        count: float,
        time_in_force: str = "immediate_or_cancel",
        client_order_id: str = "",
    ) -> dict:
        payload = {
            "ticker": ticker,
            "side": side,
            "count": f"{count:.2f}".rstrip("0").rstrip(".") if count != int(count) else str(int(count)),
            "price": f"{price:.4f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id
        return self.post("/portfolio/events/orders", payload)

    def balance(self) -> dict:
        return self.get("/portfolio/balance", signed=True)

    def fills(self, min_ts: int | None = None, max_ts: int | None = None, ticker: str | None = None) -> list:
        return self.paginate(
            "/portfolio/fills",
            "fills",
            {"min_ts": min_ts, "max_ts": max_ts, "ticker": ticker},
            signed=True,
        )

    def orders(self, min_ts: int | None = None, status: str | None = None, ticker: str | None = None) -> list:
        return self.paginate(
            "/portfolio/orders",
            "orders",
            {"min_ts": min_ts, "status": status, "ticker": ticker},
            signed=True,
        )

    def positions(self, event_ticker: str | None = None, ticker: str | None = None) -> dict:
        return self.get(
            "/portfolio/positions",
            {"event_ticker": event_ticker, "ticker": ticker, "limit": 200},
            signed=True,
        )

    def settlements(self, min_ts: int | None = None, ticker: str | None = None) -> list:
        return self.paginate(
            "/portfolio/settlements",
            "settlements",
            {"min_ts": min_ts, "ticker": ticker},
            signed=True,
        )

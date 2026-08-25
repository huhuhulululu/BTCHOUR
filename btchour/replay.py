from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from btchour.config import CATALOG_DIR, Settings, load_settings
from btchour.engine import make_client
from btchour.fees import fill_cost
from btchour.kalshi import KalshiClient, market_from_api
from btchour.model import digital_prob, effective_vol, realized_annual_vol
from btchour.paper import paper_settle
from btchour.tickers import format_event_ticker

ET = ZoneInfo("America/New_York")


def _settlement_band(markets) -> tuple[float, float] | None:
    yes = [m.strike for m in markets if m.result == "yes" and m.strike is not None]
    no = [m.strike for m in markets if m.result == "no" and m.strike is not None]
    if not yes or not no:
        return None
    return max(yes), min(no)


def _minute_spot(series: list[dict]) -> dict[int, float]:
    by_min: dict[int, list[float]] = {}
    for point in series:
        minute = (int(point["t"]) // 60_000) * 60_000
        by_min.setdefault(minute, []).append(float(point["v"]))
    return {minute: values[-1] for minute, values in by_min.items()}


def replay_event(client: KalshiClient, event_ticker: str, settings: Settings) -> dict:
    payload = client.get(f"/events/{event_ticker}")
    markets = [market_from_api(item) for item in payload.get("markets") or []]
    band = _settlement_band(markets)
    live = client.live_data(event_ticker, "1h").get("live_data") or {}
    details = live.get("details") or {}
    series = details.get("timeseries") or []
    maturity_ms = int(details.get("maturity_ts_ms") or 0)
    spots = _minute_spot(series)
    if not band or not spots or not maturity_ms:
        return {"event_ticker": event_ticker, "error": "incomplete data", "takes": []}

    lo, hi = band
    settle_price = (lo + hi) / 2
    strikes = [lo, hi]
    # one extra rung each side when present
    extras = sorted({m.strike for m in markets if m.strike is not None})
    for strike in extras:
        if lo - 200 <= strike <= hi + 200 and strike not in strikes:
            strikes.append(strike)

    start = int(maturity_ms / 1000) - 3600
    end = int(maturity_ms / 1000)
    candles: dict[float, dict[int, dict]] = {}
    results = {m.strike: m.result for m in markets if m.strike is not None}
    for strike in strikes:
        ticker = f"{event_ticker}-T{strike}"
        try:
            data = client.get(
                f"/series/KXBTCD/markets/{ticker}/candlesticks",
                {"start_ts": start, "end_ts": end, "period_interval": 1},
            )
        except Exception as exc:
            candles[strike] = {"_error": str(exc)}
            continue
        candles[strike] = {int(row["end_period_ts"]): row for row in data.get("candlesticks") or []}

    minutes = sorted(spots)
    takes = []
    best = None
    for idx, minute_ms in enumerate(minutes):
        spot = spots[minute_ms]
        end_ts = minute_ms // 1000 + 60
        left = maturity_ms / 1000 - end_ts
        if left < 8:
            continue
        window = [spots[k] for k in minutes[max(0, idx - 30) : idx + 1]]
        vol = effective_vol(realized_annual_vol(window, 60.0), settings.annual_vol)
        for strike in strikes:
            stick = candles.get(strike, {}).get(end_ts)
            if not stick:
                continue
            ask_raw = (stick.get("yes_ask") or {}).get("close_dollars")
            if not ask_raw:
                continue
            ask = float(ask_raw)
            if ask <= 0 or ask >= 1:
                continue
            p_yes = digital_prob(spot, strike, left, vol)
            for side, model_p, side_ask in (
                ("yes", p_yes, ask),
                ("no", 1.0 - p_yes, round(1.0 - float((stick.get("yes_bid") or {}).get("close_dollars") or 0), 4)),
            ):
                if side_ask <= 0 or side_ask >= 1:
                    continue
                cost = fill_cost(side_ask, taker=True)
                ev = cost.betting_ev(model_p)
                row = {
                    "ts": datetime.fromtimestamp(end_ts, timezone.utc).isoformat(),
                    "event_ticker": event_ticker,
                    "strike": strike,
                    "side": side,
                    "spot": spot,
                    "seconds_left": left,
                    "ask": side_ask,
                    "model_p": model_p,
                    "if_win_roi": cost.if_win_roi,
                    "ev": ev,
                    "vol": vol,
                }
                if best is None or ev > best["ev"]:
                    best = row
                if (
                    cost.if_win_roi + 1e-12 >= settings.target_profit
                    and model_p + 1e-12 >= settings.min_win_prob
                    and ev + 1e-12 >= settings.min_expected_roi
                ):
                    result = results.get(strike, "")
                    pnl = paper_settle(cost.cost, 1.0, side, result) if result in {"yes", "no"} else None
                    takes.append({**row, "result": result, "pnl": pnl})

    return {
        "event_ticker": event_ticker,
        "settlement_band": [lo, hi],
        "settle_mid": settle_price,
        "candles": {str(k): len(v) if isinstance(v, dict) else 0 for k, v in candles.items()},
        "takes": takes,
        "best": best,
    }


def replay_recent_hours(hours: int = 8, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    client = make_client(settings)
    now_et = datetime.now(ET).replace(minute=0, second=0, microsecond=0)
    events = []
    for i in range(hours):
        close = now_et - timedelta(hours=i)
        events.append(format_event_ticker(close))

    reports = []
    for event_ticker in events:
        try:
            reports.append(replay_event(client, event_ticker, settings))
        except Exception as exc:
            reports.append({"event_ticker": event_ticker, "error": str(exc), "takes": []})

    taken = [take for report in reports for take in report.get("takes") or []]
    wins = [take for take in taken if (take.get("pnl") or 0) > 0]
    summary = {
        "replayed_at": datetime.now(timezone.utc).isoformat(),
        "hours": hours,
        "formula": "EV = p * b - (1 - p)",
        "gates": {
            "target_if_win": settings.target_profit,
            "min_win_prob": settings.min_win_prob,
            "min_ev": settings.min_expected_roi,
        },
        "events": reports,
        "take_count": len(taken),
        "wins": len(wins),
        "realized_pnl": sum((take.get("pnl") or 0) for take in taken),
    }
    path = CATALOG_DIR / "snapshot" / "replay.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from btchour.config import CATALOG_DIR, Settings, load_settings
from btchour.engine import make_client
from btchour.exits import OpenPosition, evaluate_exit
from btchour.fees import fill_cost
from btchour.kalshi import KalshiClient, Market, market_from_api
from btchour.model import SpotQuote, digital_prob, effective_vol, realized_annual_vol
from btchour.paper import paper_close, paper_fill, paper_settle
from btchour.strategy import (
    SessionMemory,
    SwingMemory,
    apply_swing_memory,
    remember_session_exit,
    remember_swing_exit,
    refresh_session,
    scan_markets,
)
from btchour.tickers import format_event_ticker

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ReplayBar:
    end_ts: int
    spot: float
    vol: float
    quotes: dict[float, dict]
    impulse: float = 0.0


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


def _money(stick: dict, key: str, field: str = "close_dollars") -> float | None:
    raw = (stick.get(key) or {}).get(field)
    if raw is None or raw == "":
        return None
    value = float(raw)
    if value <= 0 or value >= 1:
        return None
    return value


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def market_at_bar(
    event_ticker: str,
    strike: float,
    quotes: dict,
    open_ts: float,
    close_ts: float,
    result: str = "",
) -> Market:
    yes_ask = quotes.get("yes_ask")
    yes_bid = quotes.get("yes_bid")
    no_ask = quotes.get("no_ask")
    no_bid = quotes.get("no_bid")
    if no_ask is None and yes_bid is not None:
        no_ask = round(1.0 - yes_bid, 4)
    if no_bid is None and yes_ask is not None:
        no_bid = round(1.0 - yes_ask, 4)
    return market_from_api(
        {
            "ticker": f"{event_ticker}-T{strike}",
            "event_ticker": event_ticker,
            "title": "Bitcoin price",
            "subtitle": f"${strike} or above",
            "status": "active",
            "floor_strike": strike,
            "strike_type": "greater",
            "yes_bid_dollars": yes_bid,
            "yes_ask_dollars": yes_ask,
            "no_bid_dollars": no_bid,
            "no_ask_dollars": no_ask,
            "open_time": _iso(open_ts),
            "close_time": _iso(close_ts),
            "result": result,
        }
    )


def replay_bars(
    event_ticker: str,
    bars: list[ReplayBar],
    results: dict[float, str],
    maturity_s: float,
    settings: Settings,
    session: SessionMemory | None = None,
) -> dict:
    open_ts = maturity_s - 3600
    position = None
    takes: list[dict] = []
    best = None
    hold_candidates = 0
    swing_mem = SwingMemory()
    session = refresh_session(session, event_ticker) if settings.skip_after_loss else SessionMemory()
    for bar in bars:
        left = maturity_s - bar.end_ts
        now = datetime.fromtimestamp(bar.end_ts, timezone.utc)
        spot = SpotQuote(bar.spot, "replay", annual_vol=bar.vol, impulse=bar.impulse)
        markets = [
            market_at_bar(event_ticker, strike, quotes, open_ts, maturity_s, results.get(strike, ""))
            for strike, quotes in bar.quotes.items()
        ]
        for market in markets:
            ask = market.yes_ask_effective
            if ask is None:
                continue
            p_yes = digital_prob(bar.spot, market.strike, max(left, 1.0), bar.vol)
            cost = fill_cost(ask, taker=True)
            ev = cost.betting_ev(p_yes)
            row = {
                "ts": now.isoformat(),
                "event_ticker": event_ticker,
                "strike": market.strike,
                "side": "yes",
                "spot": bar.spot,
                "seconds_left": left,
                "ask": ask,
                "model_p": p_yes,
                "if_win_roi": cost.if_win_roi,
                "ev": ev,
                "vol": bar.vol,
            }
            if best is None or ev > best["ev"]:
                best = row
            if (
                cost.if_win_roi + 1e-12 >= settings.target_profit
                and p_yes + 1e-12 >= settings.min_win_prob
                and ev + 1e-12 >= settings.min_expected_roi
            ):
                hold_candidates += 1

        just_closed = None
        if position is not None:
            market = next((item for item in markets if item.ticker == position["ticker"]), None)
            if market is not None and market.strike is not None:
                p_yes = digital_prob(bar.spot, market.strike, max(left, 1.0), bar.vol)
                model_p = p_yes if position["side"] == "yes" else 1.0 - p_yes
                action = None
                decision = evaluate_exit(
                    OpenPosition(
                        ticker=position["ticker"],
                        event_ticker=event_ticker,
                        side=position["side"],
                        cost=position["cost"],
                        count=position["count"],
                        peak_bid=position.get("peak_bid"),
                        play=(position.get("entry") or {}).get("play") or "",
                        entry_p=position.get("model_p") or (position.get("entry") or {}).get("model_p"),
                    ),
                    market,
                    model_p,
                    left,
                    settings,
                )
                position["peak_bid"] = decision.peak_bid
                action = decision.action
                if action:
                    closed = paper_close(position, action.price, action.reason)
                    takes.append(
                        {
                            **position["entry"],
                            "exit_ts": now.isoformat(),
                            "exit_reason": action.reason,
                            "exit_price": action.price,
                            "exit_note": action.note,
                            "pnl": closed["pnl"],
                            "roi": closed["roi"],
                            "result": action.reason,
                        }
                    )
                    just_closed = (position["ticker"], position["side"])
                    play = (position.get("entry") or {}).get("play") or ""
                    if play in {"swing_t", "impulse_t"}:
                        swing_mem = remember_swing_exit(
                            swing_mem, position["ticker"], position["side"], action.reason, play
                        )
                        if settings.skip_after_loss:
                            session = remember_session_exit(
                                session, event_ticker, action.reason, closed["pnl"]
                            )
                    position = None

        if position is None:
            opps = [
                item
                for item in apply_swing_memory(
                    scan_markets(markets, spot, settings, now), swing_mem, session
                )
                if (item.ticker, item.side) != just_closed
            ]
            if opps:
                fill = paper_fill(opps[0])
                if fill.get("status") == "open":
                    position = {
                        **fill,
                        "entry": {
                            "ts": now.isoformat(),
                            "event_ticker": event_ticker,
                            "ticker": opps[0].ticker,
                            "strike": opps[0].strike,
                            "side": opps[0].side,
                            "spot": bar.spot,
                            "seconds_left": left,
                            "ask": opps[0].ask,
                            "model_p": opps[0].model_p,
                            "if_win_roi": opps[0].if_win_roi,
                            "ev": opps[0].ev,
                            "vol": bar.vol,
                            "play": opps[0].play,
                            "lock_price": opps[0].lock_price,
                        },
                    }

    if position is not None:
        result = results.get(position["entry"]["strike"], "")
        pnl = paper_settle(position["cost"], position["count"], position["side"], result) if result in {"yes", "no"} else None
        takes.append(
            {
                **position["entry"],
                "exit_reason": "settle",
                "exit_price": 1.0 if (position["side"] == result) else 0.0,
                "pnl": pnl,
                "roi": (pnl / position["cost"]) if pnl is not None and position["cost"] else None,
                "result": result,
            }
        )

    return {
        "event_ticker": event_ticker,
        "takes": takes,
        "best": best,
        "hold_candidates": hold_candidates,
        "session": session,
    }


def replay_event(
    client: KalshiClient,
    event_ticker: str,
    settings: Settings,
    session: SessionMemory | None = None,
) -> dict:
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
    spot_lo = min(spots.values())
    spot_hi = max(spots.values())
    path_lo = min(spot_lo, lo) - 700
    path_hi = max(spot_hi, hi) + 700
    strikes = sorted(
        {
            m.strike
            for m in markets
            if m.strike is not None and (path_lo <= m.strike <= path_hi or m.strike in {lo, hi})
        }
    )
    if len(strikes) > 24:
        mid = (spot_lo + spot_hi) / 2
        strikes = sorted(strikes, key=lambda strike: min(abs(strike - lo), abs(strike - hi), abs(strike - mid)))[:24]

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
    bars: list[ReplayBar] = []
    for idx, minute_ms in enumerate(minutes):
        end_ts = minute_ms // 1000 + 60
        left = maturity_ms / 1000 - end_ts
        if left < 8:
            continue
        window = [spots[k] for k in minutes[max(0, idx - 30) : idx + 1]]
        vol = effective_vol(realized_annual_vol(window, 60.0), settings.annual_vol)
        lookback = spots[minutes[max(0, idx - 3)]]
        impulse = spots[minute_ms] - lookback
        quotes: dict[float, dict] = {}
        for strike in strikes:
            stick = candles.get(strike, {}).get(end_ts)
            if not stick:
                continue
            ask_field = "low_dollars" if settings.playbook == "lock" else "close_dollars"
            quotes[strike] = {
                "yes_ask": _money(stick, "yes_ask", ask_field),
                "yes_bid": _money(stick, "yes_bid"),
            }
        if not quotes:
            continue
        bars.append(
            ReplayBar(end_ts=end_ts, spot=spots[minute_ms], vol=vol, quotes=quotes, impulse=impulse)
        )

    played = replay_bars(event_ticker, bars, results, maturity_ms / 1000, settings, session)
    return {
        "event_ticker": event_ticker,
        "settlement_band": [lo, hi],
        "settle_mid": settle_price,
        "candles": {str(k): len(v) if isinstance(v, dict) else 0 for k, v in candles.items()},
        "playbook": settings.playbook,
        "takes": played["takes"],
        "best": played["best"],
        "hold_candidates": played["hold_candidates"],
        "session": played.get("session"),
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
    session = SessionMemory()
    for event_ticker in reversed(events):
        try:
            report = replay_event(client, event_ticker, settings, session)
            session = report.get("session") or session
            reports.append(report)
        except Exception as exc:
            reports.append({"event_ticker": event_ticker, "error": str(exc), "takes": []})
    reports.reverse()
    for report in reports:
        mem = report.pop("session", None)
        if mem is not None and hasattr(mem, "last_loss_event"):
            report["session"] = {
                "last_loss_event": mem.last_loss_event,
                "skip_next": mem.skip_next,
                "skipped_event": mem.skipped_event,
            }

    taken = [take for report in reports for take in report.get("takes") or []]
    wins = [take for take in taken if (take.get("pnl") or 0) > 0]
    reasons: dict[str, int] = {}
    for take in taken:
        key = take.get("exit_reason") or "unknown"
        reasons[key] = reasons.get(key, 0) + 1
    summary = {
        "replayed_at": datetime.now(timezone.utc).isoformat(),
        "hours": hours,
        "playbook": settings.playbook,
        "formula": "EV = p * b - (1 - p)",
        "gates": {
            "target_if_win": settings.target_profit,
            "min_win_prob": settings.min_win_prob,
            "min_ev": settings.min_expected_roi,
            "scalp_min_p": settings.scalp_min_p,
            "scalp_min_gap": settings.scalp_min_gap,
            "scalp_max_entry": settings.scalp_max_entry,
            "scalp_min_seconds": settings.scalp_min_seconds,
            "scalp_max_lock": settings.scalp_max_lock,
            "swing_min_p": settings.swing_min_p,
            "swing_min_gap": settings.swing_min_gap,
            "swing_min_ask": settings.swing_min_ask,
            "swing_max_ask": settings.swing_max_ask,
            "swing_target": settings.swing_target,
            "swing_max_clip": settings.swing_max_clip,
            "swing_trail": settings.swing_trail,
            "swing_fade": settings.swing_fade,
            "skip_after_loss": settings.skip_after_loss,
            "lock_min_p": settings.lock_min_p,
            "min_sigma": settings.min_sigma,
            "invalidate_p": settings.invalidate_p,
            "flatten_seconds": settings.flatten_seconds,
            "allow_early_exit": settings.allow_early_exit,
        },
        "events": reports,
        "take_count": len(taken),
        "wins": len(wins),
        "exit_reasons": reasons,
        "realized_pnl": sum((take.get("pnl") or 0) for take in taken),
    }
    path = CATALOG_DIR / "snapshot" / "replay.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary

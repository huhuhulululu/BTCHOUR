from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from btchour.config import CATALOG_DIR, DATA_DIR, Settings, load_settings
from btchour.engine import make_client
from btchour.exits import OpenPosition, evaluate_exit
from btchour.fees import fill_cost
from btchour.kalshi import KalshiClient, Market, market_from_api
from btchour.model import SpotQuote, digital_prob, effective_vol, realized_annual_vol
from btchour.paper import paper_close, paper_fill, paper_settle
from btchour.strategy import (
    T_PLAYS,
    SessionMemory,
    SwingMemory,
    apply_swing_memory,
    impulse_wait_flipped,
    remember_session_exit,
    remember_swing_exit,
    refresh_session,
    pick_flex_entries,
    scan_markets,
    wait_book_crossed,
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


def _position_from_fill(fill: dict, opp, now: datetime, event_ticker: str, bar: ReplayBar, left: float) -> dict:
    ask = fill["price"]
    return {
        **fill,
        "entry": {
            "ts": now.isoformat(),
            "event_ticker": event_ticker,
            "ticker": opp.ticker,
            "strike": opp.strike,
            "side": opp.side,
            "spot": bar.spot,
            "seconds_left": left,
            "ask": ask,
            "model_p": opp.model_p,
            "if_win_roi": fill.get("if_win_roi", opp.if_win_roi),
            "ev": opp.ev,
            "vol": bar.vol,
            "play": opp.play,
            "lock_price": opp.lock_price,
        },
    }


def _promote_wait(working: dict) -> dict:
    rest = float(working["price"])
    filled = fill_cost(rest, float(working["count"]), taker=False)
    promoted = dict(working)
    promoted["status"] = "open"
    promoted["taker"] = False
    promoted["price"] = rest
    promoted["fee"] = filled.fee
    promoted["cost"] = filled.cost
    promoted["if_win_roi"] = filled.if_win_roi
    entry = dict(promoted.get("entry") or {})
    entry["ask"] = rest
    entry["if_win_roi"] = filled.if_win_roi
    promoted["entry"] = entry
    return promoted


def _held_seconds(position: dict, now: datetime) -> float | None:
    raw = (position.get("entry") or {}).get("filled_ts") or (position.get("entry") or {}).get("ts")
    if not raw:
        return None
    filled = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return (now - filled).total_seconds()


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
    working = None
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
                        held_seconds=_held_seconds(position, now),
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
                    if play in T_PLAYS or play.startswith("lock"):
                        swing_mem = remember_swing_exit(
                            swing_mem, position["ticker"], position["side"], action.reason, play
                        )
                    if play in T_PLAYS and settings.skip_after_loss:
                        session = remember_session_exit(
                            session, event_ticker, action.reason, closed["pnl"], position["side"]
                        )
                    position = None

        if working is not None and position is None:
            market = next((item for item in markets if item.ticker == working["ticker"]), None)
            play = (working.get("entry") or {}).get("play") or working.get("play") or ""
            rest = float(working["price"])
            side = working["side"]
            ask = None
            if market is not None:
                ask = market.yes_ask_effective if side == "yes" else market.no_ask_effective
            quotes = {}
            strike = (working.get("entry") or {}).get("strike")
            if strike is not None:
                quotes = bar.quotes.get(float(strike)) or {}
            if wait_book_crossed(
                side,
                rest,
                ask,
                yes_bid_high=quotes.get("yes_bid_high"),
                yes_ask_low=quotes.get("yes_ask_low"),
                impulse=bar.impulse if play == "impulse_wait" else None,
                min_impulse=settings.impulse_min if play == "impulse_wait" else None,
            ):
                position = _promote_wait(working)
                entry = dict(position.get("entry") or {})
                entry["filled_ts"] = now.isoformat()
                position["entry"] = entry
                working = None
                continue
            if play == "impulse_wait" and (
                impulse_wait_flipped(side, bar.impulse, settings) or left + 1e-12 < settings.swing_min_seconds
            ):
                working = None
            elif play != "impulse_wait" and left + 1e-12 < settings.swing_min_seconds:
                working = None

        if position is None:
            opps = [
                item
                for item in apply_swing_memory(
                    scan_markets(markets, spot, settings, now), swing_mem, session
                )
                if (item.ticker, item.side) != just_closed
            ]
            working_plays = set()
            if working is not None:
                working_plays.add((working.get("entry") or {}).get("play") or working.get("play") or "")
            chosen = pick_flex_entries(opps, working_plays=working_plays)
            if working is not None:
                if chosen and chosen[0].play == "lock_hold" and chosen[0].taker:
                    working = None
                    fill = paper_fill(chosen[0])
                    if fill.get("status") == "open":
                        position = _position_from_fill(fill, chosen[0], now, event_ticker, bar, left)
            elif chosen:
                fill = paper_fill(chosen[0])
                if fill.get("status") == "open":
                    position = _position_from_fill(fill, chosen[0], now, event_ticker, bar, left)
                elif fill.get("play") == "impulse_wait":
                    working = _position_from_fill(fill, chosen[0], now, event_ticker, bar, left)
                    quotes = bar.quotes.get(float(chosen[0].strike)) or {}
                    ask = chosen[0].ask
                    if wait_book_crossed(
                        working["side"],
                        float(working["price"]),
                        ask,
                        yes_bid_high=quotes.get("yes_bid_high"),
                        yes_ask_low=quotes.get("yes_ask_low"),
                        impulse=bar.impulse,
                        min_impulse=settings.impulse_min,
                    ):
                        position = _promote_wait(working)
                        entry = dict(position.get("entry") or {})
                        entry["filled_ts"] = now.isoformat()
                        position["entry"] = entry
                        working = None

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


@dataclass
class EventTape:
    """One hourly event's spot path + candlesticks. Replay many playbooks without refetching."""

    event_ticker: str
    spots: dict[int, float]
    candles: dict[float, dict]
    results: dict[float, str]
    maturity_ms: int
    band: tuple[float, float] | None
    error: str | None = None

    def to_dict(self) -> dict:
        candles = {}
        for strike, sticks in self.candles.items():
            if isinstance(sticks, dict) and set(sticks) == {"_error"}:
                candles[str(strike)] = sticks
            else:
                candles[str(strike)] = {str(ts): stick for ts, stick in sticks.items()}
        return {
            "event_ticker": self.event_ticker,
            "spots": {str(k): v for k, v in self.spots.items()},
            "candles": candles,
            "results": {str(k): v for k, v in self.results.items()},
            "maturity_ms": self.maturity_ms,
            "band": list(self.band) if self.band else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "EventTape":
        candles: dict[float, dict] = {}
        for strike_s, sticks in (raw.get("candles") or {}).items():
            if isinstance(sticks, dict) and set(sticks) == {"_error"}:
                candles[float(strike_s)] = sticks
                continue
            loaded: dict = {}
            for key, stick in (sticks or {}).items():
                if key == "_error":
                    loaded["_error"] = stick
                else:
                    loaded[int(key)] = stick
            candles[float(strike_s)] = loaded
        band = raw.get("band")
        return cls(
            event_ticker=str(raw.get("event_ticker") or ""),
            spots={int(k): float(v) for k, v in (raw.get("spots") or {}).items()},
            candles=candles,
            results={float(k): str(v) for k, v in (raw.get("results") or {}).items()},
            maturity_ms=int(raw.get("maturity_ms") or 0),
            band=(float(band[0]), float(band[1])) if band else None,
            error=raw.get("error"),
        )


def tape_cache_path(event_ticker: str):
    return DATA_DIR / "replay-cache" / f"{event_ticker}.json"


def recent_event_tickers(hours: int, now: datetime | None = None) -> list[str]:
    now_et = (now or datetime.now(ET)).astimezone(ET).replace(minute=0, second=0, microsecond=0)
    return [format_event_ticker(now_et - timedelta(hours=i)) for i in range(hours)]


def fetch_event_tape(client: KalshiClient, event_ticker: str, settings: Settings) -> EventTape:
    payload = client.get(f"/events/{event_ticker}")
    markets = [market_from_api(item) for item in payload.get("markets") or []]
    band = _settlement_band(markets)
    live = client.live_data(event_ticker, "1h").get("live_data") or {}
    details = live.get("details") or {}
    series = details.get("timeseries") or []
    maturity_ms = int(details.get("maturity_ts_ms") or 0)
    spots = _minute_spot(series)
    if not band or not spots or not maturity_ms:
        return EventTape(event_ticker, {}, {}, {}, 0, None, error="incomplete data")

    lo, hi = band
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
    candles: dict[float, dict] = {}
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
    return EventTape(event_ticker, spots, candles, results, maturity_ms, band)


def load_event_tape(
    client: KalshiClient,
    event_ticker: str,
    settings: Settings,
    *,
    refresh: bool = False,
) -> EventTape:
    path = tape_cache_path(event_ticker)
    if not refresh and path.is_file():
        try:
            tape = EventTape.from_dict(json.loads(path.read_text()))
            if tape.error is None and tape.spots and tape.maturity_ms:
                return tape
        except Exception:
            pass
    tape = fetch_event_tape(client, event_ticker, settings)
    if tape.error is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tape.to_dict()) + "\n")
    return tape


def load_recent_tapes(hours: int, settings: Settings, client: KalshiClient | None = None) -> list[EventTape]:
    client = client or make_client(settings)
    tapes = []
    for index, event_ticker in enumerate(recent_event_tickers(hours)):
        tapes.append(load_event_tape(client, event_ticker, settings, refresh=index == 0))
    return tapes


def bars_from_tape(tape: EventTape, settings: Settings) -> list[ReplayBar]:
    if tape.error or not tape.band or not tape.spots or not tape.maturity_ms:
        return []
    minutes = sorted(tape.spots)
    bars: list[ReplayBar] = []
    ask_field = "low_dollars" if settings.playbook == "lock" else "close_dollars"
    for idx, minute_ms in enumerate(minutes):
        end_ts = minute_ms // 1000 + 60
        left = tape.maturity_ms / 1000 - end_ts
        if left < 8:
            continue
        window = [tape.spots[k] for k in minutes[max(0, idx - 30) : idx + 1]]
        vol = effective_vol(realized_annual_vol(window, 60.0), settings.annual_vol)
        lookback = tape.spots[minutes[max(0, idx - 3)]]
        impulse = tape.spots[minute_ms] - lookback
        quotes: dict[float, dict] = {}
        for strike, sticks in tape.candles.items():
            if not isinstance(sticks, dict):
                continue
            stick = sticks.get(end_ts)
            if not stick:
                continue
            quotes[float(strike)] = {
                "yes_ask": _money(stick, "yes_ask", ask_field),
                "yes_bid": _money(stick, "yes_bid"),
                "yes_ask_low": _money(stick, "yes_ask", "low_dollars"),
                "yes_bid_high": _money(stick, "yes_bid", "high_dollars"),
            }
        if not quotes:
            continue
        bars.append(
            ReplayBar(
                end_ts=end_ts,
                spot=tape.spots[minute_ms],
                vol=vol,
                quotes=quotes,
                impulse=impulse,
            )
        )
    return bars


def tape_from_bars(
    event_ticker: str,
    bars: list[ReplayBar],
    results: dict[float, str],
    maturity_s: float,
    band: tuple[float, float] | None = None,
) -> EventTape:
    spots: dict[int, float] = {}
    candles: dict[float, dict] = {}
    for bar in bars:
        minute_ms = (bar.end_ts - 60) * 1000
        spots[minute_ms] = bar.spot
        for strike, quotes in bar.quotes.items():
            ask = quotes.get("yes_ask")
            bid = quotes.get("yes_bid")
            candles.setdefault(strike, {})[bar.end_ts] = {
                "yes_ask": {
                    "close_dollars": ask,
                    "low_dollars": quotes.get("yes_ask_low", ask),
                },
                "yes_bid": {
                    "close_dollars": bid,
                    "high_dollars": quotes.get("yes_bid_high", bid),
                },
            }
    if band is None and results:
        yes = [strike for strike, result in results.items() if result == "yes"]
        no = [strike for strike, result in results.items() if result == "no"]
        if yes and no:
            band = (max(yes), min(no))
        else:
            strikes = list(results)
            band = (min(strikes), max(strikes))
    return EventTape(event_ticker, spots, candles, results, int(maturity_s * 1000), band)


def replay_tape(tape: EventTape, settings: Settings, session: SessionMemory | None = None) -> dict:
    if tape.error:
        return {"event_ticker": tape.event_ticker, "error": tape.error, "takes": []}
    bars = bars_from_tape(tape, settings)
    if not bars or not tape.band:
        return {"event_ticker": tape.event_ticker, "error": tape.error or "incomplete data", "takes": []}
    lo, hi = tape.band
    played = replay_bars(tape.event_ticker, bars, tape.results, tape.maturity_ms / 1000, settings, session)
    return {
        "event_ticker": tape.event_ticker,
        "settlement_band": [lo, hi],
        "settle_mid": (lo + hi) / 2,
        "candles": {str(k): len(v) if isinstance(v, dict) else 0 for k, v in tape.candles.items()},
        "playbook": settings.playbook,
        "takes": played["takes"],
        "best": played["best"],
        "hold_candidates": played["hold_candidates"],
        "session": played.get("session"),
    }


def _session_public(mem) -> dict | None:
    if mem is None:
        return None
    if hasattr(mem, "last_loss_event"):
        return {
            "last_loss_event": mem.last_loss_event,
            "last_side": getattr(mem, "last_side", None),
            "skip_next": mem.skip_next,
            "skipped_event": mem.skipped_event,
        }
    if isinstance(mem, dict):
        return mem
    return None


def summarize_replays(
    reports: list[dict],
    settings: Settings,
    hours: int,
    *,
    write: bool = True,
) -> dict:
    cleaned = []
    for report in reports:
        row = dict(report)
        public = _session_public(row.pop("session", None))
        if public is not None:
            row["session"] = public
        cleaned.append(row)
    taken = [take for report in cleaned for take in report.get("takes") or []]
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
            "impulse_min_p": settings.impulse_min_p,
            "impulse_max_ask": settings.impulse_max_ask,
            "impulse_wait": settings.impulse_wait,
            "impulse_rest": settings.impulse_rest,
            "impulse_wait_min_ask": settings.impulse_wait_min_ask,
            "impulse_wait_max_ask": settings.impulse_wait_max_ask,
            "impulse_wait_max_distance": settings.impulse_wait_max_distance,
            "impulse_wait_stop": settings.impulse_wait_stop,
            "impulse_wait_scratch_seconds": settings.impulse_wait_scratch_seconds,
            "lock_min_p": settings.lock_min_p,
            "min_sigma": settings.min_sigma,
            "invalidate_p": settings.invalidate_p,
            "flatten_seconds": settings.flatten_seconds,
            "allow_early_exit": settings.allow_early_exit,
        },
        "events": cleaned,
        "take_count": len(taken),
        "wins": len(wins),
        "exit_reasons": reasons,
        "realized_pnl": sum((take.get("pnl") or 0) for take in taken),
    }
    if write:
        path = CATALOG_DIR / "snapshot" / "replay.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def replay_tapes(tapes: list[EventTape], settings: Settings, *, write: bool = False) -> dict:
    reports = []
    session = SessionMemory()
    for tape in reversed(tapes):
        try:
            report = replay_tape(tape, settings, session)
            session = report.get("session") or session
            reports.append(report)
        except Exception as exc:
            reports.append({"event_ticker": tape.event_ticker, "error": str(exc), "takes": []})
    reports.reverse()
    return summarize_replays(reports, settings, len(tapes), write=write)


def replay_event(
    client: KalshiClient,
    event_ticker: str,
    settings: Settings,
    session: SessionMemory | None = None,
) -> dict:
    return replay_tape(fetch_event_tape(client, event_ticker, settings), settings, session)


def replay_recent_hours(hours: int = 8, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    return replay_tapes(load_recent_tapes(hours, settings), settings, write=True)

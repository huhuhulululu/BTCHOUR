from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.kalshi import Market
from btchour.model import SpotQuote, digital_prob, effective_vol
from btchour.strategy import (
    _seconds_left,
    coupon_min_ask,
    coupon_rest_ready,
    coupon_sides,
    evaluate_impulse_wait_market,
    is_coupon_window,
    is_next_session_book,
    pick_dump_wait,
)


@dataclass(frozen=True)
class ImpulseReject:
    ticker: str
    side: str
    ask: float | None
    model_p: float
    reasons: list[str]


def tape_impulse(points: list[tuple[datetime, float]], now: datetime, price: float, lookback: float = 180.0) -> float:
    """Dollar change versus the scan/tape print about `lookback` seconds ago."""
    target = lookback
    chosen = None
    best = 10**9
    for ts, value in points:
        age = (now - ts).total_seconds()
        if age < 60:
            continue
        score = abs(age - target)
        if score < best:
            best = score
            chosen = value
    if chosen is None:
        return 0.0
    return price - chosen


def series_impulse(series: list[dict], price: float, ts_ms: int, lookback_ms: int = 180_000) -> float:
    """Impulse from a 1s BRTI series. Use ~lookback if present, else the oldest print ≥60s."""
    if not series or ts_ms is None:
        return 0.0
    older = [point for point in series if ts_ms - int(point["t"]) >= 60_000]
    if not older:
        return 0.0
    target = ts_ms - lookback_ms
    chosen = min(older, key=lambda point: abs(int(point["t"]) - target))
    return price - float(chosen["v"])


def merge_impulse(*values: float) -> float:
    """Keep the largest-magnitude reading. Same-direction moves should not cancel."""
    if not values:
        return 0.0
    return max(values, key=lambda value: abs(value))


def _coupon_ladder_rejects(
    markets: list[Market],
    spot: SpotQuote,
    settings: Settings,
    now: datetime,
) -> list[ImpulseReject]:
    """Nearest session rungs and why they are not a coupon."""
    reach = settings.impulse_wait_max_distance or settings.swing_max_distance
    sides = coupon_sides(spot.impulse, settings)
    rejects: list[tuple[float, ImpulseReject]] = []
    for market in markets:
        if not is_next_session_book(market, now) or market.strike is None:
            continue
        seconds = _seconds_left(market.close_time, now)
        if not is_coupon_window(seconds, settings):
            continue
        dist = abs(market.strike - spot.price)
        if dist > reach + 1e-9:
            continue
        vol = effective_vol(spot.annual_vol, settings.annual_vol)
        p_yes = digital_prob(spot.price, market.strike, seconds, vol)
        for side in sides:
            ask = market.yes_ask_effective if side == "yes" else market.no_ask_effective
            model_p = p_yes if side == "yes" else 1.0 - p_yes
            reasons: list[str] = []
            if ask is None or ask <= 0 or ask >= 1.0:
                reasons.append("no_ask")
            else:
                rest = settings.impulse_rest
                lo = coupon_min_ask(side, settings)
                if ask <= rest + 1e-12:
                    reasons.append(f"ask {ask:.2f}<=rest")
                elif ask + 1e-12 < lo:
                    reasons.append(f"ask {ask:.2f}<{lo:.2f}")
                elif ask > settings.impulse_wait_max_ask + 1e-12:
                    reasons.append(f"ask {ask:.2f}>{settings.impulse_wait_max_ask:.2f}")
                if model_p + 1e-12 < rest:
                    reasons.append(f"p {model_p:.2f}<{rest:.2f}")
            if reasons:
                rejects.append((dist, ImpulseReject(market.ticker, side, ask, model_p, reasons)))
    rejects.sort(key=lambda row: (row[0], abs((row[1].ask or 1.0) - 0.35)))
    return [row[1] for row in rejects]


def diagnose_impulse(
    markets: list[Market],
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    move = spot.impulse
    report: dict = {
        "move": move,
        "status": "no_impulse",
        "need": settings.impulse_min,
        "candidates": [],
    }
    dump_on = abs(move) + 1e-9 >= settings.impulse_min
    forming = any(coupon_rest_ready(side, move, settings) for side in coupon_sides(move, settings))
    if not dump_on and not forming:
        return report
    waits = []
    if forming:
        for market in markets:
            waits.extend(evaluate_impulse_wait_market(market, spot, settings, now))
    wait_count = len(waits)
    waits = pick_dump_wait(waits, spot)
    if waits:
        report["status"] = "wait"
        report["wait"] = waits[0].ticker
        report["open"] = 0
        report["wait_count"] = wait_count
        report["candidates"] = [
            {
                "ticker": wait.ticker,
                "side": wait.side,
                "ask": wait.ask,
                "p": wait.model_p,
                "reasons": [],
            }
            for wait in waits
        ]
        return report
    if forming and not dump_on:
        rejects = _coupon_ladder_rejects(markets, spot, settings, now)
        if rejects:
            report["status"] = "no_coupon"
            report["open"] = 0
            report["wait_count"] = 0
            report["candidates"] = [
                {"ticker": row.ticker, "side": row.side, "ask": row.ask, "p": row.model_p, "reasons": row.reasons}
                for row in rejects[:8]
            ]
            return report
    if not dump_on:
        report["wait_count"] = 0
        report["open"] = 0
        return report
    want_yes = move > 0
    side = "yes" if want_yes else "no"
    rejects: list[ImpulseReject] = []
    ok = 0
    for market in markets:
        if not is_next_session_book(market, now):
            continue
        if market.strike is None:
            continue
        if abs(market.strike - spot.price) > settings.swing_max_distance + 1e-9:
            continue
        seconds = _seconds_left(market.close_time, now)
        if seconds + 1e-12 < settings.swing_min_seconds:
            continue
        vol = effective_vol(spot.annual_vol, settings.annual_vol)
        p_yes = digital_prob(spot.price, market.strike, seconds, vol)
        model_p = p_yes if want_yes else 1.0 - p_yes
        ask = market.yes_ask_effective if want_yes else market.no_ask_effective
        reasons: list[str] = []
        if ask is None or ask <= 0 or ask >= 1.0:
            reasons.append("no_ask")
        else:
            if ask < settings.swing_min_ask:
                reasons.append(f"ask {ask:.2f}<{settings.swing_min_ask:.2f}")
            if ask > settings.impulse_max_ask:
                reasons.append(f"ask {ask:.2f}>{settings.impulse_max_ask:.2f}")
            if model_p + 1e-12 < settings.impulse_min_p:
                reasons.append(f"p {model_p:.2f}<{settings.impulse_min_p:.2f}")
            if (model_p - ask) + 1e-12 < settings.impulse_min_gap:
                reasons.append(f"gap {model_p - ask:.2f}<{settings.impulse_min_gap:.2f}")
        if not reasons:
            ok += 1
        rejects.append(ImpulseReject(market.ticker, side, ask, model_p, reasons))
    rejects.sort(key=lambda row: abs((row.ask or 1.0) - 0.45))
    if ok:
        report["status"] = "open"
    else:
        report["status"] = "blocked"
    report["open"] = ok
    report["wait_count"] = 0
    report["candidates"] = [
        {"ticker": row.ticker, "side": row.side, "ask": row.ask, "p": row.model_p, "reasons": row.reasons}
        for row in rejects[:8]
    ]
    return report


def journal_line(diagnosis: dict) -> str:
    """One-line reject for the paper journal. Empty candidate lists stay empty."""
    status = str(diagnosis.get("status") or "")
    candidates = diagnosis.get("candidates") or []
    if not candidates:
        return "blocked_no_hourly_candidates" if status == "blocked" else ""
    row = candidates[0]
    ticker = row.get("ticker") or ""
    ask = row.get("ask")
    p = row.get("p")
    reasons = ",".join(row.get("reasons") or [])
    if not ticker and ask is None and p is None:
        return "blocked_no_hourly_candidates" if status == "blocked" else ""
    line = f"{ticker} ask={ask} p={p} {reasons}".strip()
    wait = diagnosis.get("wait")
    if status == "wait" and wait:
        return f"wait {wait} | {line}"
    return line

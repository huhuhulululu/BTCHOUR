from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.kalshi import Market
from btchour.model import SpotQuote, digital_prob, effective_vol
from btchour.strategy import _seconds_left, evaluate_impulse_wait_market, is_fast_window


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
    if abs(move) + 1e-9 < settings.impulse_min:
        return report
    want_yes = move > 0
    side = "yes" if want_yes else "no"
    rejects: list[ImpulseReject] = []
    ok = 0
    for market in markets:
        if not is_fast_window(market.open_time, market.close_time):
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
    waits = []
    if move < 0:
        for market in markets:
            waits.extend(evaluate_impulse_wait_market(market, spot, settings, now))
    if ok:
        report["status"] = "open"
    elif waits:
        report["status"] = "wait"
        report["wait"] = waits[0].ticker
    else:
        report["status"] = "blocked"
    report["open"] = ok
    report["wait_count"] = len(waits)
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

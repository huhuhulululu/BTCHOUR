from __future__ import annotations

import json
from datetime import datetime, timezone

from btchour.catalog import sync_catalog
from btchour.config import CATALOG_DIR, Settings, load_settings
from btchour.engine import _markets_from_snapshot, make_client
from btchour.kalshi import KalshiClient
from btchour.model import SpotQuote, effective_vol
from btchour.score import score_market
from btchour.strategy import evaluate_scalp_market, market_window_ok, scan_markets


def probe_book(client: KalshiClient | None = None, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    client = client or make_client(settings)
    snapshot = sync_catalog(client, settings)
    spot_info = snapshot["spot"]
    spot = SpotQuote(
        price=spot_info["price"],
        source=spot_info["source"],
        twap60=spot_info.get("twap60"),
        annual_vol=spot_info.get("annual_vol") or settings.annual_vol,
        ts_ms=spot_info.get("ts_ms"),
    )
    markets = _markets_from_snapshot(snapshot)
    now = datetime.now(timezone.utc)
    scores = []
    for market in markets:
        if not market_window_ok(market.open_time, market.close_time, settings):
            continue
        close = datetime.fromisoformat((market.close_time or now.isoformat()).replace("Z", "+00:00"))
        seconds = (close - now).total_seconds()
        scores.extend(
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
    scores.sort(key=lambda row: row.ev, reverse=True)
    passing = [row for row in scores if row.passes]
    locks = scan_markets(markets, spot, settings, now) if settings.playbook == "lock" else []
    scalps = []
    if settings.playbook in {"flex", "scalp"}:
        for market in markets:
            scalps.extend(evaluate_scalp_market(market, spot, settings, now))
        scalps.sort(key=lambda row: ((row.model_p - row.ask), row.ev), reverse=True)
    waits = [row for row in locks if row.play == "lock_wait"]
    takes = [row for row in locks if row.play == "lock_hold"]
    high_p = [row for row in scores if row.model_p >= settings.min_win_prob]
    cheapest_high_p = sorted(high_p, key=lambda row: row.ask)[:5]
    report = {
        "probed_at": now.isoformat(),
        "event": (snapshot.get("current_hour") or {}).get("event"),
        "horizons": [block.get("event") for block in (snapshot.get("tradable") or [])],
        "spot": spot_info,
        "formula": "EV = p * b - (1 - p)  where b = if-win net odds after fees",
        "playbook": settings.playbook,
        "gates": {
            "target_if_win": settings.target_profit,
            "min_win_prob": settings.min_win_prob,
            "min_ev": settings.min_expected_roi,
            "min_sigma": settings.min_sigma,
        },
        "scored": len(scores),
        "passing": [row.as_dict() for row in passing],
        "lock_takes": [row.as_dict() for row in takes],
        "lock_waits": [row.as_dict() for row in waits[:8]],
        "cheapest_high_p": [row.as_dict() for row in cheapest_high_p],
        "best_ev": [row.as_dict() for row in scores[:12]],
        "near_miss_high_p": [
            row.as_dict()
            for row in scores
            if row.model_p >= settings.min_win_prob and not row.passes
        ][:8],
        "scalps": [row.as_dict() for row in scalps[:8]],
    }
    path = CATALOG_DIR / "snapshot" / "probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report

from __future__ import annotations

from datetime import datetime, timezone

from btchour.catalog import sync_catalog
from btchour.config import CATALOG_DIR, Settings, load_settings
from btchour.engine import _markets_from_snapshot, make_client
from btchour.kalshi import KalshiClient
from btchour.model import SpotQuote, effective_vol
from btchour.score import score_market
from btchour.strategy import evaluate_scalp_market
from btchour.tickers import is_hourly_window


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
        if settings.hourly_only and not is_hourly_window(market.open_time, market.close_time):
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
    scalps = []
    for market in markets:
        if settings.hourly_only and not is_hourly_window(market.open_time, market.close_time):
            continue
        scalps.extend(evaluate_scalp_market(market, spot, settings, now))
    scalps.sort(key=lambda row: ((row.model_p - row.ask), row.ev), reverse=True)
    report = {
        "probed_at": now.isoformat(),
        "event": (snapshot.get("current_hour") or {}).get("event"),
        "spot": spot_info,
        "formula": "EV = p * b - (1 - p)  where b = if-win net odds after fees",
        "playbook": settings.playbook,
        "gates": {
            "target_if_win": settings.target_profit,
            "min_win_prob": settings.min_win_prob,
            "min_ev": settings.min_expected_roi,
            "scalp_min_p": settings.scalp_min_p,
            "scalp_min_gap": settings.scalp_min_gap,
            "scalp_max_entry": settings.scalp_max_entry,
            "scalp_min_seconds": settings.scalp_min_seconds,
        },
        "scored": len(scores),
        "passing": [row.as_dict() for row in passing],
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
    import json

    path.write_text(json.dumps(report, indent=2) + "\n")
    return report

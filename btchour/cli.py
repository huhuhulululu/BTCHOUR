from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from btchour.catalog import sync_catalog
from btchour.config import load_settings
from btchour.engine import make_client, run_cycle, run_loop, scan_once
from btchour.store import Store


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kalshi BTC hourly (KXBTCD) catalog and 20% if-win engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync", help="Pull Kalshi hourly directory into catalog/")
    sub.add_parser("scan", help="Sync, score the current hour, print qualifying tickets")
    run = sub.add_parser("run", help="Loop: scan, paper/live fill, settle")
    run.add_argument("--once", action="store_true", help="Single cycle then exit")
    sub.add_parser("status", help="Local paper/live ledger summary")

    args = parser.parse_args(argv)
    settings = load_settings()
    client = make_client(settings)

    if args.cmd == "sync":
        snapshot = sync_catalog(client, settings)
        _print_json(
            {
                "synced_at": snapshot["synced_at"],
                "spot": snapshot["spot"],
                "open_events": snapshot["open_events"],
                "current_hour": snapshot["current_hour"]["event"],
                "markets": snapshot["current_hour"]["market_count"],
                "wrote": ["catalog/series/", "catalog/snapshot/", "data/catalog/latest.json"],
            }
        )
        return 0

    if args.cmd == "scan":
        payload = scan_once(client, settings)
        _print_json(
            {
                "event": payload["event"],
                "spot": payload["spot"],
                "market_count": payload["market_count"],
                "opportunity_count": len(payload["opportunities"]),
                "opportunities": payload["opportunities"],
                "note": (
                    "Empty opportunities is normal. The bot only prints a ticket when "
                    f"if-win ROI >= {settings.target_profit:.0%} and model P(win) >= {settings.min_win_prob:.0%}."
                ),
            }
        )
        return 0

    if args.cmd == "run":
        if args.once:
            _print_json(run_cycle(client, settings))
            return 0
        print(
            f"starting {settings.mode} loop at {datetime.now(timezone.utc).isoformat()} "
            f"target_if_win={settings.target_profit:.0%} min_p={settings.min_win_prob:.0%}",
            flush=True,
        )
        run_loop(settings)
        return 0

    if args.cmd == "status":
        store = Store()
        _print_json({"summary": store.summary(), "open": [dict(row) for row in store.open_trades()]})
        return 0

    parser.error(f"unknown command {args.cmd}")
    return 2

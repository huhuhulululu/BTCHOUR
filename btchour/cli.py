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
    parser = argparse.ArgumentParser(description="Kalshi BTC hourly (KXBTCD) engine. Score: EV = p*b - (1-p)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    calc = sub.add_parser("ev", help="Compute EV = p*b - (1-p)")
    calc.add_argument("--p", type=float, required=True, help="P(win) in [0,1]")
    calc.add_argument("--b", type=float, required=True, help="Net odds (if-win profit / stake)")

    sub.add_parser("sync", help="Pull Kalshi hourly directory into catalog/")
    sub.add_parser("scan", help="Sync, score the current hour, print qualifying tickets")
    sub.add_parser("probe", help="Score the live book, including EV near-misses")
    replay = sub.add_parser("replay", help="Minute-replay recent settled hours against the 20% EV gate")
    replay.add_argument("--hours", type=int, default=8)
    run = sub.add_parser("run", help="Loop: scan, paper/live fill, settle")
    run.add_argument("--once", action="store_true", help="Single cycle then exit")
    sub.add_parser("status", help="Local paper/live ledger summary")

    args = parser.parse_args(argv)
    if args.cmd == "ev":
        from btchour.fees import Edge

        edge = Edge.from_parts(args.p, args.b)
        _print_json(edge.as_dict())
        return 0

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

    if args.cmd == "probe":
        from btchour.probe import probe_book

        report = probe_book(client, settings)
        _print_json(
            {
                "event": report["event"],
                "spot": report["spot"],
                "formula": report["formula"],
                "passing": report["passing"],
                "best_ev": report["best_ev"][:8],
                "near_miss_high_p": report["near_miss_high_p"],
            }
        )
        return 0

    if args.cmd == "replay":
        from btchour.replay import replay_recent_hours

        summary = replay_recent_hours(args.hours, settings)
        _print_json(
            {
                "hours": summary["hours"],
                "take_count": summary["take_count"],
                "wins": summary["wins"],
                "realized_pnl": summary["realized_pnl"],
                "events": [
                    {
                        "event_ticker": item.get("event_ticker"),
                        "settlement_band": item.get("settlement_band"),
                        "takes": item.get("takes") or [],
                        "best": item.get("best"),
                        "error": item.get("error"),
                    }
                    for item in summary["events"]
                ],
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
                "formula": payload.get("formula"),
                "best_ev": payload.get("best_ev"),
                "note": (
                    "Empty is normal. Ticket requires EV=p*b-(1-p) "
                    f">= {settings.min_ev:.0%}, b >= {settings.target_profit:.0%}, p >= {settings.min_win_prob:.0%}."
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

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone

from btchour.catalog import sync_catalog
from btchour.config import load_settings
from btchour.engine import make_client, run_cycle, run_loop, scan_once
from btchour.store import Store


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _with_playbook(settings, playbook: str | None, no_early_exit: bool = False):
    updates = {}
    if playbook:
        updates["playbook"] = playbook
        if playbook == "lock":
            updates["allow_early_exit"] = False
            updates["allow_maker"] = True
            updates["min_win_prob"] = settings.lock_min_p
            updates["poll_seconds"] = max(settings.poll_seconds, 5)
        elif playbook in {"flex", "swing"}:
            updates["allow_early_exit"] = True
            updates["allow_maker"] = playbook == "flex"
            updates["poll_seconds"] = min(settings.poll_seconds, 3)
        elif playbook == "scalp":
            updates["allow_early_exit"] = True
            updates["allow_maker"] = False
        elif playbook == "hold":
            updates["allow_early_exit"] = False
    if no_early_exit:
        updates["allow_early_exit"] = False
    return replace(settings, **updates) if updates else settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kalshi BTC hourly engine. Score: EV = p*b - (1-p). Default playbook: flex (lock first, then 做T)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    calc = sub.add_parser("ev", help="Compute EV = p*b - (1-p)")
    calc.add_argument("--p", type=float, required=True, help="P(win) in [0,1]")
    calc.add_argument("--b", type=float, required=True, help="Net odds (if-win profit / stake)")

    sub.add_parser("sync", help="Pull Kalshi hourly directory into catalog/")
    scan = sub.add_parser("scan", help="Sync, score the current hour, print qualifying tickets")
    scan.add_argument("--playbook", choices=["flex", "swing", "lock", "hold", "scalp"])
    probe = sub.add_parser("probe", help="Score the live book: lock takes/waits, 做T swings, EV near-misses")
    probe.add_argument("--playbook", choices=["flex", "swing", "lock", "hold", "scalp"])
    replay = sub.add_parser("replay", help="Minute-replay recent settled hours (flex / swing / lock / hold / scalp)")
    replay.add_argument("--hours", type=int, default=8)
    replay.add_argument("--playbook", choices=["flex", "swing", "lock", "hold", "scalp"])
    replay.add_argument("--no-early-exit", action="store_true", help="Force hold-to-settle (no lock/invalidate/flatten)")
    run = sub.add_parser("run", help="Loop: manage exits, scan, paper/live fill, settle")
    run.add_argument("--once", action="store_true")
    run.add_argument("--playbook", choices=["flex", "swing", "lock", "hold", "scalp"])
    sub.add_parser("status", help="Local paper/live ledger summary")
    sub.add_parser("learn", help="Show recent impulse journal: what printed, what was rejected, why")

    args = parser.parse_args(argv)
    if args.cmd == "ev":
        from btchour.fees import Edge

        edge = Edge.from_parts(args.p, args.b)
        _print_json(edge.as_dict())
        return 0

    settings = load_settings()
    settings = _with_playbook(
        settings,
        getattr(args, "playbook", None),
        getattr(args, "no_early_exit", False),
    )
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
                "horizons": report.get("horizons"),
                "spot": report["spot"],
                "formula": report["formula"],
                "playbook": report.get("playbook"),
                "passing": report["passing"],
                "lock_takes": report.get("lock_takes"),
                "lock_waits": report.get("lock_waits"),
                "swings": report.get("swings"),
                "cheapest_high_p": report.get("cheapest_high_p"),
                "scalps": report.get("scalps"),
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
                "playbook": summary.get("playbook"),
                "take_count": summary["take_count"],
                "wins": summary["wins"],
                "exit_reasons": summary.get("exit_reasons"),
                "realized_pnl": summary["realized_pnl"],
                "events": [
                    {
                        "event_ticker": item.get("event_ticker"),
                        "settlement_band": item.get("settlement_band"),
                        "takes": item.get("takes") or [],
                        "best": item.get("best"),
                        "hold_candidates": item.get("hold_candidates"),
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
                "playbook": payload.get("playbook"),
                "opportunity_count": len(payload["opportunities"]),
                "opportunities": payload["opportunities"],
                "formula": payload.get("formula"),
                "best_ev": payload.get("best_ev"),
                "note": (
                    f"Playbook={settings.playbook}. flex = lock_hold first, then swing_t 做T, then lock_wait. "
                    f"Lock still needs EV=p*b-(1-p) >= {settings.min_ev:.0%}, σ>={settings.min_sigma}, "
                    f"p>={settings.lock_min_p:.1%}, ask<=$0.82. 做T clips ~{settings.swing_target:.0%} or trails; "
                    "it is not a locked 20%."
                ),
            }
        )
        return 0

    if args.cmd == "run":
        if args.once:
            _print_json(run_cycle(client, settings))
            return 0
        print(
            f"starting {settings.mode} loop playbook={settings.playbook} "
            f"at {datetime.now(timezone.utc).isoformat()} "
            f"target_if_win={settings.target_profit:.0%} min_p={settings.min_win_prob:.0%} "
            f"early_exit={settings.allow_early_exit}",
            flush=True,
        )
        run_loop(settings)
        return 0

    if args.cmd == "status":
        store = Store()
        _print_json({"summary": store.summary(), "open": [dict(row) for row in store.open_trades()]})
        return 0

    if args.cmd == "learn":
        store = Store()
        rows = [dict(row) for row in store.recent_journal(20)]
        _print_json({"journal": rows, "summary": store.summary()})
        return 0

    parser.error(f"unknown command {args.cmd}")
    return 2

from __future__ import annotations

import json
from datetime import datetime, timezone

from btchour.config import CATALOG_DIR, Settings, apply_playbook, load_settings
from btchour.replay import EventTape, load_recent_tapes, replay_tapes


DEFAULT_VARIANTS = [
    {"name": "flex_skip", "playbook": "flex", "skip_after_loss": True},
    {
        "name": "flex_nowait",
        "playbook": "flex",
        "skip_after_loss": True,
        "impulse_wait": False,
    },
    {
        "name": "flex_wait_loose",
        "playbook": "flex",
        "skip_after_loss": True,
        "impulse_wait": True,
        "impulse_wait_min_ask": 0.0,
        "impulse_wait_max_ask": 0.48,
        "impulse_wait_max_distance": 600.0,
        "impulse_wait_scratch_seconds": 0.0,
    },
    {"name": "flex_noskip", "playbook": "flex", "skip_after_loss": False},
    {
        "name": "flex_cheap",
        "playbook": "flex",
        "skip_after_loss": True,
        "impulse_min_p": 0.30,
        "impulse_max_ask": 0.35,
    },
    {
        "name": "flex_cheap_wide",
        "playbook": "flex",
        "skip_after_loss": True,
        "impulse_min_p": 0.30,
        "impulse_max_ask": 0.52,
    },
    {"name": "swing_skip", "playbook": "swing", "skip_after_loss": True},
    {"name": "lock", "playbook": "lock", "skip_after_loss": False},
]


def compact_run(summary: dict, name: str) -> dict:
    takes = []
    for event in summary.get("events") or []:
        for take in event.get("takes") or []:
            takes.append(
                {
                    "event": take.get("event_ticker") or event.get("event_ticker"),
                    "side": take.get("side"),
                    "ask": take.get("ask"),
                    "play": take.get("play"),
                    "exit": take.get("exit_reason"),
                    "roi": take.get("roi"),
                    "pnl": take.get("pnl"),
                }
            )
    return {
        "name": name,
        "hours": summary.get("hours"),
        "playbook": summary.get("playbook"),
        "skip_after_loss": (summary.get("gates") or {}).get("skip_after_loss"),
        "impulse_min_p": (summary.get("gates") or {}).get("impulse_min_p"),
        "impulse_max_ask": (summary.get("gates") or {}).get("impulse_max_ask"),
        "impulse_wait": (summary.get("gates") or {}).get("impulse_wait"),
        "take_count": summary.get("take_count"),
        "wins": summary.get("wins"),
        "realized_pnl": summary.get("realized_pnl"),
        "exit_reasons": summary.get("exit_reasons"),
        "takes": takes,
    }


def sweep_tapes(
    tapes: list[EventTape],
    settings: Settings,
    variants: list[dict] | None = None,
    windows: list[int] | None = None,
    *,
    write: bool = True,
) -> dict:
    variants = variants or DEFAULT_VARIANTS
    windows = windows or [len(tapes)]
    runs = []
    for window in windows:
        slice_tapes = tapes[:window]
        for variant in variants:
            cfg = apply_playbook(
                settings,
                variant["playbook"],
                skip_after_loss=variant.get("skip_after_loss"),
                extras=variant,
            )
            summary = replay_tapes(slice_tapes, cfg, write=False)
            label = variant["name"]
            if len(windows) > 1:
                label = f"{variant['name']}_{window}h"
            runs.append(compact_run(summary, label))
    payload = {
        "swept_at": datetime.now(timezone.utc).isoformat(),
        "hours": len(tapes),
        "windows": windows,
        "events": [tape.event_ticker for tape in tapes],
        "formula": "EV = p * b - (1 - p)",
        "runs": runs,
    }
    if write:
        path = CATALOG_DIR / "snapshot" / "sweep.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def sweep_recent_hours(hours: int = 16, settings: Settings | None = None) -> dict:
    """Fetch each hour once (cached under data/replay-cache/), then replay several playbooks."""
    settings = settings or load_settings()
    fetch_hours = 24 if hours >= 16 else hours
    tapes = load_recent_tapes(fetch_hours, settings)
    windows = [16, 24] if fetch_hours >= 24 else [len(tapes)]
    payload = sweep_tapes(tapes, settings, windows=windows)
    flex = apply_playbook(settings, "flex", skip_after_loss=True)
    replay_tapes(tapes[: min(16, len(tapes))], flex, write=True)
    return payload

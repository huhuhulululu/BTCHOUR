"""A durable tape archive, so a backtest is reproducible after the cache dies.

`data/replay-cache/` is where `sweep` parks whatever it happened to fetch this
run. That is a cache, not a dataset: it is gitignored, it is overwritten, and
nothing records which hours it holds. Every number the repo has ever published
therefore rests on a sample nobody can re-run.

The archive fixes only that. It stores each settled hour once, gzipped, under
`data/archive/`, and hands them back oldest-or-newest-first on request so a
train/test split means the same thing tomorrow.

Kalshi access is required to *fill* the archive (`sweep` / `replay` do that);
reading it needs nothing.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime

from btchour.config import DATA_DIR
from btchour.replay import EventTape, tape_cache_path
from btchour.tickers import parse_event_ticker


def archive_dir():
    return DATA_DIR / "archive"


def archive_path(event_ticker: str):
    return archive_dir() / f"{event_ticker}.json.gz"


def store_tape(tape: EventTape) -> bool:
    """Write one settled hour. Returns False for an hour not worth keeping."""
    if tape.error or not tape.spots or not tape.band or not tape.maturity_ms:
        return False
    path = archive_path(tape.event_ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(tape.to_dict(), handle)
    return True


def load_tape(event_ticker: str) -> EventTape | None:
    path = archive_path(event_ticker)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return EventTape.from_dict(json.load(handle))
    except Exception:
        return None


def archived_tickers() -> list[str]:
    directory = archive_dir()
    if not directory.is_dir():
        return []
    return sorted(path.name[: -len(".json.gz")] for path in directory.glob("*.json.gz"))


def absorb_cache() -> dict:
    """Move whatever the replay cache currently holds into the archive.

    Run it after a `sweep`; it is idempotent, so running it twice costs
    nothing and keeps hours the next sweep would otherwise drop.
    """
    cache_dir = tape_cache_path("X").parent
    added, skipped = [], []
    if not cache_dir.is_dir():
        return {"archive": str(archive_dir()), "added": [], "skipped": [], "total": len(archived_tickers())}
    for path in sorted(cache_dir.glob("*.json")):
        try:
            tape = EventTape.from_dict(json.loads(path.read_text()))
        except Exception:
            skipped.append(path.stem)
            continue
        (added if store_tape(tape) else skipped).append(tape.event_ticker or path.stem)
    return {
        "archive": str(archive_dir()),
        "added": added,
        "skipped": skipped,
        "total": len(archived_tickers()),
    }


def _close_utc(event_ticker: str) -> datetime | None:
    try:
        return parse_event_ticker(event_ticker)["close_utc"]
    except ValueError:
        return None


def load_archive(limit: int | None = None) -> list[EventTape]:
    """Archived hours, newest first -- the order `replay_tapes` expects."""
    dated = []
    for ticker in archived_tickers():
        close = _close_utc(ticker)
        if close is not None:
            dated.append((close, ticker))
    dated.sort(reverse=True)
    if limit is not None:
        dated = dated[:limit]
    tapes = [load_tape(ticker) for _, ticker in dated]
    return [tape for tape in tapes if tape is not None]


def archive_summary() -> dict:
    tickers = archived_tickers()
    closes = [c for c in (_close_utc(t) for t in tickers) if c is not None]
    return {
        "archive": str(archive_dir()),
        "hours": len(tickers),
        "earliest": min(closes).isoformat() if closes else None,
        "latest": max(closes).isoformat() if closes else None,
    }

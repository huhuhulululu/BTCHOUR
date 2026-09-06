"""Run the *production replay loop* over the 66-day archive instead of 16 live hours.

`btchour replay --hours 16` is the only end-to-end measurement this repo has ever had of
what the whole machine earns: entries, the pick order, session memory, the skip-after-loss
rule, the exit stack, settlement. Every study in `research/` measures one play in
isolation, so none of them can answer the question `docs/GOALS.md` actually asks -- what
does a *day* of the current default configuration make.

This builds `replay.EventTape` objects out of `data/hourly.sqlite` and hands them to
`replay.replay_tape` untouched. Nothing in `btchour/` is reimplemented or relaxed: the
same function that replays a live hour replays 1555 of them.

    python3 research/replay_db.py                       # the shipping default
    python3 research/replay_db.py --playbook swing      # what `--playbook swing` would do
    python3 research/replay_db.py --no-hold             # 017 off, as a control
    python3 research/replay_db.py --slice early         # calendar half
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import btchour.replay as replay_mod  # noqa: E402
from btchour.config import Settings  # noqa: E402
from btchour.replay import EventTape, replay_tape  # noqa: E402
from btchour.strategy import SessionMemory, _clip_count  # noqa: E402
from research.hourly_lab import DEFAULT_DB, clustered_t  # noqa: E402

SIDES = ("yes_bid", "yes_ask")


def _stick(row) -> dict:
    out: dict = {}
    for side in SIDES:
        out[side] = {
            "open_dollars": row[f"{side}_open"],
            "high_dollars": row[f"{side}_high"],
            "low_dollars": row[f"{side}_low"],
            "close_dollars": row[f"{side}_close"],
        }
    out["price"] = {"close_dollars": row["price_close"]}
    out["volume_fp"] = row["volume"]
    out["open_interest_fp"] = row["open_interest"]
    return out



def fill_through(side: str, rest: float, close_ask, *, yes_bid_high=None,
                 yes_ask_low=None, impulse=None, min_impulse=None) -> bool:
    """A resting bid is hit whenever the offer comes DOWN to it -- including through it.

    Production's `_ask_at_rest` requires the ask to be *at* the rest (or one tick
    through) and treats a book that collapsed past it as no fill: "Paper AUG2802 filled
    T79599/T79499 NO at 0.25 after the book was 0.03. That is not ask==rest."

    On a live book it is. A 25c resting bid is exactly what gets hit as the offer falls
    from 32c to 3c -- you own it at 25c and it is worth 3c. Refusing that fill drops the
    adverse-selected half of the distribution, which is the half ADR 015 was worried
    about in the first place. This keeps every impulse guard (pulling the rest when the
    tape flips is a real cancel) and relaxes only the price test, so the difference
    between the two runs is the fill convention and nothing else.
    """
    if impulse is not None:
        if side == "no" and impulse >= 0:
            return False
        if side == "yes" and impulse <= 0:
            return False
        if min_impulse is not None and abs(impulse) + 1e-9 < abs(min_impulse):
            return False
    candidates = [close_ask]
    if side == "no" and yes_bid_high is not None:
        candidates.append(1.0 - float(yes_bid_high))
    if side == "yes" and yes_ask_low is not None:
        candidates.append(float(yes_ask_low))
    return any(c is not None and float(c) <= rest + 1e-12 for c in candidates)



def same_bar_fill_line() -> int | None:
    """Line of a promotion that fills a coupon on the bar it was hung, if one exists.

    `replay_bars` used to call `wait_book_crossed` twice. The first is the honest one: a
    rest hung on an earlier bar, tested against this bar. The second ran immediately
    after hanging and filled from the SAME bar -- the hang was decided on that bar's
    close (`ask_field` is close_dollars for flex) while the fill read that bar's low or
    high, an extreme that may have printed before the order existed.

    ADR 032 removed that second site, so this now returns None on a healthy tree and the
    flag becomes a no-op. It is kept as a tripwire: if a second fill site ever comes
    back, this finds it again and `--no-same-bar-fill` starts doing real work.
    """
    import inspect

    lines, start = inspect.getsourcelines(replay_mod.replay_bars)
    hits = [start + i for i, line in enumerate(lines) if "wait_book_crossed(" in line]
    return max(hits) if len(hits) >= 2 else None


def no_same_bar(inner):
    """Wrap a fill rule so a same-bar promotion never fills. No-op once 032 is in."""
    import sys

    blocked = same_bar_fill_line()
    if blocked is None:
        log_once = ("note: --no-same-bar-fill is a no-op -- ADR 032 removed the same-bar"
                    " promotion from replay_bars, so this is now the default behaviour."
                    " To reproduce 032's before/after (+7.64c t=3.23 vs +2.18c t=0.56),"
                    " check out the commit before the fix:"
                    " `git checkout bc1ff58 -- btchour/replay.py`.")
        print(log_once)
        return inner

    def guarded(*args, **kwargs):
        frame = sys._getframe(1)
        if frame.f_lineno == blocked:
            return False
        return inner(*args, **kwargs)

    return guarded



def close_ask_bars(inner):
    """Force the entry ask to the minute's CLOSE, whatever the playbook asked for.

    `bars_from_tape` uses `low_dollars` as the ask for the whole bar when the playbook is
    `lock`. The strategy then decides on that price and fills at it, which assumes it
    always caught the best print of the minute -- the same folded time axis ADR 032 found
    in the coupon path, in a second place. This wrapper rebuilds each bar's ask from the
    candle close so the two can be compared. `yes_ask_low` is left alone: using the
    minute's low to ask "would a resting order have been hit" is legitimate.
    """
    def wrapped(tape, settings):
        bars = inner(tape, settings)
        for bar in bars:
            for strike, quotes in bar.quotes.items():
                stick = (tape.candles.get(strike) or {}).get(bar.end_ts)
                if not stick:
                    continue
                close = (stick.get("yes_ask") or {}).get("close_dollars")
                if close in (None, ""):
                    continue
                value = float(close)
                quotes["yes_ask"] = value if 0 < value < 1 else None
        return bars

    return wrapped


def tapes(db: Path, limit: int | None = None, slice_half: str = ""):
    """One `EventTape` per settled hour, in the shape `replay_tape` already expects."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    events = conn.execute(
        "SELECT event_ticker, open_ts, close_ts, settle_value FROM events"
        " WHERE settle_value IS NOT NULL ORDER BY close_ts"
    ).fetchall()
    if limit:
        events = events[-limit:]
    if slice_half:
        mid = len(events) // 2
        events = events[:mid] if slice_half == "early" else events[mid:]

    for row in events:
        ev, open_ts, close_ts = row["event_ticker"], int(row["open_ts"]), int(row["close_ts"])

        # spots: minute-close BRTI keyed by the minute's start in ms, as the live tape is
        by_minute: dict[int, float] = {}
        for spot in conn.execute(
            "SELECT ts, value FROM spot WHERE event_ticker=? ORDER BY ts", (ev,)
        ):
            minute = (int(spot["ts"]) // 60) * 60
            by_minute[minute] = float(spot["value"])
        if len(by_minute) < 20:
            continue
        spots = {minute * 1000: value for minute, value in by_minute.items()}

        results: dict[float, str] = {}
        for market in conn.execute(
            "SELECT strike, result FROM markets WHERE event_ticker=?", (ev,)
        ):
            results[float(market["strike"])] = market["result"] or ""

        candles: dict[float, dict] = {}
        for quote in conn.execute(
            "SELECT * FROM quotes WHERE event_ticker=? ORDER BY ts", (ev,)
        ):
            strike = float(quote["strike"])
            ts = int(quote["ts"])
            if ts <= open_ts or ts > close_ts:
                continue
            candles.setdefault(strike, {})[ts] = _stick(quote)
        if not candles:
            continue

        yes = [k for k, r in results.items() if r == "yes"]
        no = [k for k, r in results.items() if r == "no"]
        band = (max(yes), min(no)) if yes and no else None
        if band is None:
            continue

        yield EventTape(
            event_ticker=ev,
            spots=spots,
            candles=candles,
            results=results,
            maturity_ms=close_ts * 1000,
            band=band,
        )
    conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--playbook", default="flex",
                    choices=["flex", "swing", "lock", "edge", "hold", "scalp"])
    ap.add_argument("--no-hold", action="store_true",
                    help="turn ADR 017 off (pre-017 clip stack on a filled coupon)")
    ap.add_argument("--no-early-exit", action="store_true")
    ap.add_argument("--impulse-taker", action="store_true")
    ap.add_argument("--fill", default="atrest", choices=["atrest", "through"],
                    help="atrest = production `_ask_at_rest`; through = a resting bid is"
                         " hit whenever the offer falls to or past it")
    ap.add_argument("--close-ask", action="store_true",
                    help="entry ask = the minute close even under --playbook lock, which"
                         " otherwise decides and fills on the minute's low")
    ap.add_argument("--no-same-bar-fill", action="store_true",
                    help="refuse a fill landing on the bar the rest was hung. No-op since"
                         " ADR 032 removed that path from production; kept as a tripwire")
    ap.add_argument("--slip", type=float, default=0.0,
                    help="cents of adverse slippage charged to every entry")
    args = ap.parse_args(argv)

    settings = Settings(playbook=args.playbook)
    if args.no_hold:
        settings = replace(settings, impulse_wait_hold=False)
    if args.no_early_exit:
        settings = replace(settings, allow_early_exit=False)
    if args.impulse_taker:
        settings = replace(settings, impulse_taker=True)

    if args.close_ask:
        replay_mod.bars_from_tape = close_ask_bars(replay_mod.bars_from_tape)

    base = fill_through if args.fill == "through" else replay_mod.wait_book_crossed
    if args.no_same_bar_fill:
        replay_mod.wait_book_crossed = no_same_bar(base)
    elif args.fill == "through":
        replay_mod.wait_book_crossed = base

    takes: list[dict] = []
    hours = 0
    session = SessionMemory()
    for tape in tapes(args.db, args.limit or None, args.slice):
        hours += 1
        report = replay_tape(tape, settings, session)
        for take in report.get("takes") or []:
            take["event_ticker"] = tape.event_ticker
            takes.append(take)

    days = hours / 24.0
    label = (f"playbook={args.playbook} hold={not args.no_hold}"
             f" early_exit={not args.no_early_exit} impulse_taker={args.impulse_taker}"
             f" fill={args.fill} same_bar={not args.no_same_bar_fill}"
             f" close_ask={args.close_ask}")
    print(f"# hours={hours} days={days:.1f} slice={args.slice or 'full'}  {label}")
    if not takes:
        print("   no entries -- the loop never took a position over this whole tape")
        return 0

    pnl = [float(t.get("pnl") or 0.0) - args.slip / 100.0 * float(t.get("count") or 1.0)
           for t in takes]
    clusters = [t["event_ticker"] for t in takes]
    mean, t_stat, lo, hi = clustered_t(pnl, clusters)
    wins = sum(1 for p in pnl if p > 0)
    total = sum(pnl)

    print(f"   entries      {len(takes)}  ({len(takes) / max(days, 1e-9):.2f}/day,"
          f" in {len(set(clusters))} of {hours} hours)")
    print(f"   per entry    ${mean:+.4f}  t={t_stat:+.2f}  CI[${lo:+.4f}, ${hi:+.4f}]")
    print(f"   per day      ${total / max(days, 1e-9):+.3f}")
    print(f"   total        ${total:+.2f}  win rate {wins / len(takes):.1%}")
    print(f"   worst / best ${min(pnl):+.2f} / ${max(pnl):+.2f}"
          f"  median ${statistics.median(pnl):+.4f}")

    # Dollars per take are size-weighted; the studies all report cents per contract, so
    # print both or the two can never be compared (this is the 025/029 lesson in another
    # costume -- the aggregation convention is doing the judging).
    counts = [float(t.get("count") or _clip_count(float(t.get("ask") or 0.0), settings))
              for t in takes]
    contracts = sum(counts)
    if contracts > 0:
        per_contract = [p / c * 100.0 for p, c in zip(pnl, counts) if c > 0]
        cl2 = [t["event_ticker"] for t, c in zip(takes, counts) if c > 0]
        m2, t2, lo2, hi2 = clustered_t(per_contract, cl2)
        print(f"   contracts    {contracts:.0f} total, sizes "
              f"{sorted(set(counts))[:6]}{'...' if len(set(counts)) > 6 else ''}")
        print(f"   per contract {m2:+.3f}c  t={t2:+.2f}  CI[{lo2:+.3f}c, {hi2:+.3f}c]"
              f"   (size-weighted: {total / contracts * 100:+.3f}c)")
        priced = [float(t.get("ask") or 0.0) for t in takes if t.get("ask")]
        if priced:
            print(f"   mean entry   {statistics.fmean(priced):.4f} over {len(priced)} takes")

    order = sorted(takes, key=lambda t: t.get("entry_ts") or 0)
    mid = len(order) // 2
    h1 = sum(float(t.get("pnl") or 0.0) for t in order[:mid])
    h2 = sum(float(t.get("pnl") or 0.0) for t in order[mid:])
    print(f"   halves       first ${h1:+.2f} / second ${h2:+.2f}")

    by_play: dict[str, list[float]] = {}
    by_reason: dict[str, int] = {}
    for take, value in zip(takes, pnl):
        by_play.setdefault(take.get("play") or "?", []).append(value)
        key = take.get("exit_reason") or "?"
        by_reason[key] = by_reason.get(key, 0) + 1
    print("   by play:")
    for play, values in sorted(by_play.items(), key=lambda kv: -sum(kv[1])):
        print(f"     {play:<16} n={len(values):>5} total=${sum(values):+8.2f}"
              f" mean=${statistics.fmean(values):+.4f}")
    print("   exits: " + "  ".join(f"{k}={v}" for k, v in
                                   sorted(by_reason.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

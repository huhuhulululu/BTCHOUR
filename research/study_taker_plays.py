"""Study 8 -- `swing_t` and `impulse_t` scored by driving the production code itself.

ADR 026 measured `swing_t` with a hand-written copy of the gate and the exit stack. That
is one paraphrase away from the thing that would actually trade, and 027 is what happens
when a paraphrase drifts. This script instead builds real `Market` / `SpotQuote` objects
out of the tape and calls `strategy.evaluate_swing_market`, `strategy.evaluate_impulse_market`
and `exits.evaluate_exit` -- so whatever the repo would do minute by minute is what gets
scored, including every gate nobody remembered to copy.

`impulse_t` has never been measured at all. It is the momentum twin of `swing_t`: the
side is forced to the impulse direction, the gap needed is 2pp instead of 8pp, and p only
has to clear 0.52. Section 10 of the backtest already found the impulse signal carries no
direction, so this is the play most likely to be paying for a signal that isn't there.

Both are default-off (`playbook=flex` + `impulse_taker=False`), so nothing here changes
what runs today. What it decides is whether ADR 017's hold-to-settlement treatment should
extend to them before anyone turns them on -- 026 left that to the user, with numbers for
only half the question.

    python3 research/study_taker_plays.py
    python3 research/study_taker_plays.py --play impulse_t --slice late
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.config import Settings  # noqa: E402
from btchour.exits import OpenPosition, evaluate_exit  # noqa: E402
from btchour.fees import exit_proceeds, taker_fee  # noqa: E402
from btchour.kalshi import Market  # noqa: E402
from btchour.model import SpotQuote  # noqa: E402
from btchour.strategy import (  # noqa: E402
    evaluate_impulse_market,
    evaluate_swing_market,
    hour_minute,
)
from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    Hour,
    liquidity_tier,
    load_hours,
    rung_reference_volume,
    sample_days,
)


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_market(hour: Hour, strike: float, quote, bar) -> Market:
    """A real `Market` off the tape, so production gates see what they expect."""
    return Market(
        ticker=f"{hour.event_ticker}-T{strike:.2f}",
        event_ticker=hour.event_ticker,
        title="",
        subtitle="",
        status="active",
        strike=strike,
        strike_type="greater",
        yes_bid=quote.yes_bid,
        yes_ask=quote.yes_ask,
        no_bid=quote.no_bid,
        no_ask=quote.no_ask,
        last=None,
        volume=quote.volume,
        open_interest=None,
        open_time=iso(hour.open_ts),
        close_time=iso(hour.close_ts),
        rules_primary="",
        result="",
        raw={},
    )


def as_spot(bar) -> SpotQuote:
    return SpotQuote(
        price=bar.spot,
        source="brti",
        annual_vol=bar.annual_vol,
        ts_ms=bar.ts * 1000,
        impulse=bar.impulse,
    )


def run_exit_stack(hour: Hour, entry_bar, strike: float, side: str, entry: float,
                   play: str, entry_p: float, settings: Settings) -> tuple[float, str]:
    """Cents and reason for one entry, walked forward through `exits.evaluate_exit`."""
    cost = entry + taker_fee(entry)
    position = OpenPosition(
        ticker=f"{hour.event_ticker}-T{strike:.2f}",
        event_ticker=hour.event_ticker,
        side=side,
        cost=cost,
        count=1.0,
        peak_bid=None,
        play=play,
        entry_p=entry_p,
    )
    for bar in hour.bars:
        if bar.ts <= entry_bar.ts:
            continue
        quote = bar.quotes.get(strike)
        if quote is None:
            continue
        market = as_market(hour, strike, quote, bar)
        seconds = float(hour.close_ts - bar.ts)
        from btchour.model import digital_prob, effective_vol

        vol = effective_vol(bar.annual_vol, settings.annual_vol)
        p_yes = digital_prob(bar.spot, strike, seconds, vol,
                             minute=hour_minute(market, seconds))
        model_p = p_yes if side == "yes" else 1.0 - p_yes
        position = OpenPosition(
            **{**position.__dict__, "held_seconds": float(bar.ts - entry_bar.ts)}
        )
        decision = evaluate_exit(position, market, model_p, seconds, settings)
        position = OpenPosition(**{**position.__dict__, "peak_bid": decision.peak_bid})
        if decision.action is not None:
            proceeds, _fee = exit_proceeds(decision.action.price)
            return (proceeds - cost) * 100.0, decision.action.reason
    won = hour.won(strike, side)
    return ((1.0 if won else 0.0) - cost) * 100.0, "settle"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--play", default="both", choices=["both", "swing_t", "impulse_t"])
    ap.add_argument("--slip", type=float, default=0.0,
                    help="cents of adverse entry slippage to charge each fill")
    args = ap.parse_args(argv)

    settings = Settings()
    swing_settings = Settings(playbook="swing")
    impulse_settings = Settings(playbook="flex", impulse_taker=True)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'}"
          f" slip={args.slip:.2f}c  (gates and exits are the production functions)")

    plays = ["swing_t", "impulse_t"] if args.play == "both" else [args.play]
    out: dict[str, Bucket] = {}
    reasons: dict[str, dict[str, int]] = {}
    for play in plays:
        for label in ("hold", "stack"):
            out[f"{play}/{label}"] = Bucket(f"{play} -> {'settlement' if label == 'hold' else 'exit stack'}")
        for tier in ("liquid", "cold"):
            out[f"{play}/hold/{tier}"] = Bucket(f"{play} -> settlement, {tier} rungs")
        reasons[play] = {}

    seen: set = set()
    for hour in hours:
        for bar in hour.bars:
            now = datetime.fromtimestamp(bar.ts, timezone.utc)
            reference = rung_reference_volume(bar)
            for strike, quote in bar.quotes.items():
                if quote.yes_bid is None or quote.yes_ask is None or quote.volume < 1.0:
                    continue
                market = as_market(hour, strike, quote, bar)
                spot = as_spot(bar)
                for play in plays:
                    if play == "swing_t":
                        rows = evaluate_swing_market(market, spot, swing_settings, now=now)
                        use = swing_settings
                    else:
                        rows = evaluate_impulse_market(market, spot, impulse_settings, now=now)
                        use = impulse_settings
                    for row in rows:
                        key = (hour.event_ticker, strike, row.side, play)
                        if key in seen:
                            continue
                        seen.add(key)
                        won = hour.won(strike, row.side)
                        if won is None:
                            continue
                        entry = row.ask + args.slip / 100.0
                        if not (0.0 < entry < 1.0):
                            continue
                        cost = entry + taker_fee(entry)
                        held = ((1.0 if won else 0.0) - cost) * 100.0
                        out[f"{play}/hold"].add(hour.event_ticker, held, won, entry, hour.close_ts)
                        tier = "cold" if liquidity_tier(quote.volume, reference) == "cold" else "liquid"
                        out[f"{play}/hold/{tier}"].add(hour.event_ticker, held, won, entry, hour.close_ts)
                        cents, reason = run_exit_stack(
                            hour, bar, strike, row.side, entry, play, row.model_p, use
                        )
                        out[f"{play}/stack"].add(hour.event_ticker, cents, won, entry, hour.close_ts)
                        reasons[play][reason] = reasons[play].get(reason, 0) + 1

    for play in plays:
        print(f"\n## {play}")
        for suffix in ("hold", "stack", "hold/liquid", "hold/cold"):
            bucket = out[f"{play}/{suffix}"]
            if len(bucket):
                print("   " + bucket.result(days).row())
        hold = out[f"{play}/hold"].result(days)
        stack = out[f"{play}/stack"].result(days)
        if hold.n:
            print(f"   exit stack costs {stack.mean_cents - hold.mean_cents:+.2f}c per contract")
            order = sorted(reasons[play].items(), key=lambda kv: -kv[1])
            print("   exits: " + "  ".join(f"{k}={v}" for k, v in order))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

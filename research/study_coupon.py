"""Study 3 -- what the current default (`impulse_wait` 25c coupon rest) actually earns.

ADR 015 states the open question in words: resting $0.25 under a 32-42c book asks the
market for an 8c adverse discount before it will fill, which selects fills that were
already going the wrong way. This measures it on a year-scale hourly tape instead of on
seven live tickets.

For each hour it hangs the coupon exactly as `strategy.coupon_*` does -- tape side,
|impulse| >= $100, ladder within $600, ask inside the 32-42c band (YES from 28c) -- and
fills only when the side's ask actually trades down to the rest, with the same-way
impulse still on. Filled coupons are then scored three ways:

  hold      held to settlement (the honest denominator)
  clip      the live exit stack: 10-50% band, -80% wait stop, 8-minute scratch
  taker     what the same signal would have earned buying at the 32-42c ask instead

    python3 research/study_coupon.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.fees import lock_exit_price, round_trip_roi  # noqa: E402
from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    Hour,
    load_hours,
    net_cents,
    sample_days,
)

REST = 0.25
IMPULSE_MIN = 100.0
COUPON_HI = 0.42
NO_LO, YES_LO = 0.32, 0.28
MAX_DISTANCE = 600.0
MAX_RESTS = 3
WAIT_STOP = 0.80
SCRATCH_SECONDS = 480.0
TARGET, CAP = 0.10, 0.50


def tape_side(impulse: float) -> str:
    return "yes" if impulse >= IMPULSE_MIN else "no"


def rest_ready(side: str, impulse: float) -> bool:
    if side == "no":
        return impulse < 0 and abs(impulse) >= IMPULSE_MIN
    return impulse > 0 and abs(impulse) >= IMPULSE_MIN


FILL_TICKS = 0  # 0 = quote touch (upper bound); 1 = the ask traded a tick THROUGH the rest


def ask_low(quote, side: str) -> float | None:
    """Lowest ask printed inside the minute on `side`, shifted by the fill convention.

    "The candle touched my price" is an upper bound on maker fills, and treating it as a
    fill rate is a named mistake in the 15m repo's LESSONS. Kalshi prices sit on a 1c
    grid, so a strict cross means `ask_low <= rest - 1 tick`; shifting the observed low
    up by FILL_TICKS cents expresses that without touching the comparison below.
    """
    if side == "yes":
        value = quote.yes_ask_low
    else:
        value = None if quote.yes_bid_high is None else round(1.0 - quote.yes_bid_high, 4)
    return None if value is None else value + 0.01 * FILL_TICKS


def run(hours: list[Hour]) -> dict[str, Bucket]:
    out = {
        "hold": Bucket("coupon fill -> settlement"),
        "clip": Bucket("coupon fill -> live exit stack"),
        "taker": Bucket("same signal, taker at the 32-42c ask"),
    }
    hung = filled = 0
    for hour in hours:
        rests: list[dict] = []
        seen: set[tuple[float, str]] = set()
        for bar in hour.bars:
            side = tape_side(bar.impulse)

            # 1. fill / cancel what is already resting
            for rest in list(rests):
                if rest["done"]:
                    continue
                quote = bar.quotes.get(rest["strike"])
                if quote is None:
                    continue
                low = ask_low(quote, rest["side"])
                same_way = rest_ready(rest["side"], bar.impulse)
                if low is not None and low <= REST + 1e-9 and same_way:
                    rest["done"] = True
                    rest["fill_ts"] = bar.ts
                    filled += 1
                    won = hour.won(rest["strike"], rest["side"])
                    if won is None:
                        continue
                    out["hold"].add(hour.event_ticker, (1.0 if won else 0.0) * 100 - REST * 100,
                                    won, REST, hour.close_ts)
                    exit_cents = replay_exit_stack(hour, bar.ts, rest["strike"], rest["side"], won)
                    out["clip"].add(hour.event_ticker, exit_cents, won, REST, hour.close_ts)
                    continue
                # tape flipped hard the other way -> pull the rest (005)
                if rest["side"] == "no" and bar.impulse >= IMPULSE_MIN:
                    rest["done"] = True
                elif rest["side"] == "yes" and bar.impulse <= -IMPULSE_MIN:
                    rest["done"] = True

            live = sum(1 for r in rests if not r["done"])
            if live >= MAX_RESTS or not rest_ready(side, bar.impulse):
                continue

            # 2. hang new coupons
            for strike, quote in sorted(bar.quotes.items(), key=lambda kv: abs(kv[1].strike - bar.spot)):
                if live >= MAX_RESTS:
                    break
                if (strike, side) in seen:
                    continue
                if abs(bar.spot - strike) > MAX_DISTANCE:
                    continue
                ask = quote.ask(side)
                lo = YES_LO if side == "yes" else NO_LO
                if ask is None or not (lo <= ask <= COUPON_HI):
                    continue
                seen.add((strike, side))
                rests.append({"strike": strike, "side": side, "ts": bar.ts, "done": False})
                hung += 1
                live += 1
                won = hour.won(strike, side)
                if won is not None:
                    out["taker"].add(hour.event_ticker, net_cents(ask, won), won, ask, hour.close_ts)
    out["_hung"] = hung  # type: ignore[assignment]
    out["_filled"] = filled  # type: ignore[assignment]
    return out


def replay_exit_stack(hour: Hour, fill_ts: int, strike: float, side: str, won: bool) -> float:
    """Cents for one filled coupon under the live exit stack (maker in, taker out)."""
    cost = REST  # maker fee is 0 on this series
    floor_price = lock_exit_price(cost, 1.0, TARGET)
    cap_price = lock_exit_price(cost, 1.0, CAP)
    peak: float | None = None
    for bar in hour.bars:
        if bar.ts <= fill_ts:
            continue
        quote = bar.quotes.get(strike)
        if quote is None:
            continue
        bid = quote.bid(side)
        if bid is None:
            continue
        if cap_price is not None and bid + 1e-12 >= cap_price:
            return _exit_cents(cost, cap_price)
        roi = round_trip_roi(cost, bid)
        if roi + 1e-12 <= -WAIT_STOP:
            return _exit_cents(cost, bid)
        peak = bid if peak is None else max(peak, bid)
        held = bar.ts - fill_ts
        if held >= SCRATCH_SECONDS and round_trip_roi(cost, peak) + 1e-12 < TARGET:
            return _exit_cents(cost, bid)
        if floor_price is not None and peak + 1e-12 >= floor_price and peak - bid + 1e-12 >= 0.04:
            return _exit_cents(cost, bid)
    return ((1.0 if won else 0.0) - cost) * 100.0


def _exit_cents(cost: float, price: float) -> float:
    from btchour.fees import exit_proceeds

    proceeds, _ = exit_proceeds(price)
    return (proceeds - cost) * 100.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"],
                    help="calendar half: fit on early, validate on late")
    ap.add_argument("--fill-ticks", type=int, default=0,
                    help="0 = quote touch (upper bound); 1 = strict cross one tick through")
    args = ap.parse_args(argv)

    global FILL_TICKS
    FILL_TICKS = args.fill_ticks

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    out = run(hours)
    hung, filled = out.pop("_hung"), out.pop("_filled")  # type: ignore[arg-type]
    convention = "touch (upper bound)" if not FILL_TICKS else f"strict cross, {FILL_TICKS} tick(s) through"
    print(f"# hours={len(hours)} days={days:.1f}  fill convention: {convention}")
    print(f"# coupons hung={hung}  filled={filled}  fill rate={filled / max(hung, 1):.1%}")
    for bucket in out.values():
        if len(bucket):
            print("   " + bucket.result(days).row())
        else:
            print(f"   {bucket.label:<34} n=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

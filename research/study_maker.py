"""Study 6 -- maker on the favourite, where the fee is zero.

The calibration map says the hourly ladder is roughly *fair* on the favourite side
(+0.2 to +1.6pp) and 2-3pp expensive on the longshot. On the favourite, +0.7pp of gross
edge is almost exactly the taker fee at that price (0.07·P·(1−P) ≈ 0.84c at 0.86), which
is why buying the favourite as a taker nets ~0. `KXBTCD` maker fee is **0**
(`catalog/rules/fees.md`), so the same fill as a maker keeps the whole thing, plus the
spread.

Against that stands adverse selection: a resting bid fills when the tape is coming at
you. The 15m repo measured 5.5-6.3pp of it on signal-conditioned rests
(phase13 `adverse_selection`) and it killed the maker version there.

Two fill conventions, both reported, because "candle touched my price" is an upper bound
and treating it as a fill rate is a listed mistake in that repo's LESSONS:

  touch   the side's ask reached the rest (ask_low <= rest) -- optimistic
  cross   the ask traded strictly through it (ask_low < rest) -- what a back-of-queue
          order can actually expect

    python3 research/study_maker.py --slice early
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.hourly_lab import (  # noqa: E402
    Bucket,
    DEFAULT_DB,
    Hour,
    cushion,
    load_hours,
    sample_days,
)

BANDS = [(0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 0.99)]


def rest_ask_low(quote, side: str) -> float | None:
    """Lowest ask printed inside the minute on `side` (NO ask = 1 - YES bid)."""
    if side == "yes":
        return quote.yes_ask_low
    return None if quote.yes_bid_high is None else round(1.0 - quote.yes_bid_high, 4)


def run(hours: list[Hour], offset: int, min_cushion: float, max_per_event: int):
    """Rest `offset` ticks below the favourite's ask; hold every fill to settlement."""
    out: dict[tuple, Bucket] = {}
    hung = {"touch": 0, "cross": 0}
    for hour in hours:
        rests: list[dict] = []
        seen: set[tuple] = set()
        for bar in hour.bars:
            for rest in rests:
                if rest["filled"]:
                    continue
                quote = bar.quotes.get(rest["strike"])
                if quote is None:
                    continue
                low = rest_ask_low(quote, rest["side"])
                if low is None:
                    continue
                price = rest["price"]
                won = hour.won(rest["strike"], rest["side"])
                if won is None:
                    continue
                for mode, hit in (("touch", low <= price + 1e-9), ("cross", low < price - 1e-9)):
                    if rest[mode] or not hit:
                        continue
                    rest[mode] = True
                    # maker fee is 0 on KXBTCD
                    cents = ((1.0 if won else 0.0) - price) * 100.0
                    key = (rest["band"], mode)
                    out.setdefault(
                        key,
                        Bucket(f"rest -{offset}c in {rest['band'][0]:.2f}-{rest['band'][1]:.2f} [{mode}]"),
                    ).add(hour.event_ticker, cents, won, price, hour.close_ts)
                rest["filled"] = rest["touch"] and rest["cross"]

            if len([r for r in rests if not r["filled"]]) >= max_per_event:
                continue
            if bar.seconds_left < 120:
                continue
            for strike, quote in sorted(bar.quotes.items(), key=lambda kv: -cushion(bar, kv[0])):
                if quote.volume < 1.0:
                    continue
                side = "yes" if bar.spot > strike else "no"
                if (strike, side) in seen:
                    continue
                if min_cushion and cushion(bar, strike) < min_cushion:
                    continue
                ask = quote.ask(side)
                if ask is None:
                    continue
                price = round(ask - 0.01 * offset, 4)
                band = next((b for b in BANDS if b[0] <= price < b[1]), None)
                if band is None:
                    continue
                seen.add((strike, side))
                rests.append(
                    {"strike": strike, "side": side, "price": price, "band": band,
                     "touch": False, "cross": False, "filled": False}
                )
                hung["touch"] += 1
                hung["cross"] += 1
                if len([r for r in rests if not r["filled"]]) >= max_per_event:
                    break
    return out, hung


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="", choices=["", "early", "late"])
    ap.add_argument("--offsets", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--cushion", type=float, default=0.0)
    ap.add_argument("--max-per-event", type=int, default=3)
    args = ap.parse_args(argv)

    hours = load_hours(args.db, limit=args.limit or None, slice_half=args.slice)
    days = sample_days(hours)
    print(f"# hours={len(hours)} days={days:.1f} slice={args.slice or 'full'} "
          f"cushion>={args.cushion}  maker fee = 0, held to settlement")

    for offset in args.offsets:
        out, hung = run(hours, offset, args.cushion, args.max_per_event)
        print(f"\n## rest {offset} tick(s) under the favourite ask   (hung={hung['touch']})")
        for band in BANDS:
            for mode in ("touch", "cross"):
                bucket = out.get((band, mode))
                if bucket is None or len(bucket) < 150:
                    continue
                fill = len(bucket) / max(hung[mode], 1)
                print("   " + bucket.result(days).row() + f" fill={fill:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

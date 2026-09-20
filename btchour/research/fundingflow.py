"""Price pressure around perpetual funding settlement stamps.

A different kind of mechanism from anything 021 ranked. Every mechanism tested
in 022-024 was a *risk premium*: somebody is paid for bearing something. This
one is **scheduled flow**. Perpetual funding settles at fixed, published,
known-in-advance times. A trader who does not want to pay funding closes
before the stamp and reopens after; one who wants to receive it opens before.
That is a predictable order-flow imbalance at a predictable instant, and
predictable flow is the cleanest thing a liquidity provider can be paid for.

The shape of a real effect: when funding is strongly positive (longs pay), the
crowd that wants to avoid paying is long, so selling pressure builds into the
stamp and reverses after it. The signed prediction is therefore

    return into the stamp  has the opposite sign to the funding rate
    return out of the stamp has the same sign as the funding rate

and the trade is to take the other side of the exiting crowd. Crucially the
prediction is *signed by a published number*, which is what makes this a
mechanism rather than a seasonality search: there is no free parameter to
choose and no sign to fit.

**That prediction was tested and it is wrong, and something else was found in
its place.** Decision 025 measured `signed_into_bp` at -16.94bp on thin alts
conditioned on a large lagged funding rate: the price moves *with* the funding
sign into the stamp, not against it, so there is no exiting crowd to take the
other side of. What is there instead is short-horizon continuation --
an extreme funding rate predicts same-direction returns over the next hour --
which is a momentum effect that happens to be indexed by funding, not the
scheduled-flow mechanism this module set out to test. The arithmetic here is
unchanged and still correct; only the story is different, and `signed_into_bp`
is retained precisely because it is the field that falsified the story.

What would make it not a business even if the pattern is real, and is
therefore measured alongside it:

* **Funding itself.** Holding through the stamp means paying or receiving
  funding, and 023 measured that term at up to -7.36%/yr on the wrong side of
  a crowded book. A strategy that captures a 2bp reversal and pays a 4bp
  funding charge is a loss. So the funding actually settled is charged.
* **The spread.** 023 measured Binance perp effective spread at a median of
  3.99bp, which is the cost of two crossings. A reversal smaller than that is
  not reachable.
* **The independent unit is the symbol-day, not the stamp.** Most perps settle
  every four hours, so a day holds six stamps riding one price path, and all
  twelve symbols ride the same crypto beta. Bootstrapping stamps would inflate
  the sample by roughly six and the cross-section by twelve more.

All returns here are in basis points of the price at the reference instant, so
they compare directly against the spread and the funding charge.
"""

from __future__ import annotations

from dataclasses import dataclass

MINUTE_MS = 60_000
HOUR_MS = 3_600_000

# Cost of crossing the spread twice, in bp of notional. The median effective
# spread on Binance USDT-M perps measured 3.99bp in decision 023; a round trip
# pays half of it on each of two crossings, so the spread cost is that number.
ROUND_TRIP_SPREAD_BP = 3.99
# Published USDT-M taker fee, per side, in bp.
TAKER_FEE_BP = 5.0


@dataclass(frozen=True)
class Stamp:
    """One funding settlement on one symbol, with the price path around it."""

    symbol: str
    ts: int  # the settlement instant
    rate: float  # the rate the position was allowed to know, as a decimal
    settled_rate: float  # the rate actually settled at `ts`
    price_before: float  # at ts - lead
    price_at: float  # at ts
    price_after: float  # at ts + lag
    lead_ms: int
    lag_ms: int

    @property
    def into_bp(self) -> float:
        """Return over the run-up into the stamp, in bp."""
        if self.price_before <= 0:
            return 0.0
        return (self.price_at / self.price_before - 1.0) * 10_000.0

    @property
    def out_bp(self) -> float:
        """Return over the window after the stamp, in bp."""
        if self.price_at <= 0:
            return 0.0
        return (self.price_after / self.price_at - 1.0) * 10_000.0

    @property
    def rate_bp(self) -> float:
        return self.rate * 10_000.0

    @property
    def signed_into_bp(self) -> float:
        """`into_bp` oriented so a positive number supports the mechanism.

        The claim is that the run-up runs *against* the funding sign, so
        multiplying by `-sign(rate)` makes a confirming observation positive.
        A zero rate carries no prediction and contributes nothing.
        """
        s = (self.rate > 0) - (self.rate < 0)
        return -s * self.into_bp

    @property
    def signed_out_bp(self) -> float:
        """`out_bp` oriented so a positive number supports the mechanism."""
        s = (self.rate > 0) - (self.rate < 0)
        return s * self.out_bp

    def reversal_bp(self, *, pay_funding: bool = True) -> float:
        """The tradeable version: take the crowd's other side into the stamp,
        unwind after it.

        The position is opened at `ts - lead` on the side the exiting crowd is
        selling, held through the stamp and closed at `ts + lag`. Holding
        through the stamp means the funding is settled against the position,
        and the position's side is the opposite of the crowd's, so a positive
        funding rate is *received*. Both crossings are charged.
        """
        s = (self.rate > 0) - (self.rate < 0)
        if s == 0:
            return 0.0
        # Long when funding is positive: the crowd that pays is long and is
        # selling into the stamp, so the other side is the buyer, and that
        # buyer then receives the funding.
        gross = s * ((self.price_after / self.price_before - 1.0) * 10_000.0)
        # The side was chosen from `rate`, but what settles is `settled_rate`,
        # and the two can disagree in sign. Receiving is not guaranteed: a
        # position placed on a stale signal pays when the rate flips.
        funding = s * self.settled_rate * 10_000.0 if pay_funding else 0.0
        return gross + funding - ROUND_TRIP_SPREAD_BP - 2 * TAKER_FEE_BP


def build_stamps(
    symbol: str,
    closes: dict[int, float],
    funding: list,
    *,
    lead_ms: int = 30 * MINUTE_MS,
    lag_ms: int = 30 * MINUTE_MS,
    signal: str = "lagged",
) -> list[Stamp]:
    """One `Stamp` per settlement that has all three prices on the minute grid.

    A stamp missing any of the three prices is dropped whole rather than
    filled from a neighbouring minute: an interpolated endpoint would put an
    invented price at exactly the instant the effect is supposed to live at.

    `signal` decides which rate the position is allowed to know about, and it
    is the difference between a result and an artifact.

    * `"lagged"` (the default) uses the **previous** settlement's realized
      rate. That number was published hours before the window opens, so a
      position sized on it is implementable.
    * `"realized"` uses the rate that settles at `ts`. Binance computes it as
      a time-weighted average of the premium index over the interval *ending*
      at `ts`, so it is not final until the settlement instant and it embeds
      the price action of the last thirty minutes -- which is the first half
      of the trading window. Conditioning on it therefore selects entries
      using the move that follows them. It is kept only so the size of that
      artifact can be shown.

    The settled rate is carried on the stamp either way, because the funding
    actually paid is the realized one no matter which rate chose the trade.
    """
    if signal not in ("lagged", "realized"):
        raise ValueError("signal must be 'lagged' or 'realized'")
    ordered = sorted(funding, key=lambda f: int(f.ts))
    prev_rate: dict[int, float] = {}
    for a, b in zip(ordered, ordered[1:]):
        prev_rate[int(b.ts)] = float(a.rate)
    out: list[Stamp] = []
    for f in ordered:
        ts = int(f.ts)
        if signal == "lagged":
            if ts not in prev_rate:
                continue  # no earlier settlement to have known about
            chosen = prev_rate[ts]
        else:
            chosen = float(f.rate)
        # Bar open times, so snap each instant down to its minute.
        a = (ts - lead_ms) // MINUTE_MS * MINUTE_MS
        b = ts // MINUTE_MS * MINUTE_MS
        c = (ts + lag_ms) // MINUTE_MS * MINUTE_MS
        pa, pb, pc = closes.get(a), closes.get(b), closes.get(c)
        if not pa or not pb or not pc:
            continue
        out.append(
            Stamp(
                symbol=symbol,
                ts=ts,
                rate=chosen,
                settled_rate=float(f.rate),
                price_before=pa,
                price_at=pb,
                price_after=pc,
                lead_ms=lead_ms,
                lag_ms=lag_ms,
            )
        )
    return out


def by_symbol_day(stamps: list[Stamp]) -> list[list[Stamp]]:
    """Group into the unit that gets bootstrapped."""
    DAY = 86_400_000
    buckets: dict[tuple[str, int], list[Stamp]] = {}
    for s in stamps:
        buckets.setdefault((s.symbol, s.ts // DAY), []).append(s)
    return list(buckets.values())


def summarize(
    stamps: list[Stamp],
    *,
    label: str = "",
    min_rate_bp: float = 0.0,
) -> dict:
    """Bootstrap symbol-days, not stamps.

    `min_rate_bp` restricts to settlements where the funding was large enough
    for anyone to bother avoiding it. That is a real conditioning variable
    rather than a fitted one -- the mechanism says nothing at all when funding
    is zero -- but it is a knob, so the summary reports the threshold it used
    and the caller is expected to show the whole curve.
    """
    from .metrics import cluster_ci, t_stat, _mean, _stdev
    from .carry import _concentration

    stamps = [s for s in stamps if abs(s.rate_bp) >= min_rate_bp]
    if not stamps:
        return {"label": label, "n": 0}
    groups = by_symbol_day(stamps)
    into = [[s.signed_into_bp for s in g] for g in groups]
    outw = [[s.signed_out_bp for s in g] for g in groups]
    rev = [[s.reversal_bp() for s in g] for g in groups]
    rev_nf = [[s.reversal_bp(pay_funding=False) for s in g] for g in groups]
    flat_into = [v for g in into for v in g]
    flat_out = [v for g in outw for v in g]
    flat_rev = [v for g in rev for v in g]
    day_means = [_mean(g) for g in rev]
    return {
        "label": label,
        "min_rate_bp": min_rate_bp,
        "n": len(stamps),
        "n_symbol_days": len(groups),
        "n_symbols": len({s.symbol for s in stamps}),
        "abs_rate_bp_mean": _mean([abs(s.rate_bp) for s in stamps]),
        "signed_into_bp": _mean(flat_into),
        "signed_into_ci": cluster_ci(into, draws=4000),
        "signed_out_bp": _mean(flat_out),
        "signed_out_ci": cluster_ci(outw, draws=4000),
        "reversal_bp": _mean(flat_rev),
        "reversal_ci": cluster_ci(rev, draws=4000),
        "reversal_t_by_day": t_stat(day_means),
        "reversal_bp_no_funding": _mean([v for g in rev_nf for v in g]),
        "cost_floor_bp": ROUND_TRIP_SPREAD_BP + 2 * TAKER_FEE_BP,
        "share_stamps_confirming": (
            sum(1 for v in flat_into if v > 0) / len(flat_into)
        ),
        "share_trades_positive": sum(1 for v in flat_rev if v > 0) / len(flat_rev),
        "top5_profit_share": _concentration(flat_rev, 5),
        "worst_bp": min(flat_rev),
        "best_bp": max(flat_rev),
        "sd_bp": _stdev(day_means),
    }

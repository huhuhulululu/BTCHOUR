"""Realized maker / taker P&L from prints that actually happened.

Every earlier instrument in this repo had to invent fills. The replay wick
model invented them out of a candle's extreme, the maker scan invented them
out of "the book touched our price", and both inventions paid us money the
exchange never would have. `research/maker.py` tightened the invention
(`through` instead of `touch`) and the edge it was measuring shrank by three
quarters.

This module stops inventing. Kalshi publishes every print with the taker's
side, so for each print somebody really did rest an order there and really
did get filled. The maker's side, price and size are all determined; the
settlement result is published too. So the P&L of the resting side is
arithmetic, with no fill rule, no queue assumption and no model of ours
anywhere in it.

What it measures is the *pool*: what the whole population of makers earned
per contract in a market. That is an upper bound on what an entrant quoting
blindly could earn -- some of those resting orders knew something -- but it
is a real bound in the right direction. A market whose maker pool is
negative cannot be made profitably by anybody, us included.

Bucketing is by the maker's own holding cost, never by the quoted price:
resting an offer at 0.10 is economically a 0.90 purchase of the other side,
and sorting those two together is what made decision 018 wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from ..fees import TAKER_COEFF, ceil_centicent
from .metrics import _mean, _stdev


@dataclass(frozen=True)
class Print:
    """One trade as the exchange reported it."""

    ticker: str
    event: str
    series: str
    ts: str
    yes_price: float
    count: float
    taker_side: str  # "yes" or "no" -- which side the aggressor bought


@dataclass(frozen=True)
class Leg:
    """The resting side of one print, priced to settlement."""

    ticker: str
    event: str
    series: str
    ts: str
    count: float
    maker_cost: float  # what the maker paid per contract for the side it got
    maker_side: str  # "yes" or "no"
    maker_pnl: float  # per contract, after maker fees
    taker_pnl: float  # per contract, after taker fees


def taker_fee_per_contract(yes_price: float, multiplier: float = 1.0) -> float:
    """Kalshi's quadratic taker fee, per single contract.

    The published formula is `ceil(0.07 * C * P * (1-P))` rounded up to the
    cent, which is a per-order rounding, not a per-contract one. Dividing a
    ceiling by the order size would overstate the fee on small clips and
    understate nothing, so the per-contract figure here is the unrounded
    rate; the rounding only ever costs a trader more.
    """
    p = max(0.0, min(1.0, yes_price))
    return TAKER_COEFF * multiplier * p * (1.0 - p)


def settle_leg(
    print_: Print,
    result: str,
    *,
    maker_fee_per_contract: float = 0.0,
    taker_multiplier: float = 1.0,
) -> Leg | None:
    """Price one print's two sides against the published settlement.

    `result` is the market's own `result` field: "yes" or "no". Anything
    else (void, unsettled, an empty string) returns None rather than a
    guess -- a voided market is not a zero, it is an absence.
    """
    if result not in ("yes", "no"):
        return None
    yp = print_.yes_price
    if not (0.0 <= yp <= 1.0):
        return None
    if print_.taker_side == "yes":
        # The aggressor bought YES, so the resting order sold YES: it is long
        # NO, and it paid 1 - yes_price for it.
        maker_side = "no"
        maker_cost = 1.0 - yp
        maker_gross = (1.0 if result == "no" else 0.0) - maker_cost
    elif print_.taker_side == "no":
        maker_side = "yes"
        maker_cost = yp
        maker_gross = (1.0 if result == "yes" else 0.0) - maker_cost
    else:
        return None
    fee = taker_fee_per_contract(yp, taker_multiplier)
    return Leg(
        ticker=print_.ticker,
        event=print_.event,
        series=print_.series,
        ts=print_.ts,
        count=print_.count,
        maker_cost=maker_cost,
        maker_side=maker_side,
        maker_pnl=maker_gross - maker_fee_per_contract,
        taker_pnl=-maker_gross - fee,
    )


def _weighted(values: list[tuple[float, float]]) -> float:
    """Size-weighted mean of (value, weight) pairs."""
    total = sum(w for _, w in values)
    if total <= 0:
        return 0.0
    return sum(v * w for v, w in values) / total


@dataclass(frozen=True)
class PoolSummary:
    series: str
    prints: int
    contracts: float
    notional: float
    events: int
    maker_cents_per_contract: float
    maker_ci95: tuple[float, float]
    maker_t: float
    taker_cents_per_contract: float
    maker_total_dollars: float

    def as_dict(self) -> dict:
        return asdict(self)


def _cluster_ratio_ci(
    pairs: list[tuple[str, float, float]],
    *,
    alpha: float = 0.05,
    draws: int = 3000,
    seed: int = 11,
) -> tuple[float, float, float]:
    """Interval for a size-weighted mean, resampling settling events.

    The statistic is a ratio of sums (total maker dollars over total
    contracts), so the bootstrap has to resample whole events and re-form the
    ratio; resampling prints would treat 300,000 contracts riding one BTC
    hour as 300,000 independent draws. Returns (lo, hi, t) with t built from
    the same event-level resampling, so the two never disagree about whether
    zero is inside.
    """
    import random as _random

    totals: dict[str, list[float]] = {}
    for event, pnl, size in pairs:
        row = totals.setdefault(event, [0.0, 0.0])
        row[0] += pnl
        row[1] += size
    rows = [(p, s) for p, s in totals.values() if s > 0]
    if len(rows) < 2:
        return (0.0, 0.0, 0.0)
    rng = _random.Random(seed)
    n = len(rows)
    point = sum(p for p, _ in rows) / sum(s for _, s in rows)
    draws_out = []
    for _ in range(draws):
        num = 0.0
        den = 0.0
        for _ in range(n):
            p, s = rows[rng.randrange(n)]
            num += p
            den += s
        if den > 0:
            draws_out.append(num / den)
    if not draws_out:
        return (point, point, 0.0)
    draws_out.sort()
    lo = draws_out[max(0, int(len(draws_out) * alpha / 2) - 1)]
    hi = draws_out[min(len(draws_out) - 1, int(len(draws_out) * (1 - alpha / 2)))]
    se = _stdev(draws_out)
    return (lo, hi, (point / se) if se > 0 else 0.0)


def summarize_pool(series: str, legs: list[Leg]) -> PoolSummary:
    """Per-contract P&L of the resting side, size-weighted, clustered by event."""
    if not legs:
        return PoolSummary(series, 0, 0.0, 0.0, 0, 0.0, (0.0, 0.0), 0.0, 0.0, 0.0)
    contracts = sum(leg.count for leg in legs)
    notional = sum(leg.count * leg.maker_cost for leg in legs)
    maker = _weighted([(leg.maker_pnl, leg.count) for leg in legs])
    taker = _weighted([(leg.taker_pnl, leg.count) for leg in legs])
    lo, hi, tstat = _cluster_ratio_ci(
        [(leg.event, leg.maker_pnl * leg.count * 100.0, leg.count) for leg in legs]
    )
    return PoolSummary(
        series=series,
        prints=len(legs),
        contracts=contracts,
        notional=notional,
        events=len({leg.event for leg in legs}),
        maker_cents_per_contract=maker * 100.0,
        maker_ci95=(lo, hi),
        maker_t=tstat,
        taker_cents_per_contract=taker * 100.0,
        maker_total_dollars=sum(leg.maker_pnl * leg.count for leg in legs),
    )


COST_BUCKETS = ((0.0, 0.05), (0.05, 0.15), (0.15, 0.35), (0.35, 0.65), (0.65, 0.85), (0.85, 0.95), (0.95, 1.0))


def by_cost_bucket(legs: list[Leg]) -> list[dict]:
    """Split the pool by what the maker paid, not by what it quoted."""
    out = []
    for lo, hi in COST_BUCKETS:
        rows = [leg for leg in legs if lo <= leg.maker_cost < hi or (hi == 1.0 and leg.maker_cost == 1.0)]
        if not rows:
            continue
        summary = summarize_pool(f"{lo:.2f}-{hi:.2f}", rows)
        out.append({"bucket": f"{lo:.2f}-{hi:.2f}", **summary.as_dict()})
    return out


# --- markout ---------------------------------------------------------------
#
# The pool above prices every print to settlement, which answers "what does
# quoting and holding earn". That is not the market-making business: a maker
# quotes, fills, and flattens, and its P&L is the spread it captured minus
# how far the price moved against it before it could get out. Priced to
# settlement, one in-play tennis match can carry 20% of a fortnight's total,
# so the number is mostly about who won the match.
#
# Markout asks the narrower question against the same real prints: where did
# the market trade shortly after we were filled? The reference price is the
# size-weighted average of the prints in the next `horizon` seconds -- real
# trades again, not a quote we imagine was there. Bid-ask bounce makes that
# average sit near the middle of the book, so a maker filled on its offer
# starts half a spread ahead and gives back whatever the flow knew.

def _epoch(ts: str) -> float:
    from datetime import datetime, timezone

    text = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return datetime.fromisoformat(text[:19] + "+00:00").timestamp()


@dataclass(frozen=True)
class Markout:
    event: str
    ticker: str
    count: float
    maker_cost: float
    cents: float
    burst: float  # contracts in the sweep this print belonged to


def markouts(prints: list[Print], *, horizon: float = 60.0) -> list[Markout]:
    """Per-contract maker markout over `horizon` seconds of later prints.

    Prints with nothing after them inside the horizon are dropped rather than
    marked at their own price: a fill with no follow-on trade has no reference,
    and scoring it flat would quietly credit the maker with the full spread on
    every quiet patch and on every market's last minute.
    """
    rows = sorted(prints, key=lambda p: (p.ticker, _epoch(p.ts)))
    out: list[Markout] = []
    start = 0
    while start < len(rows):
        stop = start
        while stop < len(rows) and rows[stop].ticker == rows[start].ticker:
            stop += 1
        chunk = rows[start:stop]
        times = [_epoch(p.ts) for p in chunk]
        burst = _burst_sizes(chunk, times)
        # Prefix sums let the horizon window slide instead of rescanning.
        notional = [0.0]
        size = [0.0]
        for p in chunk:
            notional.append(notional[-1] + p.yes_price * p.count)
            size.append(size[-1] + p.count)
        import bisect

        for i, p in enumerate(chunk):
            hi = bisect.bisect_right(times, times[i] + horizon)
            lo = bisect.bisect_right(times, times[i])
            if hi <= lo:
                continue
            weight = size[hi] - size[lo]
            if weight <= 0:
                continue
            future = (notional[hi] - notional[lo]) / weight
            if p.taker_side == "yes":
                # Maker is short YES at p.yes_price, i.e. long NO at 1 - price.
                cents = (p.yes_price - future) * 100.0
                cost = 1.0 - p.yes_price
            elif p.taker_side == "no":
                cents = (future - p.yes_price) * 100.0
                cost = p.yes_price
            else:
                continue
            out.append(Markout(p.event, p.ticker, p.count, cost, cents, burst[i]))
        start = stop
    return out


def _burst_sizes(chunk: list[Print], times: list[float], gap: float = 1.0) -> list[float]:
    """Contracts in the sweep each print belongs to.

    Consecutive trades in one market, same taker side, inside `gap` seconds
    are one aggressive order walking the book. Whoever rested at the front of
    the level filled on every sweep; somebody further back filled only on the
    big ones, so burst size stands in for how deep in the queue a fill
    implies you were.
    """
    sizes = [0.0] * len(chunk)
    run: list[int] = []
    for i, p in enumerate(chunk):
        if run:
            prev = chunk[run[-1]]
            if not (prev.taker_side == p.taker_side and times[i] - times[run[-1]] <= gap):
                total = sum(chunk[j].count for j in run)
                for j in run:
                    sizes[j] = total
                run = []
        run.append(i)
    if run:
        total = sum(chunk[j].count for j in run)
        for j in run:
            sizes[j] = total
    return sizes


def summarize_markouts(label: str, rows: list[Markout]) -> dict:
    if not rows:
        return {"label": label, "fills": 0}
    contracts = sum(r.count for r in rows)
    mean = sum(r.cents * r.count for r in rows) / contracts
    lo, hi, tstat = _cluster_ratio_ci([(r.event, r.cents * r.count, r.count) for r in rows])
    return {
        "label": label,
        "fills": len(rows),
        "contracts": contracts,
        "events": len({r.event for r in rows}),
        "cents_per_contract": mean,
        "ci95": (lo, hi),
        "t": tstat,
    }


# --- queue position --------------------------------------------------------
#
# Every number above is what the maker population earned. An entrant does not
# join that population at the front. 019 measured the gap on `KXBTCD` by
# swapping "the book touched our price" for "the book traded through it": the
# fill count fell 60% and three quarters of the edge went with it.
#
# Prints carry the same information without a fill rule. Consecutive trades
# in one market, same taker side, inside a second or so are one aggressive
# order walking the book. Whoever rested at the front of the level filled on
# every burst; somebody deeper filled only on the big ones. So splitting the
# markout by burst size says what the back of the queue was paid, using the
# same real trades as the front.


def _burst_sizes(chunk: list[Print], times: list[float], gap: float = 1.0) -> list[float]:
    """Contracts in the sweep each print belongs to.

    Consecutive trades in one market, same taker side, inside `gap` seconds
    are one aggressive order walking the book. Whoever rested at the front of
    the level filled on every sweep; somebody further back filled only on the
    big ones, so burst size stands in for how deep in the queue a fill
    implies you were.
    """
    sizes = [0.0] * len(chunk)
    run: list[int] = []
    for i, p in enumerate(chunk):
        if run:
            prev = chunk[run[-1]]
            if not (prev.taker_side == p.taker_side and times[i] - times[run[-1]] <= gap):
                total = sum(chunk[j].count for j in run)
                for j in run:
                    sizes[j] = total
                run = []
        run.append(i)
    if run:
        total = sum(chunk[j].count for j in run)
        for j in run:
            sizes[j] = total
    return sizes


def summarize_markouts(label: str, rows: list[Markout]) -> dict:
    if not rows:
        return {"label": label, "fills": 0}
    contracts = sum(r.count for r in rows)
    mean = sum(r.cents * r.count for r in rows) / contracts
    lo, hi, tstat = _cluster_ratio_ci([(r.event, r.cents * r.count, r.count) for r in rows])
    return {
        "label": label,
        "fills": len(rows),
        "contracts": contracts,
        "events": len({r.event for r in rows}),
        "cents_per_contract": mean,
        "ci95": (lo, hi),
        "t": tstat,
    }


# --- queue position --------------------------------------------------------
#
# Every number above is what the maker population earned. An entrant does not
# join that population at the front. 019 measured the gap on `KXBTCD` by
# swapping "the book touched our price" for "the book traded through it": the
# fill count fell 60% and three quarters of the edge went with it.
#
# Prints carry the same information without a fill rule. Consecutive trades
# in one market, same taker side, inside a second or so are one aggressive
# order walking the book. Whoever rested at the front of the level filled on
# every burst; somebody deeper filled only on the big ones. So splitting the
# markout by burst size says what the back of the queue was paid, using the
# same real trades as the front.


def bursts(prints: list[Print], *, gap: float = 1.0) -> dict[int, float]:
    """Map each print's index to the contract size of the sweep it belongs to.

    Indices are positions in the list as passed, so the caller can line the
    sizes up with whatever it already computed from the same list.
    """
    order = sorted(range(len(prints)), key=lambda i: (prints[i].ticker, _epoch(prints[i].ts)))
    sizes: dict[int, float] = {}
    run: list[int] = []
    for pos, idx in enumerate(order):
        p = prints[idx]
        if run:
            prev = prints[run[-1]]
            same = (
                prev.ticker == p.ticker
                and prev.taker_side == p.taker_side
                and _epoch(p.ts) - _epoch(prev.ts) <= gap
            )
            if not same:
                total = sum(prints[j].count for j in run)
                for j in run:
                    sizes[j] = total
                run = []
        run.append(idx)
    if run:
        total = sum(prints[j].count for j in run)
        for j in run:
            sizes[j] = total
    return sizes


# --- fetching --------------------------------------------------------------


def settled_markets(client, series: str, days: float, *, min_volume: float = 500.0, max_pages: int = 60) -> list[dict]:
    """Markets of one series that settled inside the window, with their result."""
    import time

    now = int(time.time())
    floor = now - int(days * 86400)
    out: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"limit": 1000, "status": "settled", "series_ticker": series, "min_close_ts": floor}
        if cursor:
            params["cursor"] = cursor
        payload = client.get("/markets", params)
        rows = payload.get("markets") or []
        for row in rows:
            if float(row.get("volume_fp") or 0) < min_volume:
                continue
            if row.get("result") not in ("yes", "no"):
                continue
            out.append(row)
        cursor = payload.get("cursor")
        if not cursor or not rows:
            break
    return out


def market_prints(client, market: dict, series: str, *, max_pages: int = 200) -> tuple[list[Print], bool]:
    """Every print on one market, plus whether the page budget ran out.

    The second element is not optional bookkeeping. `/markets/trades` pages
    newest first, so stopping early keeps the *end* of the market's life and
    throws away its beginning -- on `KXBTC15M` a 20-page budget silently
    turned the question into "what happened in the last few minutes before
    settlement" and moved the measured maker edge from +0.18c to +0.52c with
    an interval that never admitted the difference. A truncated market is not
    a smaller sample of the same thing; it is a different thing.
    """
    ticker = market["ticker"]
    event = market.get("event_ticker") or ticker
    out: list[Print] = []
    cursor = None
    pages = 0
    while pages < max_pages:
        params = {"ticker": ticker, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = client.get("/markets/trades", params)
        rows = payload.get("trades") or []
        for row in rows:
            out.append(
                Print(
                    ticker=ticker,
                    event=event,
                    series=series,
                    ts=row["created_time"],
                    yes_price=float(row["yes_price_dollars"]),
                    count=float(row["count_fp"]),
                    taker_side=row.get("taker_side") or "",
                )
            )
        pages += 1
        cursor = payload.get("cursor")
        if not cursor or not rows:
            return out, False
    return out, True


def scan_series(client, series: str, days: float, *, min_volume: float = 500.0, limit: int = 200, seed: int = 5):
    """Price one series' maker pool off real prints. Returns (legs, report)."""
    import random

    markets = settled_markets(client, series, days, min_volume=min_volume)
    if len(markets) > limit:
        random.Random(seed).shuffle(markets)
        markets = markets[:limit]
    legs: list[Leg] = []
    truncated = 0
    for market in markets:
        prints, cut = market_prints(client, market, series)
        truncated += 1 if cut else 0
        for print_ in prints:
            leg = settle_leg(print_, market["result"])
            if leg is not None:
                legs.append(leg)
    pool = summarize_pool(series, legs)
    marks = markouts([_print_of(leg) for leg in legs], horizon=60.0)
    report = {
        "series": series,
        "days": days,
        "markets": len(markets),
        "truncated_markets": truncated,
        "pool": pool.as_dict(),
        "markout_60s": summarize_markouts("60s", marks),
        "markout_60s_burst_2000": summarize_markouts("burst>=2000", [m for m in marks if m.burst >= 2000]),
        "by_cost": by_cost_bucket(legs),
    }
    return legs, report


def _print_of(leg: Leg) -> Print:
    """Rebuild the print a leg came from; the mapping is one to one."""
    if leg.maker_side == "no":
        return Print(leg.ticker, leg.event, leg.series, leg.ts, round(1.0 - leg.maker_cost, 4), leg.count, "yes")
    return Print(leg.ticker, leg.event, leg.series, leg.ts, round(leg.maker_cost, 4), leg.count, "no")


def render(report: dict) -> str:
    pool = report["pool"]
    lines = [
        f"series {report['series']}  {report['days']}d  markets {report['markets']}"
        f"  truncated {report['truncated_markets']}",
    ]
    if report["truncated_markets"]:
        lines.append("  WARNING: truncated markets keep only the end of their life; the sample is not the market")
    lines.append(
        f"  pool      {pool['maker_cents_per_contract']:+.3f} c/contract"
        f"  ci[{pool['maker_ci95'][0]:+.2f},{pool['maker_ci95'][1]:+.2f}]  t={pool['maker_t']:+.2f}"
        f"  events {pool['events']}  contracts {pool['contracts']:,.0f}"
    )
    for key, label in (("markout_60s", "markout 60s"), ("markout_60s_burst_2000", "  burst>=2000")):
        row = report[key]
        if row.get("fills"):
            lines.append(
                f"  {label:<12} {row['cents_per_contract']:+.4f} c"
                f"  ci[{row['ci95'][0]:+.3f},{row['ci95'][1]:+.3f}]  t={row['t']:+.2f}"
            )
    for row in report["by_cost"]:
        lines.append(
            f"  cost {row['bucket']}  {row['maker_cents_per_contract']:+7.3f} c"
            f"  ci[{row['maker_ci95'][0]:+.2f},{row['maker_ci95'][1]:+.2f}]"
            f"  contracts {row['contracts']:,.0f}"
        )
    return "\n".join(lines)

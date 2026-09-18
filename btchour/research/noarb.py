"""Constraints the exchange itself guarantees, checked on live quotes.

Two shapes of multi-market event carry a relation that holds in every world,
so a violation is money and not an opinion:

**Nested ladders** -- `KXBTCD` lists `BTC >= K` for many K, so YES(K) must be
non-increasing in K. `research/ladder.py` already prices this shape off
archived candles; the version here works off a live snapshot so it can be
pointed at any series on the exchange.

**Mutually exclusive sets** -- a range ladder (`KXBTCY`: BTC's end-of-year
price in one of 28 bands) or a winner market lists outcomes of which at most
one can settle YES. Selling every leg at its bid receives `sum(bid)` and owes
at most 1, so `sum(bid) > 1` is a locked profit whatever happens.

The mirror trade is not available. `mutually_exclusive` promises *at most*
one winner, not exactly one: a candidate list can omit the eventual winner,
and a leg can be closed or suspended while its siblings still quote. So
`sum(ask) < 1` is only an arbitrage when the set is also exhaustive, which
the API does not assert and which a snapshot cannot check. Buying the basket
is priced here for reference and never reported as an edge.

Fees are the whole question. A basket of N legs pays N taker fees, and on a
`quadratic` series each is `0.07 * p * (1-p)`, which for a 28-bucket ladder
sums to about 1.3 cents in total -- small, because most buckets are cheap and
`p(1-p)` collapses at the ends. On a `fee_multiplier = 0` series it is
literally zero. That is the one structural reason to look here at all after
018 found the `KXBTCD` ladder crossed once in 47,293 chances: there the two
legs cost 3 cents in fees against a 1 cent crossing.

Reported edges are always net of fees, and the size column is the binding
one: a 2-cent edge on 3 contracts is not a strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from ..fees import TAKER_COEFF


def leg_fee(price: float, multiplier: float = 1.0) -> float:
    p = max(0.0, min(1.0, price))
    return TAKER_COEFF * multiplier * p * (1.0 - p)


@dataclass(frozen=True)
class BasketEdge:
    event: str
    series: str
    kind: str  # "buy_all" or "sell_all"
    legs: int
    gross: float  # dollars per basket before fees
    fees: float
    net: float
    min_size: float  # baskets available at the quoted sizes
    prices: tuple[float, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def exhaustive_edges(
    markets: list[dict], *, multiplier: float = 1.0, include_buy: bool = False
) -> list[BasketEdge]:
    """Price the short basket of a mutually-exclusive set.

    `markets` are the event's markets as the API returns them, and every leg
    must quote: a missing bid kills the basket outright, because a leg priced
    at an implied 0.00 would invent liquidity that is not there. `include_buy`
    prices the long basket too, which is an arbitrage only when the set is
    also exhaustive -- see the module docstring.
    """
    if len(markets) < 2:
        return []
    event = markets[0].get("event_ticker") or ""
    series = (markets[0].get("ticker") or "").split("-")[0]
    out: list[BasketEdge] = []

    asks = [_f(m.get("yes_ask_dollars")) for m in markets]
    ask_sizes = [_f(m.get("yes_ask_size_fp")) for m in markets]
    if include_buy and all(a is not None and 0.0 < a < 1.0 for a in asks):
        gross = 1.0 - sum(asks)
        fees = sum(leg_fee(a, multiplier) for a in asks)
        out.append(
            BasketEdge(event, series, "buy_all", len(asks), gross, fees, gross - fees,
                       min(s or 0.0 for s in ask_sizes), tuple(asks))
        )

    bids = [_f(m.get("yes_bid_dollars")) for m in markets]
    bid_sizes = [_f(m.get("yes_bid_size_fp")) for m in markets]
    if all(b is not None and 0.0 < b < 1.0 for b in bids):
        gross = sum(bids) - 1.0
        fees = sum(leg_fee(b, multiplier) for b in bids)
        out.append(
            BasketEdge(event, series, "sell_all", len(bids), gross, fees, gross - fees,
                       min(s or 0.0 for s in bid_sizes), tuple(bids))
        )
    return out


@dataclass(frozen=True)
class LadderCross:
    event: str
    series: str
    low_strike: float
    high_strike: float
    buy_ask: float
    sell_bid: float
    gross: float
    fees: float
    net: float
    size: float

    def as_dict(self) -> dict:
        return asdict(self)


NESTED_STRIKE_TYPES = ("greater", "greater_or_equal")


def rung_subject(market: dict) -> str:
    """The question a rung asks, with its own threshold taken out.

    An event is not a ladder. `KXNCAAF1HSPREAD-26SEP18PRSTORE` holds two:
    "Oregon wins 1H by over K" and "Portland St. wins 1H by over K", and
    Oregon at 7.5 against Portland St. at 2.5 is not a crossed ladder, it is
    two opposite bets. Pairing across them is how this scan first reported
    18,082 crossings in 19,479 pairs -- every one of them manufactured.

    Stripping the digits out of `yes_sub_title` leaves the subject: both
    Oregon rungs collapse to the same key, and `$85,800 or above` collapses
    to the same key as `$85,900 or above`. Markets with no subtitle fall back
    to their own ticker, which pairs with nothing.
    """
    text = market.get("yes_sub_title") or market.get("subtitle") or ""
    if not text:
        return market.get("ticker") or ""
    return "".join(ch for ch in text if not (ch.isdigit() or ch in ".,$")).strip()


def ladder_crosses(markets: list[dict], *, multiplier: float = 1.0) -> list[LadderCross]:
    """A bid at a higher strike sitting above an ask at a lower one.

    Only nested ladders carry the relation: `strike_type` has to say so, the
    ticker has to be a `T<number>` rung, and the strikes have to be distinct.
    """
    by_subject: dict[str, list] = {}
    for m in markets:
        if m.get("strike_type") not in NESTED_STRIKE_TYPES:
            continue
        strike = m.get("floor_strike")
        if strike is None:
            continue
        ask = _f(m.get("yes_ask_dollars"))
        bid = _f(m.get("yes_bid_dollars"))
        by_subject.setdefault(rung_subject(m), []).append(
            (float(strike), ask, bid, _f(m.get("yes_ask_size_fp")) or 0.0, _f(m.get("yes_bid_size_fp")) or 0.0, m)
        )
    out: list[LadderCross] = []
    for rungs in by_subject.values():
        if len(rungs) < 2:
            continue
        rungs.sort(key=lambda r: r[0])
        out.extend(_crosses(rungs, multiplier))
    return out


def _crosses(rungs: list, multiplier: float) -> list["LadderCross"]:
    out: list[LadderCross] = []
    for i, (k_lo, ask_lo, _b, ask_sz, _bs, m_lo) in enumerate(rungs):
        if ask_lo is None or not (0.0 < ask_lo < 1.0):
            continue
        for k_hi, _a, bid_hi, _as2, bid_sz, _m in rungs[i + 1:]:
            if k_hi <= k_lo:
                continue
            if bid_hi is None or not (0.0 < bid_hi < 1.0):
                continue
            gross = bid_hi - ask_lo
            if gross <= 0:
                continue
            fees = leg_fee(ask_lo, multiplier) + leg_fee(bid_hi, multiplier)
            out.append(
                LadderCross(
                    event=m_lo.get("event_ticker") or "",
                    series=(m_lo.get("ticker") or "").split("-")[0],
                    low_strike=k_lo, high_strike=k_hi, buy_ask=ask_lo, sell_bid=bid_hi,
                    gross=gross, fees=fees, net=gross - fees, size=min(ask_sz, bid_sz),
                )
            )
    return out


def _f(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

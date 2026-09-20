"""Static triangular arbitrage on Binance spot, from real trades only.

This is the one shape in the whole programme that carries no risk premium at
all. A triangle either violates or it does not: if BTCUSDT, ETHUSDT and ETHBTC
trade at prices that let you go round the loop and come back with more than
you started, that is money on the floor and no theory is needed to justify it.
Decision 018 ran the same test on Kalshi's mutually exclusive baskets and
found zero violations in 23,765 observations, so this is partly a control --
if it finds a large edge, the pipeline is wrong, not the market.

The measurement discipline that makes it mean something:

* **Real trades, both legs, same instant.** A violation built from stale
  prints is not a violation. Every observation here requires all three pairs
  to have traded inside the same short window, and the price used is the last
  trade in that window on each pair. Quotes are not used at all, so the
  phantom-fill problem of decision 018 cannot arise.
* **Signed by aggressor.** A round trip has to *cross* on each leg: you buy at
  the ask and sell at the bid. Binance publishes `is_buyer_maker`, so a trade
  where the buyer was the taker is evidence of where the ask was, and one
  where the seller was the taker is evidence of the bid. Using mid prints on
  both sides is how a triangle appears to pay half a spread per leg that
  nobody could have crossed.
* **Fees charged three times.** Three legs, three taker fees. Binance spot
  taker is 10bp at retail, 7.5bp paying in BNB, and the lowest published VIP
  tier is 1.7bp. All three are reported, because a violation that only exists
  for the cheapest participant on the venue is a statement about who you are.

What a positive result would look like: violations that (a) exceed three
taker fees, (b) persist long enough to execute three legs, and (c) recur on
independent days rather than clustering in one hour. All three are reported.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

# Binance spot taker fee schedule, in basis points per leg.
SPOT_TAKER_BP = {
    "retail": 10.0,
    "bnb_discount": 7.5,
    "vip9": 1.7,
}

LEGS = 3


def norm_ts(raw: int) -> int:
    """Milliseconds, whatever the archive is using this year.

    Binance moved spot `aggTrades` timestamps to **microseconds** during 2025,
    and the same file layout carries both eras. Nothing downstream notices:
    with stamps a thousand times too large, a one-second matching window
    becomes a one-millisecond window, 96% of observations get dropped for
    staleness, and the 4% that survive are exactly the bursts where prices are
    moving -- so the scan reports a fat one-sided tail of "violations" that are
    really stale legs. Same family as the interval-in-the-cache-path bug in
    decision 023: the number is wrong by a clean factor and nothing raises.
    """
    return raw // 1000 if raw > 1_000_000_000_000_000 else raw


@dataclass(frozen=True)
class Quote:
    """One pair's last trade in a window, with which side was the aggressor."""

    price: float
    ts: int
    taker_bought: bool
    notional: float


@dataclass(frozen=True)
class Violation:
    """One instant where the loop closed above 1 before fees."""

    ts: int
    edge_bp: float  # gross, before any fee
    direction: str  # "forward" or "reverse"
    min_notional: float  # the thinnest leg's traded notional, a capacity hint
    staleness_ms: int  # spread between the oldest and newest of the 3 prints

    def net_bp(self, tier: str = "retail") -> float:
        return self.edge_bp - LEGS * SPOT_TAKER_BP[tier]


def _last_at_or_before(stamps: list[int], t: int) -> int | None:
    i = bisect.bisect_right(stamps, t) - 1
    return i if i >= 0 else None


def loop_edge_bp(base_quote: float, cross_quote: float, cross_base: float) -> float:
    """Gross basis points from going round the loop once.

    With A=BTC, B=ETH, Q=USDT and the pairs A/Q (`base_quote`), B/Q
    (`cross_quote`) and B/A (`cross_base`), the identity that must hold is

        cross_quote = cross_base * base_quote

    so the loop's gross return is the ratio of the two sides minus one. A
    positive number means B/Q is rich against the synthetic built from the
    other two.
    """
    synthetic = cross_base * base_quote
    if synthetic <= 0:
        return 0.0
    return (cross_quote / synthetic - 1.0) * 10_000.0


def scan(
    aq: list[Quote],
    bq: list[Quote],
    ba: list[Quote],
    *,
    window_ms: int = 1_000,
    require_crossable: bool = True,
    max_stale_frac: float = 0.5,
    allow_stale: bool = False,
) -> tuple[list[Violation], dict]:
    """Walk the tape and record every instant the loop closes above fees.

    Observations are taken at each trade on the *cross* pair (B/A), which is
    the thinnest of the three and therefore the binding constraint; the other
    two legs contribute their last trade within `window_ms`. An observation
    where any leg has no trade in the window is dropped, not filled forward.

    Raises when more than `max_stale_frac` of the cross pair's trades are
    dropped for staleness. The USDT legs of any of these triangles trade
    hundreds of times a second, so if most windows come up empty the units or
    the window are wrong, not the market -- and the survivors of that
    accident are a biased subsample (trade bursts, where legs really are
    stale), which produces large one-sided fake violations. Failing loudly is
    the whole point: this exact silent version ran first and looked like an
    edge of 50 basis points.
    """
    a_ts = [q.ts for q in aq]
    b_ts = [q.ts for q in bq]
    out: list[Violation] = []
    seen = dropped_stale = 0
    edges: list[float] = []
    for q in ba:
        ia = _last_at_or_before(a_ts, q.ts)
        ib = _last_at_or_before(b_ts, q.ts)
        if ia is None or ib is None:
            dropped_stale += 1
            continue
        a, b = aq[ia], bq[ib]
        if q.ts - a.ts > window_ms or q.ts - b.ts > window_ms:
            dropped_stale += 1
            continue
        seen += 1
        edge = loop_edge_bp(a.price, b.price, q.price)
        edges.append(edge)
        # To sell B/Q and buy the synthetic you need the B/Q print to be a
        # bid-side print (a taker sold into it) and the other two to be
        # ask-side. Requiring that is what stops the scan from collecting
        # half a spread per leg that nobody crossed.
        if edge > 0:
            direction = "forward"
            crossable = (not b.taker_bought) and a.taker_bought and q.taker_bought
        else:
            direction = "reverse"
            crossable = b.taker_bought and (not a.taker_bought) and (not q.taker_bought)
        if require_crossable and not crossable:
            continue
        stale = max(q.ts - a.ts, q.ts - b.ts)
        out.append(
            Violation(
                ts=q.ts,
                edge_bp=abs(edge),
                direction=direction,
                min_notional=min(a.notional, b.notional, q.notional),
                staleness_ms=stale,
            )
        )
    total = seen + dropped_stale
    if total and dropped_stale / total > max_stale_frac and not allow_stale:
        raise ValueError(
            f"{dropped_stale}/{total} observations dropped as stale "
            f"({dropped_stale / total:.0%}) with window_ms={window_ms}: the "
            f"legs' timestamps are probably not in milliseconds (see norm_ts) "
            f"or the window is too tight. Refusing to report a biased subsample. "
            f"Pass allow_stale=True only to probe deliberately, and read "
            f"stats['stale_frac'] alongside anything it returns."
        )
    edges.sort()
    stats = {
        "observations": seen,
        "dropped_stale": dropped_stale,
        "crossable_candidates": len(out),
        "edge_bp_median": edges[len(edges) // 2] if edges else 0.0,
        "edge_bp_p01": edges[int(len(edges) * 0.01)] if edges else 0.0,
        "edge_bp_p99": edges[int(len(edges) * 0.99)] if edges else 0.0,
        "abs_edge_bp_median": (
            sorted(abs(e) for e in edges)[len(edges) // 2] if edges else 0.0
        ),
        "stale_frac": dropped_stale / total if total else 0.0,
    }
    return out, stats


def summarize(
    violations: list[Violation], stats: dict, *, label: str = ""
) -> dict:
    """Count what survives each fee tier, and how concentrated it is."""
    out = {
        "label": label,
        "observations": stats["observations"],
        "dropped_stale": stats["dropped_stale"],
        "stale_frac": stats["stale_frac"],
        "edge_bp_p01": stats["edge_bp_p01"],
        "abs_edge_bp_median": stats["abs_edge_bp_median"],
        "edge_bp_p99": stats["edge_bp_p99"],
        "crossable_candidates": len(violations),
    }
    for tier, fee in SPOT_TAKER_BP.items():
        survivors = [v for v in violations if v.net_bp(tier) > 0]
        out[f"survivors_{tier}"] = len(survivors)
        out[f"total_bp_{tier}"] = sum(v.net_bp(tier) for v in survivors)
        out[f"best_bp_{tier}"] = max((v.net_bp(tier) for v in survivors), default=0.0)
        out[f"capacity_usd_{tier}"] = min(
            (v.min_notional for v in survivors), default=0.0
        )
        out[f"cost_floor_bp_{tier}"] = LEGS * fee
    return out

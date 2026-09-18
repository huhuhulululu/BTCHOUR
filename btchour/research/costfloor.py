"""The structural account, ported from prediction markets to continuous ones.

`catalog/rules/screen.md` rule 1 is `spread / one-sided taker fee >= 3`. It
killed `KXBTCD` before any data was pulled, because on Kalshi the spread *is*
the whole prize: a contract settles to 0 or 1, you hold it to settlement, and
there is no carry, no term structure and no holding period to amortize costs
over.

None of that survives the port. In a continuous market the holding period is a
free parameter, so the same rule has to be written per unit of holding period:

    taker round trip  : you pay the spread plus two taker fees
    maker round trip  : you earn the spread and pay two maker fees
    breakeven fraction: round-trip cost / the move available over the horizon

The last line is the one that ports. It asks: what fraction of a typical move
over your holding period must your forecast capture before fees stop eating
you? It needs no model of ours, no fill rule and no queue assumption -- only a
fee schedule, a quoted spread and a volatility. That makes it computable
before a single row of data is downloaded, which is the entire point of the
screen.

Adverse selection is *not* in here. It never is at this stage: rule 4 measures
it from real prints. What this module establishes is whether a mechanism is
already dead before adverse selection gets a turn -- which, for quoting on
crypto perps, it is.

Every input carries its source and a `verified` flag. Unverified numbers do
not go in a conclusion; they mark what to measure once the egress allowlist
opens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Execution styles for a full round trip (enter and exit).
TAKER_BOTH = "taker_both"
MAKER_IN_TAKER_OUT = "maker_in_taker_out"
MAKER_BOTH = "maker_both"

_STYLES = (TAKER_BOTH, MAKER_IN_TAKER_OUT, MAKER_BOTH)


@dataclass(frozen=True)
class Venue:
    """Cost structure of one instrument at one fee tier, in basis points."""

    name: str
    spread_bp: float
    taker_bp: float
    maker_bp: float
    source: str
    verified: bool = False

    def __post_init__(self) -> None:
        for field in ("spread_bp", "taker_bp", "maker_bp"):
            if getattr(self, field) < 0:
                raise ValueError(f"{self.name}: {field} must be >= 0")


@dataclass(frozen=True)
class Horizon:
    """A holding period and the move typically available over it."""

    name: str
    annual_vol: float
    periods_per_year: float
    source: str
    verified: bool = False

    @property
    def sigma_bp(self) -> float:
        """One-standard-deviation move over one period, in basis points."""

        return 1e4 * self.annual_vol / math.sqrt(self.periods_per_year)


def round_trip_cost_bp(venue: Venue, style: str) -> float:
    """Cost of entering and exiting once, in bp. Negative means it pays.

    Crossing the spread once costs half of it against the mid, so a taker
    round trip pays the full spread; resting on both sides earns the full
    spread. Fees are charged on both legs either way.
    """

    if style == TAKER_BOTH:
        return venue.spread_bp + 2.0 * venue.taker_bp
    if style == MAKER_IN_TAKER_OUT:
        return venue.maker_bp + venue.taker_bp
    if style == MAKER_BOTH:
        return -venue.spread_bp + 2.0 * venue.maker_bp
    raise ValueError(f"unknown execution style {style!r}; expected one of {_STYLES}")


def quoting_ratio(venue: Venue) -> float:
    """`spread / two maker fees`: rule 1 rewritten for the resting side.

    Below 1 the fee schedule alone makes quoting negative, before the first
    informed counterparty shows up. `inf` when the venue charges no maker fee
    (Kalshi's `quadratic` series, most equity rebate tiers).
    """

    if venue.maker_bp == 0.0:
        return math.inf
    return venue.spread_bp / (2.0 * venue.maker_bp)


def breakeven_fraction(venue: Venue, horizon: Horizon, style: str) -> float:
    """Fraction of a one-sigma move over `horizon` that just covers costs.

    This is the ported rule 1. `screen.md` wanted the prize at least three
    times the cost, so the mirror threshold here is 1/3: a mechanism whose
    costs eat more than a third of the horizon's whole move is asking the
    forecast for more than forecasts deliver.
    """

    return round_trip_cost_bp(venue, style) / horizon.sigma_bp


#: Quoted spreads and fee schedules, base retail tier, no volume discounts.
VENUES: tuple[Venue, ...] = (
    Venue(
        name="Binance USDT-M BTCUSDT perp",
        spread_bp=0.09,
        taker_bp=5.0,
        maker_bp=2.0,
        source="binance.com/en/fee/futureFee (VIP0 0.0200/0.0500); "
        "Kaiko/Amberdata 2026: BTC perp spread 0.09bp avg, 0.01bp tightest",
        verified=True,
    ),
    Venue(
        name="Binance spot BTCUSDT",
        spread_bp=0.09,
        taker_bp=10.0,
        maker_bp=10.0,
        source="binance.com/en/fee/spotMaker (VIP0 0.1000/0.1000)",
        verified=True,
    ),
    Venue(
        name="US large cap equity, retail broker",
        spread_bp=0.5,
        taker_bp=0.28,
        maker_bp=0.28,
        source="1c spread on a $200 name; $0.0035/share commission plus "
        "SEC/TAF. NOT yet verified against a live quote feed",
        verified=False,
    ),
    Venue(
        name="CME ES future",
        spread_bp=0.5,
        taker_bp=0.10,
        maker_bp=0.10,
        source="one 0.25pt tick at 5000 index; ~$2.5 all-in per contract on "
        "$250k notional. NOT yet verified",
        verified=False,
    ),
    Venue(
        name="Kalshi KXBTCD (measured, decisions 018-020)",
        spread_bp=107.0,
        taker_bp=175.0,
        maker_bp=0.0,
        source="measured: 1.07c spread, 1.75c ATM taker fee, no maker fee, "
        "on $1 notional so 1c = 100bp",
        verified=True,
    ),
)

#: Holding periods and the move available over each.
HORIZONS: tuple[Horizon, ...] = (
    Horizon("BTC 1 minute", 0.50, 365 * 24 * 60, "50% annualized, 24/7"),
    Horizon("BTC 1 hour", 0.50, 365 * 24, "50% annualized, 24/7"),
    Horizon("BTC 1 day", 0.50, 365, "50% annualized, 24/7"),
    Horizon("BTC 30 days", 0.50, 365 / 30, "50% annualized, 24/7"),
    Horizon("US single name, 1 day", 0.30, 252, "30% annualized, 252 sessions"),
    Horizon("US single name, 20 days", 0.30, 252 / 20, "30% annualized"),
)


@dataclass(frozen=True)
class Carry:
    """A delta-neutral carry trade: collect a funding stream, pay two legs.

    Unlike anything in decisions 015-020, the income here is *published and
    settled by the exchange*, not inferred from a fill model -- the same
    property that made `venue.py` trustworthy. The whole trade is arithmetic
    once you have the funding history and the two fee schedules.
    """

    name: str
    funding_bp_per_day: float
    entry_cost_bp: float
    exit_cost_bp: float
    source: str
    verified: bool = False

    def gross_bp(self, days: float) -> float:
        return self.funding_bp_per_day * days

    def net_bp(self, days: float) -> float:
        return self.gross_bp(days) - self.entry_cost_bp - self.exit_cost_bp

    def prize_over_cost(self, days: float) -> float:
        """Rule 1 for a carry trade: gross income over round-trip cost."""

        cost = self.entry_cost_bp + self.exit_cost_bp
        if cost == 0.0:
            return math.inf
        return self.gross_bp(days) / cost

    def net_annualized(self, days: float) -> float:
        """Simple (not compounded) annualized net return, as a fraction."""

        return (self.net_bp(days) / 1e4) * (365.0 / days)

    def excess_over_riskfree(self, days: float, riskfree: float) -> float:
        """Net annualized minus the risk-free rate. The only number that counts.

        A carry trade is not an edge, it is payment for bearing a risk. So the
        question is never "what does it annualize to" but "what is left after
        the rate you could have had for free, given that this one carries
        exchange, liquidation and custody risk".
        """

        return self.net_annualized(days) - riskfree


#: Spot-perp cash-and-carry on Binance, both legs at VIP0.
#: Entry/exit each buy-or-sell spot (10bp, maker and taker are equal there)
#: plus rest the perp leg (2bp maker).
CARRIES: tuple[Carry, ...] = (
    Carry(
        name="BTC spot-perp carry, long-run funding",
        funding_bp_per_day=3.0,
        entry_cost_bp=12.0,
        exit_cost_bp=12.0,
        source="0.01%/8h is the Binance base funding rate, ~11%/yr; "
        "costs from the VENUES fee schedules above",
        verified=False,
    ),
    Carry(
        name="BTC spot-perp carry, 2026-09-09 reading",
        funding_bp_per_day=2.34,
        entry_cost_bp=12.0,
        exit_cost_bp=12.0,
        source="0.0078%/8h observed 2026-09-09 (single reading, not a mean); "
        "costs from the VENUES fee schedules above",
        verified=False,
    ),
)

#: 1-year US Treasury, 2026-09-16. The bar a carry trade has to clear.
RISKFREE_1Y = 0.0445

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.fees import fill_cost, max_entry_price
from btchour.kalshi import Market
from btchour.model import SpotQuote, digital_prob, effective_vol
from btchour.tickers import is_hourly_window


@dataclass(frozen=True)
class Opportunity:
    ticker: str
    event_ticker: str
    subtitle: str
    side: str
    book_side: str
    strike: float
    spot: float
    seconds_left: float
    model_p: float
    ask: float
    max_price: float
    limit_price: float
    taker: bool
    b: float
    if_win_roi: float
    expected_roi: float
    ev: float
    fee: float
    count: float
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _seconds_left(close_time: str | None, now: datetime) -> float:
    if not close_time:
        return 0.0
    close = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    return (close - now).total_seconds()


def _clip_count(price: float, settings: Settings) -> float:
    if price <= 0:
        return 0.0
    by_notional = settings.max_notional / price
    return max(0.0, min(settings.max_contracts, by_notional))


def evaluate_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    if market.strike is None or market.strike_type not in {"greater", "greater_or_equal"}:
        return []
    if settings.hourly_only and not is_hourly_window(market.open_time, market.close_time):
        return []
    seconds = _seconds_left(market.close_time, now)
    if seconds < 8:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    sides = [
        ("yes", "bid", p_yes, market.yes_ask_effective),
        ("no", "ask", 1.0 - p_yes, market.no_ask_effective),
    ]
    found: list[Opportunity] = []
    for side, book_side, model_p, ask in sides:
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        if model_p + 1e-12 < settings.min_win_prob:
            continue
        taker_cap = max_entry_price(settings.target_profit, taker=True)
        maker_cap = max_entry_price(settings.target_profit, taker=False)
        taker = ask <= taker_cap
        if not taker and not settings.allow_maker:
            continue
        limit = min(ask if taker else maker_cap, maker_cap)
        if limit <= 0:
            continue
        cost = fill_cost(ask if taker else limit, 1.0, taker=taker)
        edge = cost.edge(model_p)
        if edge.b + 1e-12 < settings.target_profit:
            continue
        if edge.ev + 1e-12 < settings.min_ev:
            continue
        count = _clip_count(limit, settings)
        if count < 1:
            continue
        found.append(
            Opportunity(
                ticker=market.ticker,
                event_ticker=market.event_ticker,
                subtitle=market.subtitle,
                side=side,
                book_side=book_side,
                strike=market.strike,
                spot=spot.price,
                seconds_left=seconds,
                model_p=model_p,
                ask=ask,
                max_price=taker_cap if taker else maker_cap,
                limit_price=ask if taker else limit,
                taker=taker,
                b=edge.b,
                if_win_roi=edge.b,
                expected_roi=edge.ev,
                ev=edge.ev,
                fee=cost.fee,
                count=int(count),
                reason=(
                    f"{side.upper()} EV={edge.ev:.1%} p={edge.p:.1%} b={edge.b:.1%} "
                    f"at {'taker' if taker else 'maker'} {ask if taker else limit:.2f}; "
                    f"strike {market.strike:.2f} / spot {spot.price:.2f}"
                ),
            )
        )
    found.sort(key=lambda row: (row.expected_roi, row.model_p), reverse=True)
    return found


def scan_markets(markets: list[Market], spot: SpotQuote, settings: Settings, now: datetime | None = None) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    for market in markets:
        opportunities.extend(evaluate_market(market, spot, settings, now))
    opportunities.sort(key=lambda row: (row.expected_roi, row.model_p, -row.seconds_left), reverse=True)
    return opportunities

from __future__ import annotations

from dataclasses import asdict, dataclass

from btchour.fees import fill_cost
from btchour.kalshi import Market
from btchour.model import digital_prob, SpotQuote


@dataclass(frozen=True)
class SideScore:
    ticker: str
    side: str
    strike: float
    spot: float
    seconds_left: float
    ask: float
    model_p: float
    if_win_roi: float
    ev: float
    fee: float
    passes: bool
    reject: str

    def as_dict(self) -> dict:
        return asdict(self)


def score_side(
    market: Market,
    spot: SpotQuote,
    seconds_left: float,
    side: str,
    ask: float,
    model_p: float,
    target_profit: float,
    min_win_prob: float,
    min_ev: float,
) -> SideScore | None:
    if ask is None or ask <= 0 or ask >= 1.0:
        return None
    cost = fill_cost(ask, taker=True)
    ev = cost.betting_ev(model_p)
    reasons = []
    if cost.if_win_roi + 1e-12 < target_profit:
        reasons.append(f"if_win {cost.if_win_roi:.1%} < {target_profit:.0%}")
    if model_p + 1e-12 < min_win_prob:
        reasons.append(f"p {model_p:.1%} < {min_win_prob:.0%}")
    if ev + 1e-12 < min_ev:
        reasons.append(f"EV {ev:.1%} < {min_ev:.0%}")
    return SideScore(
        ticker=market.ticker,
        side=side,
        strike=market.strike or 0.0,
        spot=spot.price,
        seconds_left=seconds_left,
        ask=ask,
        model_p=model_p,
        if_win_roi=cost.if_win_roi,
        ev=ev,
        fee=cost.fee,
        passes=not reasons,
        reject="; ".join(reasons),
    )


def score_market(
    market: Market,
    spot: SpotQuote,
    seconds_left: float,
    annual_vol: float,
    target_profit: float,
    min_win_prob: float,
    min_ev: float,
) -> list[SideScore]:
    if market.strike is None:
        return []
    p_yes = digital_prob(spot.price, market.strike, seconds_left, annual_vol)
    out = []
    yes = score_side(
        market, spot, seconds_left, "yes", market.yes_ask_effective or 0.0, p_yes,
        target_profit, min_win_prob, min_ev,
    )
    no = score_side(
        market, spot, seconds_left, "no", market.no_ask_effective or 0.0, 1.0 - p_yes,
        target_profit, min_win_prob, min_ev,
    )
    if yes:
        out.append(yes)
    if no:
        out.append(no)
    return out

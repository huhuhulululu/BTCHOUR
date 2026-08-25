from __future__ import annotations

import math
from dataclasses import dataclass


TAKER_COEFF = 0.07
MAKER_COEFF = 0.0175
CENTICENT = 10_000
TICK = 0.01


def ceil_centicent(amount: float) -> float:
    if amount <= 0:
        return 0.0
    scaled = round(amount * CENTICENT, 8)
    return math.ceil(scaled - 1e-9) / CENTICENT


def quadratic_fee(price: float, count: float, multiplier: float, coeff: float) -> float:
    if count <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = multiplier * coeff * count * price * (1.0 - price)
    return ceil_centicent(raw)


def taker_fee(price: float, count: float = 1.0, multiplier: float = 1.0) -> float:
    return quadratic_fee(price, count, multiplier, TAKER_COEFF)


def maker_fee(price: float, count: float = 1.0, multiplier: float = 0.0) -> float:
    return quadratic_fee(price, count, multiplier, MAKER_COEFF)


@dataclass(frozen=True)
class FillCost:
    price: float
    count: float
    fee: float
    cost: float
    if_win_pnl: float
    if_win_roi: float
    if_lose_pnl: float

    def expected_pnl(self, win_prob: float) -> float:
        return win_prob * self.if_win_pnl + (1.0 - win_prob) * self.if_lose_pnl

    def expected_roi(self, win_prob: float) -> float:
        if self.cost <= 0:
            return 0.0
        return self.expected_pnl(win_prob) / self.cost

    def betting_ev(self, win_prob: float) -> float:
        """EV per unit stake: p * b - (1 - p), where b is if-win net odds."""
        return betting_ev(win_prob, self.if_win_roi)


def betting_ev(win_prob: float, net_odds: float) -> float:
    return win_prob * net_odds - (1.0 - win_prob)


def fill_cost(price: float, count: float = 1.0, *, taker: bool = True, multiplier: float = 1.0) -> FillCost:
    fee = taker_fee(price, count, multiplier) if taker else maker_fee(price, count, 0.0)
    cost = price * count + fee
    if_win = count - cost
    if_lose = -cost
    roi = if_win / cost if cost else 0.0
    return FillCost(price, count, fee, cost, if_win, roi, if_lose)


def max_entry_price(target_roi: float, *, taker: bool = True, multiplier: float = 1.0, tick: float = TICK) -> float:
    """Highest whole-tick price whose if-win net ROI is still >= target_roi."""
    best = 0.0
    steps = int(round(0.99 / tick))
    for i in range(1, steps + 1):
        price = round(i * tick, 4)
        if fill_cost(price, 1.0, taker=taker, multiplier=multiplier).if_win_roi + 1e-12 >= target_roi:
            best = price
    return best

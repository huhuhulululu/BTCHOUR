from __future__ import annotations

import math
from dataclasses import dataclass


SECONDS_PER_YEAR = 365.25 * 24 * 3600
TWAP_SECONDS = 60.0
MIN_TAU_SECONDS = 45.0
# Never divide by zero in the final ticks; 1s of variance time is ~0.6bp of BTC move.
MIN_VARIANCE_SECONDS = 1.0

# ADR 021: BTC does not move evenly across the hour, so a trailing vol estimate is
# systematically wrong in a way that depends on where in the hour you stand. Weights
# are E[r^2] per ten-minute block of the hour, normalised to mean 1, measured on the
# FIRST calendar half of the 1544-hour sample and validated on the second. A 60-point
# per-minute profile scores the same out-of-sample (0.08800 vs 0.08799), so the shape
# is structural, not fitted -- six numbers is the honest resolution.
HOUR_VARIANCE_WEIGHTS = (1.14, 1.03, 0.94, 1.16, 0.94, 0.78)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def twap_variance_seconds(seconds: float, window: float = TWAP_SECONDS) -> float:
    """Effective variance time for a settlement that is a `window`-second MEAN.

    KXBTCD does not settle on the last print: it averages the final 60 BRTI ticks
    (`catalog/rules/settlement.md`). Averaging the tail of a Brownian path carries less
    variance than sampling its endpoint, so the residual dispersion is smaller than the
    plain time-to-close -- and the gap widens as the close approaches.

    With T = window and tau = seconds to close, settlement is (1/T)·integral of the path
    over [tau-T, tau], and Var = (1/T^2)·double-integral of min(s,t), which gives

        tau >= T :  tau - 2T/3          (matches tau at large tau, 20s at tau = 60s)
        tau <  T :  tau^3 / (3 T^2)     (we are already inside the averaging window)

    The two branches agree at tau = T. The old code did the opposite of the second
    branch -- it *raised* tau to a 60-second floor, i.e. it claimed a full minute of
    dispersion still to come with ten seconds left, when almost the whole averaging
    window has already printed.

    Measured on 1544 KXBTCD hours: this lowers Brier in every time bucket
    (0.08727 -> 0.08716 overall, 0.05288 -> 0.05231 inside the last ten minutes).
    The tau < T branch is derived, not measured -- the study sample starts at tau = 120s.
    """
    if seconds <= 0 or window <= 0:
        return 0.0
    if seconds >= window:
        return seconds - 2.0 * window / 3.0
    return (seconds ** 3) / (3.0 * window * window)


def hour_variance_weight(minute: float) -> float:
    """Relative variance of one minute, by its position in the hour (mean 1)."""
    index = int(minute) // 10
    if index < 0:
        index = 0
    elif index >= len(HOUR_VARIANCE_WEIGHTS):
        index = len(HOUR_VARIANCE_WEIGHTS) - 1
    return HOUR_VARIANCE_WEIGHTS[index]


def seasonal_scale(seconds: float, minute: float | None) -> float:
    """How much the remaining minutes weigh against a flat-vol assumption.

    A trailing 15-minute vol estimate taken at minute 5 mostly measures the previous
    hour's quiet tail and is then applied to this hour's busy open, so it runs cold;
    taken at minute 50 it measures the busy middle and is applied to the closing lull,
    so it runs hot. Measured on the sample, realised/model sd ran 1.07 at T-45..60m and
    0.88 at T-5..10m. Reshaping the remaining variance by the hour profile pulls the
    out-of-sample spread from a mean |error| of 0.053 to 0.038.

    Returns 1.0 when the position in the hour is unknown, so callers that cannot supply
    it keep the previous behaviour exactly.
    """
    if minute is None or seconds <= 0:
        return 1.0
    flat_minutes = seconds / 60.0
    if flat_minutes <= 0:
        return 1.0
    start = int(minute) + 1
    remaining = sum(hour_variance_weight(m) for m in range(start, start + max(1, round(flat_minutes))))
    return (remaining / flat_minutes) if remaining > 0 else 1.0


def variance_seconds(seconds: float, minute: float | None = None) -> float:
    """Effective variance time: the TWAP mean correction, reshaped by the hour profile."""
    return max(twap_variance_seconds(seconds) * seasonal_scale(seconds, minute), MIN_VARIANCE_SECONDS)


def digital_prob(
    spot: float,
    strike: float,
    seconds: float,
    annual_vol: float,
    drift: float = 0.0,
    minute: float | None = None,
) -> float:
    """P(settlement > strike), where settlement is the BRTI 60-second mean.

    `minute` is the position inside the hour (0-60); pass it to enable the ADR 021
    seasonal reshape. Omitting it leaves the pre-021 behaviour untouched.
    """
    if spot <= 0 or strike <= 0 or annual_vol <= 0:
        return 0.0
    tau = variance_seconds(seconds, minute) / SECONDS_PER_YEAR
    denom = annual_vol * math.sqrt(tau)
    if denom <= 0:
        return 1.0 if spot > strike else 0.0
    d = (math.log(spot / strike) + (drift - 0.5 * annual_vol * annual_vol) * tau) / denom
    return min(1.0, max(0.0, norm_cdf(d)))


def realized_annual_vol(prices: list[float], bar_seconds: float) -> float | None:
    if len(prices) < 8 or bar_seconds <= 0:
        return None
    rets = []
    for prev, cur in zip(prices, prices[1:]):
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 6:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sigma = math.sqrt(max(var, 0.0))
    if sigma <= 0:
        return None
    bars_per_year = SECONDS_PER_YEAR / bar_seconds
    annual = sigma * math.sqrt(bars_per_year)
    return min(1.8, max(0.25, annual))


def effective_vol(realized: float | None, floor: float) -> float:
    if realized is None or realized <= 0:
        return floor
    return max(realized, floor)


def sigma_cushion(
    spot: float, strike: float, seconds: float, annual_vol: float, minute: float | None = None
) -> float:
    """How many residual-vol sigmas the spot is away from the strike.

    Uses the same TWAP-corrected variance time as `digital_prob`; the cushion and the
    probability must describe one distribution, not two.
    """
    if spot <= 0 or strike <= 0 or annual_vol <= 0:
        return 0.0
    tau = variance_seconds(seconds, minute) / SECONDS_PER_YEAR
    denom = annual_vol * math.sqrt(tau)
    if denom <= 0:
        return 99.0 if spot != strike else 0.0
    return abs(math.log(spot / strike)) / denom


def required_p(target_ev: float, net_odds: float) -> float:
    """p needed for EV = p*b - (1-p) to reach target_ev."""
    if net_odds <= -1:
        return 1.0
    return min(1.0, max(0.0, (target_ev + 1.0) / (1.0 + net_odds)))


@dataclass(frozen=True)
class SpotQuote:
    price: float
    source: str
    twap60: float | None = None
    annual_vol: float | None = None
    ts_ms: int | None = None
    impulse: float = 0.0

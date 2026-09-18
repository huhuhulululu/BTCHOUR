"""Synthetic KXBTCD hour tapes with a known ground truth.

Why this exists: the live tape is the only thing that proves a strategy makes
money, but a simulator with a known truth is what proves a strategy *cannot*.
A rule that loses in a world built to favour it is broken mechanically, not
starved of signal, and no amount of real data will rescue it.

The world:

* spot is an iid GBM (a martingale), so `digital_prob` on the true vol is the
  exact fair value of every rung. No hidden edge is smuggled into the path.
* the book quotes `mid = fair(spot * (1 + k * r3)) + AR(1) noise`, where `r3`
  is the trailing 3-minute return -- the same window the engine calls
  `impulse`.
    - `k = 0`   fair book. Any positive PnL here is fees/spread luck.
    - `k < 0`   the book lags the move. Following the impulse is +EV.
    - `k > 0`   the book extrapolates the move. Fading the impulse is +EV.
* settlement is the real rule: the 60-second average of spot before the close.

`k` is the only knob that creates edge, and its sign says which side of the
impulse the edge is on. That makes it a falsification test the engine cannot
pass by accident.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from btchour.model import SECONDS_PER_YEAR, digital_prob
from btchour.replay import EventTape
from btchour.tickers import format_event_ticker

TICK = 0.01
SUBSTEP_SECONDS = 5.0
TWAP_SECONDS = 60.0


@dataclass(frozen=True)
class SimConfig:
    """One synthetic world. `bias_k` is the only source of edge."""

    annual_vol: float = 0.55
    bias_k: float = 0.0
    quote_noise: float = 0.006
    noise_phi: float = 0.85
    half_spread_atm: float = 0.005
    half_spread_tail: float = 0.02
    strike_step: float = 100.0
    strike_span: float = 1500.0
    start_spot: float = 78_000.0
    volume_scale: float = 60.0
    seed: int = 0


REGIMES: dict[str, float] = {
    "fair": 0.0,
    "underreact": -0.45,
    "overreact": 0.45,
}


def regime_config(regime: str, **overrides) -> SimConfig:
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}; pick one of {sorted(REGIMES)}")
    return SimConfig(bias_k=REGIMES[regime], **overrides)


def _tick_floor(value: float) -> float:
    return max(0.01, min(0.99, math.floor(value / TICK + 1e-9) * TICK))


def _tick_ceil(value: float) -> float:
    return max(0.01, min(0.99, math.ceil(value / TICK - 1e-9) * TICK))


def _half_spread(cfg: SimConfig, fair: float) -> float:
    """1c wide at the money, wider out in the tails -- the shape Kalshi shows."""
    away = min(1.0, abs(fair - 0.5) * 2.0)
    return cfg.half_spread_atm + (cfg.half_spread_tail - cfg.half_spread_atm) * away**2


def _strike_grid(cfg: SimConfig) -> list[float]:
    base = math.floor(cfg.start_spot / cfg.strike_step) * cfg.strike_step - 0.01
    steps = int(cfg.strike_span / cfg.strike_step)
    return [round(base + i * cfg.strike_step, 2) for i in range(-steps, steps + 1)]


def _spot_path(cfg: SimConfig, rng: random.Random, seconds: float) -> list[float]:
    """Driftless GBM sampled every `SUBSTEP_SECONDS`, including t=0."""
    dt_years = SUBSTEP_SECONDS / SECONDS_PER_YEAR
    sigma = cfg.annual_vol
    drift = -0.5 * sigma * sigma * dt_years
    shock = sigma * math.sqrt(dt_years)
    spot = cfg.start_spot
    path = [spot]
    for _ in range(int(seconds / SUBSTEP_SECONDS)):
        spot *= math.exp(drift + shock * rng.gauss(0.0, 1.0))
        path.append(spot)
    return path


def simulate_tape(
    cfg: SimConfig,
    close_utc: datetime,
    *,
    rng: random.Random | None = None,
    start_spot: float | None = None,
) -> EventTape:
    """One hour of BRTI plus a 1-minute candle per rung, in replay's own format."""
    rng = rng or random.Random(cfg.seed)
    if start_spot is not None:
        cfg = SimConfig(**{**cfg.__dict__, "start_spot": start_spot})
    maturity_s = int(close_utc.timestamp())
    open_s = maturity_s - 3600
    path = _spot_path(cfg, rng, 3600.0)
    per_minute = int(60.0 / SUBSTEP_SECONDS)
    strikes = _strike_grid(cfg)

    twap_steps = int(TWAP_SECONDS / SUBSTEP_SECONDS)
    settle = sum(path[-twap_steps:]) / twap_steps
    results = {strike: ("yes" if settle > strike else "no") for strike in strikes}

    noise = {strike: 0.0 for strike in strikes}
    spots: dict[int, float] = {}
    candles: dict[float, dict] = {strike: {} for strike in strikes}

    for minute in range(60):
        lo_idx = minute * per_minute
        hi_idx = lo_idx + per_minute
        window = path[lo_idx : hi_idx + 1]
        close_spot = window[-1]
        minute_ms = (open_s + minute * 60) * 1000
        end_ts = open_s + (minute + 1) * 60
        spots[minute_ms] = close_spot
        left = max(maturity_s - end_ts, 1.0)

        # The engine's own impulse: spot now minus spot 3 minutes ago.
        back_idx = max(0, hi_idx - 3 * per_minute)
        prior = path[back_idx]
        r3 = (close_spot - prior) / prior if prior > 0 else 0.0
        quoted_spot = close_spot * (1.0 + cfg.bias_k * r3)
        quoted_hi = max(window) * (1.0 + cfg.bias_k * r3)
        quoted_lo = min(window) * (1.0 + cfg.bias_k * r3)

        for strike in strikes:
            noise[strike] = cfg.noise_phi * noise[strike] + math.sqrt(
                max(0.0, 1.0 - cfg.noise_phi**2)
            ) * rng.gauss(0.0, cfg.quote_noise)
            drift = noise[strike]
            fair_close = digital_prob(quoted_spot, strike, left, cfg.annual_vol)
            fair_hi = digital_prob(quoted_hi, strike, left, cfg.annual_vol)
            fair_lo = digital_prob(quoted_lo, strike, left, cfg.annual_vol)
            half = _half_spread(cfg, fair_close)
            yes_ask_close = min(0.99, max(0.02, _tick_ceil(fair_close + drift + half)))
            yes_bid_close = min(0.98, _tick_floor(fair_close + drift - half))
            yes_ask_low = min(0.99, max(0.02, _tick_ceil(fair_lo + drift + half)))
            yes_bid_high = min(0.98, _tick_floor(fair_hi + drift - half))
            if yes_bid_close >= yes_ask_close:
                # A one-tick book is the tightest Kalshi shows; never cross it.
                yes_bid_close = round(yes_ask_close - TICK, 2)
            depth = math.exp(-(((fair_close - 0.5) / 0.28) ** 2))
            volume = float(int(rng.expovariate(1.0) * cfg.volume_scale * depth))
            candles[strike][end_ts] = {
                "yes_ask": {
                    "close_dollars": yes_ask_close,
                    "low_dollars": min(yes_ask_low, yes_ask_close),
                    "high_dollars": max(yes_ask_low, yes_ask_close),
                },
                "yes_bid": {
                    "close_dollars": yes_bid_close,
                    "high_dollars": max(yes_bid_high, yes_bid_close),
                    "low_dollars": min(yes_bid_high, yes_bid_close),
                },
                "volume_fp": volume,
            }

    yes_strikes = [s for s, r in results.items() if r == "yes"]
    no_strikes = [s for s, r in results.items() if r == "no"]
    band = (max(yes_strikes), min(no_strikes)) if yes_strikes and no_strikes else None
    return EventTape(
        event_ticker=format_event_ticker(close_utc),
        spots=spots,
        candles=candles,
        results=results,
        maturity_ms=maturity_s * 1000,
        band=band,
        error=None if band else "settled off the simulated ladder",
    )


def simulate_tapes(
    hours: int,
    cfg: SimConfig | None = None,
    *,
    end_utc: datetime | None = None,
) -> list[EventTape]:
    """`hours` consecutive hours, newest first -- the order `replay_tapes` wants.

    Spot carries across hours, so a run is one continuous BTC path, not a
    sequence of independent draws that would flatter a per-hour rule.
    """
    cfg = cfg or SimConfig()
    rng = random.Random(cfg.seed)
    end_utc = (end_utc or datetime(2026, 9, 1, tzinfo=timezone.utc)).replace(
        minute=0, second=0, microsecond=0
    )
    tapes: list[EventTape] = []
    spot = cfg.start_spot
    for index in range(hours):
        close_utc = end_utc - timedelta(hours=hours - 1 - index)
        tape = simulate_tape(cfg, close_utc, rng=rng, start_spot=spot)
        last_minute = max(tape.spots) if tape.spots else None
        if last_minute is not None:
            spot = tape.spots[last_minute]
        tapes.append(tape)
    tapes.reverse()
    return tapes

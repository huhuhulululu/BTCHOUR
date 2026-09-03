"""Offline lab over `data/hourly.sqlite`: loaders, the trade accounting, and the stats.

Accounting is the repo's own: `btchour.fees` for the quadratic taker fee, `btchour.model`
for the GBM digital probability and the sigma cushion. Nothing here re-implements a
cheaper fee or a friendlier fill -- a rule that only wins under a softer cost model is
not a rule.

Conventions
-----------
* One **hour event** is one cluster. Strikes inside the same hour move together, so the
  t-statistic is cluster-robust by `event_ticker` (the 15m repo's lesson: naive t on
  per-contract rows is inflated).
* A decision at minute-end `ts` may read only that minute's candle *close* and anything
  earlier. Settlement comes from the event's `expiration_value`.
* `yes` taker pays `yes_ask_close`; `no` taker pays `1 - yes_bid_close`.
* Net is reported in **cents per contract**, after entry fee (and exit fee when the rule
  exits early).
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btchour.fees import exit_proceeds, taker_fee  # noqa: E402
from btchour.model import digital_prob, realized_annual_vol, sigma_cushion  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "data" / "hourly.sqlite"

VOL_LOOKBACK_MIN = 15
VOL_FLOOR = 0.25


# --------------------------------------------------------------------------- loading


@dataclass
class Quote:
    strike: float
    yes_bid: float | None
    yes_ask: float | None
    yes_bid_low: float | None
    yes_bid_high: float | None
    yes_ask_high: float | None
    yes_ask_low: float | None
    volume: float

    @property
    def no_ask(self) -> float | None:
        return None if self.yes_bid is None else round(1.0 - self.yes_bid, 4)

    @property
    def no_bid(self) -> float | None:
        return None if self.yes_ask is None else round(1.0 - self.yes_ask, 4)

    def ask(self, side: str) -> float | None:
        return self.yes_ask if side == "yes" else self.no_ask

    def bid(self, side: str) -> float | None:
        return self.yes_bid if side == "yes" else self.no_bid


@dataclass
class Bar:
    ts: int
    minute: int  # minutes elapsed since the hour opened
    seconds_left: float
    spot: float
    annual_vol: float
    impulse: float  # 3-minute BRTI move in dollars, the repo's `impulse`
    quotes: dict[float, Quote] = field(default_factory=dict)


@dataclass
class Hour:
    event_ticker: str
    open_ts: int
    close_ts: int
    settle: float
    results: dict[float, str]
    bars: list[Bar]

    def won(self, strike: float, side: str) -> bool | None:
        result = self.results.get(strike)
        if result not in ("yes", "no"):
            return None
        return (result == "yes") if side == "yes" else (result == "no")


def _f(value):
    return None if value is None else float(value)


def load_hours(
    db: Path = DEFAULT_DB,
    limit: int | None = None,
    min_volume: float = 0.0,
    slice_half: str = "",
) -> list[Hour]:
    """`slice_half` is "early" or "late": a calendar split for out-of-sample work.

    The split is on the median *hour*, not the median trade, so the two halves are
    equal stretches of wall clock whatever the signal frequency does.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    events = conn.execute(
        "SELECT event_ticker, open_ts, close_ts, settle_value FROM events"
        " WHERE settle_value IS NOT NULL ORDER BY close_ts"
    ).fetchall()
    if limit:
        events = events[-limit:]
    if slice_half:
        mid = len(events) // 2
        events = events[:mid] if slice_half == "early" else events[mid:]

    hours: list[Hour] = []
    for row in events:
        ev = row["event_ticker"]
        open_ts, close_ts, settle = int(row["open_ts"]), int(row["close_ts"]), float(row["settle_value"])

        spot_rows = conn.execute(
            "SELECT ts, value FROM spot WHERE event_ticker=? ORDER BY ts", (ev,)
        ).fetchall()
        if len(spot_rows) < 60:
            continue
        spot = {int(r["ts"]): float(r["value"]) for r in spot_rows}

        results = {
            float(r["strike"]): (r["result"] or "")
            for r in conn.execute(
                "SELECT strike, result FROM markets WHERE event_ticker=?", (ev,)
            )
        }

        by_ts: dict[int, dict[float, Quote]] = {}
        for q in conn.execute(
            "SELECT strike, ts, yes_bid_close, yes_ask_close, yes_bid_low, yes_bid_high,"
            " yes_ask_high, yes_ask_low, volume"
            " FROM quotes WHERE event_ticker=?",
            (ev,),
        ):
            ts = int(q["ts"])
            if ts <= open_ts or ts > close_ts:
                continue
            volume = float(q["volume"] or 0.0)
            if volume < min_volume:
                continue
            by_ts.setdefault(ts, {})[float(q["strike"])] = Quote(
                strike=float(q["strike"]),
                yes_bid=_f(q["yes_bid_close"]),
                yes_ask=_f(q["yes_ask_close"]),
                yes_bid_low=_f(q["yes_bid_low"]),
                yes_bid_high=_f(q["yes_bid_high"]),
                yes_ask_high=_f(q["yes_ask_high"]),
                yes_ask_low=_f(q["yes_ask_low"]),
                volume=volume,
            )
        if not by_ts:
            continue

        bars: list[Bar] = []
        for ts in sorted(by_ts):
            price = _spot_at(spot, ts)
            if price is None:
                continue
            minute_closes = [
                _spot_at(spot, ts - 60 * k) for k in range(VOL_LOOKBACK_MIN, -1, -1)
            ]
            closes = [p for p in minute_closes if p]
            vol = realized_annual_vol(closes, 60.0) or VOL_FLOOR
            three_min = _spot_at(spot, ts - 180)
            impulse = price - three_min if three_min else 0.0
            bars.append(
                Bar(
                    ts=ts,
                    minute=int(round((ts - open_ts) / 60)),
                    seconds_left=float(close_ts - ts),
                    spot=price,
                    annual_vol=vol,
                    impulse=impulse,
                    quotes=by_ts[ts],
                )
            )
        if len(bars) < 20:
            continue
        hours.append(Hour(ev, open_ts, close_ts, settle, results, bars))

    conn.close()
    return hours


def _spot_at(spot: dict[int, float], ts: int) -> float | None:
    """Last BRTI observation at or before `ts` (10-second grid, tolerate small gaps)."""
    bucket = (ts // 10) * 10
    for back in range(0, 13):
        value = spot.get(bucket - 10 * back)
        if value is not None:
            return value
    return None


# --------------------------------------------------------------------------- trades


@dataclass
class Trade:
    event_ticker: str
    close_ts: int
    ts: int
    minute: int
    strike: float
    side: str
    entry: float
    play: str
    won: bool
    net: float  # dollars per contract
    exit_price: float | None = None
    exit_reason: str = "settle"
    model_p: float | None = None
    cushion: float | None = None

    @property
    def cents(self) -> float:
        return self.net * 100.0


def settle_trade(hour: Hour, bar: Bar, strike: float, side: str, entry: float, play: str, **extra) -> Trade | None:
    won = hour.won(strike, side)
    if won is None:
        return None
    cost = entry + taker_fee(entry)
    net = (1.0 if won else 0.0) - cost
    return Trade(
        event_ticker=hour.event_ticker,
        close_ts=hour.close_ts,
        ts=bar.ts,
        minute=bar.minute,
        strike=strike,
        side=side,
        entry=entry,
        play=play,
        won=won,
        net=net,
        **extra,
    )


def close_trade(trade: Trade, exit_price: float, reason: str) -> Trade:
    """Re-price a settled trade as an early taker exit at `exit_price` (hit the bid)."""
    cost = trade.entry + taker_fee(trade.entry)
    proceeds, _fee = exit_proceeds(exit_price)
    return Trade(
        **{
            **trade.__dict__,
            "net": proceeds - cost,
            "exit_price": exit_price,
            "exit_reason": reason,
        }
    )


# --------------------------------------------------------------------------- stats


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Result:
    n: int
    clusters: int
    mean_cents: float
    t: float
    ci_lo: float
    ci_hi: float
    win_rate: float
    implied: float
    calib_pp: float
    first_half: float
    second_half: float
    per_day: float
    label: str = ""

    def row(self) -> str:
        return (
            f"{self.label:<34} n={self.n:>6} G={self.clusters:>5} "
            f"net={self.mean_cents:>+7.3f}c t={self.t:>+6.2f} "
            f"CI[{self.ci_lo:>+7.3f},{self.ci_hi:>+7.3f}] "
            f"win={self.win_rate:>6.2%} imp={self.implied:>6.2%} calib={self.calib_pp:>+5.2f}pp "
            f"H1={self.first_half:>+6.2f} H2={self.second_half:>+6.2f} n/day={self.per_day:>5.1f}"
        )


def clustered_t(values: list[float], clusters: list[str]) -> tuple[float, float, float, float]:
    """Mean, t, and a 95% CI using cluster sums (one cluster = one hour event)."""
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0.0, 0.0)
    mean = sum(values) / n
    groups: dict[str, list[float]] = {}
    for value, key in zip(values, clusters):
        groups.setdefault(key, []).append(value)
    g = len(groups)
    if g < 2:
        return (mean, 0.0, float("-inf"), float("inf"))
    # Var(mean) = sum_g (sum_i in g (x_i - mean))^2 / n^2, scaled by the small-G correction.
    total = 0.0
    for rows in groups.values():
        deviation = sum(value - mean for value in rows)
        total += deviation * deviation
    correction = g / (g - 1)
    var = correction * total / (n * n)
    se = math.sqrt(max(var, 0.0))
    if se <= 0:
        return (mean, 0.0, float("-inf"), float("inf"))
    return (mean, mean / se, mean - 1.96 * se, mean + 1.96 * se)


def summarize(trades: list[Trade], label: str = "", days: float | None = None) -> Result:
    if not trades:
        return Result(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, label)
    cents = [t.cents for t in trades]
    clusters = [t.event_ticker for t in trades]
    mean, t, lo, hi = clustered_t(cents, clusters)

    wins = sum(1 for t_ in trades if t_.won)
    win_rate = wins / len(trades)
    implied = sum(t_.entry for t_ in trades) / len(trades)

    ordered = sorted(trades, key=lambda t_: t_.close_ts)
    mid = len(ordered) // 2
    h1 = statistics.fmean([t_.cents for t_ in ordered[:mid]]) if mid else 0.0
    h2 = statistics.fmean([t_.cents for t_ in ordered[mid:]]) if len(ordered) - mid else 0.0

    if days is None:
        span = (ordered[-1].close_ts - ordered[0].close_ts) / 86400.0
        days = max(span, 1e-9)
    return Result(
        n=len(trades),
        clusters=len(set(clusters)),
        mean_cents=mean,
        t=t,
        ci_lo=lo,
        ci_hi=hi,
        win_rate=win_rate,
        implied=implied,
        calib_pp=(win_rate - implied) * 100.0,
        first_half=h1,
        second_half=h2,
        per_day=len(trades) / days,
        label=label,
    )


class Bucket:
    """Streaming accumulator for sweeps too big to hold as Trade objects."""

    __slots__ = ("label", "cents", "clusters", "wins", "entry_sum", "close_ts")

    def __init__(self, label: str = ""):
        self.label = label
        self.cents: list[float] = []
        self.clusters: list[str] = []
        self.wins = 0
        self.entry_sum = 0.0
        self.close_ts: list[int] = []

    def add(self, cluster: str, cents: float, won: bool, entry: float, close_ts: int) -> None:
        self.cents.append(cents)
        self.clusters.append(cluster)
        self.close_ts.append(close_ts)
        self.wins += 1 if won else 0
        self.entry_sum += entry

    def __len__(self) -> int:
        return len(self.cents)

    def result(self, days: float | None = None) -> Result:
        n = len(self.cents)
        if n == 0:
            return Result(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, self.label)
        mean, t, lo, hi = clustered_t(self.cents, self.clusters)
        order = sorted(range(n), key=lambda i: self.close_ts[i])
        mid = n // 2
        h1 = statistics.fmean([self.cents[i] for i in order[:mid]]) if mid else 0.0
        h2 = statistics.fmean([self.cents[i] for i in order[mid:]]) if n - mid else 0.0
        if days is None:
            days = max((max(self.close_ts) - min(self.close_ts)) / 86400.0, 1e-9)
        win_rate = self.wins / n
        implied = self.entry_sum / n
        return Result(
            n=n,
            clusters=len(set(self.clusters)),
            mean_cents=mean,
            t=t,
            ci_lo=lo,
            ci_hi=hi,
            win_rate=win_rate,
            implied=implied,
            calib_pp=(win_rate - implied) * 100.0,
            first_half=h1,
            second_half=h2,
            per_day=n / days,
            label=self.label,
        )


def net_cents(entry: float, won: bool) -> float:
    """Taker buy at `entry`, held to settlement, in cents per contract."""
    return ((1.0 if won else 0.0) - entry - taker_fee(entry)) * 100.0


def sample_days(hours: list[Hour]) -> float:
    if not hours:
        return 0.0
    return max((hours[-1].close_ts - hours[0].close_ts) / 86400.0, 1e-9)


# --------------------------------------------------------------------------- helpers


def model_p(bar: Bar, strike: float, side: str) -> float:
    p_yes = digital_prob(bar.spot, strike, bar.seconds_left, bar.annual_vol, minute=bar.minute)
    return p_yes if side == "yes" else 1.0 - p_yes


def cushion(bar: Bar, strike: float) -> float:
    return sigma_cushion(bar.spot, strike, bar.seconds_left, bar.annual_vol, minute=bar.minute)


def favorite_side(bar: Bar, strike: float) -> str:
    """The side the tape already points at: spot above the strike means YES."""
    return "yes" if bar.spot > strike else "no"

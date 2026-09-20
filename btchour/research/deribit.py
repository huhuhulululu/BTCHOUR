"""Deribit public data: the volatility index, option price history, settlements.

H3 is the last mechanism left standing after 021's ranking, and decision 021
ranked it third for a reason: capturing a variance premium usually needs a
delta-hedging model, and this repo does not get to invent one. There is a way
round that, and it is the same trick that made `venue.py` and `carry.py` work.

Deribit options are European and cash-settled on the published index. So for a
short option held to expiry,

    pnl_in_coin = premium_received - max(0, S_T - K) / S_T     (a call)
    pnl_in_coin = premium_received - max(0, K - S_T) / S_T     (a put)

Every term is published: the premium is a price that traded, `S_T` is the
settlement index. No hedge schedule, no Greeks, no fill rule. What that buys is
a tradeable structure measured as arithmetic; what it costs is that a
held-to-expiry straddle is short the *terminal* move, not the path variance, so
it answers "is the terminal-move distribution priced rich" rather than "is
realized variance below implied". The DVOL leg answers the second question, and
the two together are the test.

**The one trap in this data.** `get_tradingview_chart_data` keeps returning
bars long after an instrument expires, carrying the last value forward at zero
volume -- `BTC-27JUN25-100000-C` still reports a close in September 2026. A
zero-volume bar is not a price, it is the same phantom print that made the
Kalshi maker scan look profitable in decision 018. `option_history` therefore
drops every bar at or after expiry and every bar with no volume, and says how
many it dropped.
"""

from __future__ import annotations

import calendar
import math
import datetime as dt
import json
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass

BASE = "https://www.deribit.com/api/v2/public"
DATA_ROOT = os.path.join("data", "deribit")

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Deribit expires options at 08:00 UTC.
EXPIRY_HOUR = 8


def _get(url: str, tries: int = 5):
    """GET and parse, retrying transport failures but not rejections.

    Deribit answers a request for an instrument that never existed with HTTP
    400 and a JSON error body. That is an answer, not a failure: strike grids
    have to be probed here because the expired-instrument endpoint only ever
    returns the latest expiry. Retrying it five times would turn every missing
    strike into a minute of backoff, so a 4xx is parsed and returned and the
    caller reads `error` from it.
    """
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btchour/deribit"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code != 429:
                try:
                    return json.load(exc)
                except Exception:  # noqa: BLE001 -- fall through to the retry
                    return {"error": {"code": exc.code, "message": str(exc)}}
            last = exc
            time.sleep(min(20, 1.6 ** i))
        except Exception as exc:  # noqa: BLE001 -- retried
            last = exc
            time.sleep(min(20, 1.6 ** i))
    raise last


def _cache(path: str, build):
    full = os.path.join(DATA_ROOT, path)
    if os.path.exists(full) and os.path.getsize(full) > 2:
        return json.load(open(full))
    out = build()
    os.makedirs(os.path.dirname(full), exist_ok=True)
    json.dump(out, open(full, "w"))
    return out


def last_friday(year: int, month: int) -> dt.datetime:
    """Deribit's monthly expiry: the last Friday of the month at 08:00 UTC."""
    last = calendar.monthrange(year, month)[1]
    d = dt.datetime(year, month, last, EXPIRY_HOUR)
    while d.weekday() != 4:  # Friday
        d -= dt.timedelta(days=1)
    return d


def monthly_expiries(start: str, end: str) -> list[dt.datetime]:
    y, m = (int(x) for x in start.split("-"))
    Y, M = (int(x) for x in end.split("-"))
    out = []
    while (y, m) <= (Y, M):
        out.append(last_friday(y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def instrument_name(currency: str, expiry: dt.datetime, strike: float, kind: str) -> str:
    """`BTC-26JUN26-100000-C`. Deribit drops the leading zero on the day."""
    k = ("%g" % strike) if float(strike) != int(strike) else "%d" % int(strike)
    return "%s-%d%s%02d-%s-%s" % (
        currency,
        expiry.day,
        _MONTHS[expiry.month - 1],
        expiry.year % 100,
        k,
        kind.upper(),
    )


def dvol_history(
    currency: str,
    start: str = "2021-01",
    end: str = "2026-10",
    *,
    field: str = "open",
) -> dict[int, float]:
    """Daily DVOL by timestamp, annualized implied volatility in percent.

    The default is the bar's **open**, not its close, and that choice is the
    difference between a clean test and a one-day look-ahead. Deribit stamps a
    daily bar at its start, so the bar labelled day D spans D 00:00 -> D+1
    00:00 and its close is a number nobody has at D 00:00. The open is the
    reading at exactly the stamp, which is the instant the paired realized-vol
    window begins.
    """
    cols = {"open": 1, "high": 2, "low": 3, "close": 4}
    if field not in cols:
        raise ValueError(f"field must be one of {sorted(cols)}")

    def build():
        a = int(dt.datetime(*map(int, (start + "-01").split("-"))).timestamp() * 1000)
        b = int(dt.datetime(*map(int, (end + "-01").split("-"))).timestamp() * 1000)
        rows: dict[str, list] = {}
        # 300 days at a time: the endpoint caps how much it will return.
        step = 300 * 86400_000
        t = a
        while t < b:
            d = _get(
                f"{BASE}/get_volatility_index_data?currency={currency}"
                f"&start_timestamp={t}&end_timestamp={min(b, t + step)}&resolution=86400"
            )
            for row in d.get("result", {}).get("data", []):
                rows[str(row[0])] = list(row[1:5])
            t += step
        return rows

    raw = _cache(f"dvol_{currency}_ohlc.json", build)
    i = cols[field] - 1
    return {int(k): v[i] for k, v in raw.items()}


def index_history(currency: str, start: str = "2021-01", end: str = "2026-10") -> dict[int, float]:
    """Daily close of the perpetual, used as the settlement reference."""

    def build():
        a = int(dt.datetime(*map(int, (start + "-01").split("-"))).timestamp() * 1000)
        b = int(dt.datetime(*map(int, (end + "-01").split("-"))).timestamp() * 1000)
        rows: dict[str, float] = {}
        step = 300 * 86400_000
        t = a
        while t < b:
            d = _get(
                f"{BASE}/get_tradingview_chart_data"
                f"?instrument_name={currency}-PERPETUAL"
                f"&start_timestamp={t}&end_timestamp={min(b, t + step)}&resolution=1D"
            )
            r = d.get("result", {})
            for ts, close in zip(r.get("ticks", []), r.get("close", [])):
                rows[str(ts)] = close
            t += step
        return rows

    raw = _cache(f"index_{currency}.json", build)
    return {int(k): v for k, v in raw.items()}


@dataclass(frozen=True)
class OptionBar:
    ts: int
    close: float  # in units of the underlying coin, Deribit's convention
    volume: float


def option_history(
    instrument: str, expiry: dt.datetime, *, cache: bool = True
) -> tuple[list[OptionBar], int]:
    """Traded daily bars strictly before expiry, zero-volume bars removed.

    Returns (bars, n_dropped). The dropped count matters: Deribit carries the
    last value forward at zero volume for months after an instrument expires,
    and a carried value is a phantom print, not a price anybody could have
    sold at.
    """
    exp_ms = int(expiry.timestamp() * 1000)

    def build():
        a = exp_ms - 400 * 86400_000
        d = _get(
            f"{BASE}/get_tradingview_chart_data?instrument_name={instrument}"
            f"&start_timestamp={a}&end_timestamp={exp_ms + 86400_000}&resolution=1D"
        )
        r = d.get("result") or {}
        if d.get("error") or r.get("status") != "ok":
            # The instrument was never listed, or has no chart data at all.
            return {"ticks": [], "close": [], "volume": []}
        return {
            "ticks": r.get("ticks", []),
            "close": r.get("close", []),
            "volume": r.get("volume", []),
        }

    raw = _cache(f"opt/{instrument}.json", build) if cache else build()
    bars: list[OptionBar] = []
    dropped = 0
    for ts, close, vol in zip(raw["ticks"], raw["close"], raw["volume"]):
        if ts >= exp_ms:
            dropped += 1
            continue
        if not vol or vol <= 0:
            dropped += 1
            continue
        if close is None or close <= 0:
            dropped += 1
            continue
        bars.append(OptionBar(ts=ts, close=close, volume=vol))
    bars.sort(key=lambda b: b.ts)
    return bars, dropped


def option_book(instrument: str) -> dict | None:
    """Live top of book for one option, to measure what selling it really costs."""
    d = _get(f"{BASE}/ticker?instrument_name={instrument}")
    r = d.get("result")
    if not r:
        return None
    bid, ask = r.get("best_bid_price"), r.get("best_ask_price")
    mark = r.get("mark_price")
    if not bid or not ask or not mark or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return {
        "instrument": instrument,
        "bid": bid,
        "ask": ask,
        "mark": mark,
        "mid": mid,
        # Relative to mid, because an option's price is small and a spread in
        # coin terms says nothing without it.
        "spread_pct_of_mid": (ask - bid) / mid * 100 if mid > 0 else None,
        "bid_size": r.get("best_bid_amount"),
        "underlying": r.get("underlying_price"),
    }


def live_instruments(currency: str, kind: str = "option") -> list[dict]:
    d = _get(f"{BASE}/get_instruments?currency={currency}&kind={kind}&expired=false")
    return d.get("result", []) or []


def delivery_prices(index_name: str = "btc_usd", *, want: int = 3000) -> dict[str, float]:
    """The real settlement index by date, `{"2026-09-18": 76422.2}`.

    This is the number Deribit actually settles options against at 08:00 UTC,
    published, so a held-to-expiry payoff needs no proxy for `S_T` and no
    assumption about which venue's price counts.
    """

    def build():
        rows: dict[str, float] = {}
        off = 0
        while off < want:
            d = _get(
                f"{BASE}/get_delivery_prices?index_name={index_name}"
                f"&offset={off}&count=1000"
            )
            r = d.get("result", {})
            data = r.get("data", []) or []
            if not data:
                break
            for row in data:
                rows[row["date"]] = row["delivery_price"]
            total = r.get("records_total", 0)
            off += len(data)
            if off >= total:
                break
        return rows

    return _cache(f"delivery_{index_name}.json", build)


# Deribit's listed strikes step by different amounts at different price levels
# and tenors, and the expired-instrument endpoint only ever returns the latest
# expiry, so the grid has to be probed rather than looked up.
#
# The step has to scale with the price. A fixed grid in dollars is the same
# mistake as annualizing funding by a fixed interval: 500 is a fine step for
# BTC at 80,000 and a 20% step for ETH at 2,500, and a "straddle" struck 20%
# away is not measuring volatility, it is measuring direction. These are
# fractions of spot, snapped to a round number.
_STRIKE_FRACTIONS = (0.005, 0.01, 0.02, 0.025, 0.05, 0.1)


def _nice(x: float) -> float:
    """The nearest 1/2/2.5/5 x 10^k at or below `x`, which is how venues pick
    strike increments."""
    if x <= 0:
        return 1.0
    mag = 10.0 ** math.floor(math.log10(x))
    for m in (5.0, 2.5, 2.0, 1.0):
        if m * mag <= x:
            return m * mag
    return mag


def strike_candidates(spot: float, *, limit: int = 16) -> list[float]:
    """Plausible listed strikes, nearest the money first."""
    out: set[float] = set()
    for frac in _STRIKE_FRACTIONS:
        step = _nice(spot * frac)
        base = round(spot / step) * step
        for k in (-2, -1, 0, 1, 2):
            v = round(base + k * step, 6)
            if v > 0:
                out.add(int(v) if float(v).is_integer() else v)
    return sorted(out, key=lambda s: abs(s - spot))[:limit]


def bar_near(bars: list[OptionBar], target_ms: int, tol_days: int = 3) -> OptionBar | None:
    """The traded bar closest to `target_ms`, or None if none is close enough.

    Only bars that actually traded are in `bars`, so this either finds a real
    price or reports that the instrument was not trading then. It never
    interpolates one.
    """
    tol = tol_days * 86400_000
    best = None
    for b in bars:
        d = abs(b.ts - target_ms)
        if d <= tol and (best is None or d < abs(best.ts - target_ms)):
            best = b
    return best

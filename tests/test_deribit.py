"""Deribit's naming, its strike grid, and the padding trap in its chart data.

None of these hit the network. What they pin down is the part of the adapter
that silently produces plausible wrong answers: an instrument name that is off
by a character returns no data and looks like an illiquid month, a strike grid
in the wrong units returns a deep out-of-the-money option and looks like a
straddle, and a chart series padded past expiry returns a price nobody could
have traded and looks like profit.
"""

from __future__ import annotations

import datetime as dt

from btchour.research import deribit as D


def test_last_friday_is_a_friday_at_eight_utc():
    for y in (2021, 2024, 2026):
        for m in range(1, 13):
            d = D.last_friday(y, m)
            assert d.weekday() == 4
            assert d.hour == D.EXPIRY_HOUR
            # and it really is the last one: a week later is the next month
            assert (d + dt.timedelta(days=7)).month != m or d.year != y


def test_monthly_expiries_span_the_range_inclusively():
    xs = D.monthly_expiries("2025-11", "2026-02")
    assert [(x.year, x.month) for x in xs] == [
        (2025, 11), (2025, 12), (2026, 1), (2026, 2)
    ]


def test_instrument_name_matches_deribit_spelling():
    e = D.last_friday(2025, 6)
    assert D.instrument_name("BTC", e, 100000, "C") == "BTC-27JUN25-100000-C"
    assert D.instrument_name("ETH", e, 2500, "p") == "ETH-27JUN25-2500-P"


def test_instrument_name_drops_the_leading_zero_on_the_day():
    e = D.last_friday(2026, 5)  # 2026-05-29
    assert D.instrument_name("BTC", e, 1000, "C").split("-")[1] == "29MAY26"
    e = D.last_friday(2021, 1)  # 2021-01-29
    assert "29JAN21" in D.instrument_name("BTC", e, 1000, "C")


def test_fractional_strikes_render_without_a_trailing_zero():
    e = D.last_friday(2025, 6)
    assert D.instrument_name("XRP", e, 2.5, "C").split("-")[2] == "2.5"
    assert D.instrument_name("XRP", e, 3.0, "C").split("-")[2] == "3"


def test_strike_grid_scales_with_the_price():
    """A grid in fixed dollars is the trap: 500 is a fine step at 80,000 and a
    20% step at 2,500, and a straddle struck 20% away measures direction."""
    for spot in (81000.0, 31000.0, 4200.0, 2500.0, 380.0, 0.55):
        cands = D.strike_candidates(spot)
        assert cands
        nearest = min(abs(k - spot) for k in cands)
        assert nearest / spot < 0.01  # something is within 1% of the money
        assert cands[0] == min(cands, key=lambda k: abs(k - spot))


def test_strike_grid_is_ordered_by_distance_from_the_money():
    c = D.strike_candidates(81000.0)
    devs = [abs(k - 81000.0) for k in c]
    assert devs == sorted(devs)


def test_nice_step_snaps_to_venue_style_increments():
    assert D._nice(1000.0) == 1000.0
    assert D._nice(900.0) == 500.0
    assert D._nice(2600.0) == 2500.0
    assert D._nice(0.037) == 0.025


def test_bar_near_finds_the_closest_traded_bar_and_never_invents_one():
    bars = [D.OptionBar(ts=i * 86400_000, close=0.05, volume=1.0) for i in (0, 5, 9)]
    assert D.bar_near(bars, 4 * 86400_000, tol_days=3).ts == 5 * 86400_000
    # nothing within tolerance: say so rather than reach further
    assert D.bar_near(bars, 20 * 86400_000, tol_days=3) is None
    assert D.bar_near([], 0) is None


def test_option_history_drops_post_expiry_and_zero_volume_bars(monkeypatch):
    """Deribit carries the last value forward at zero volume for months after
    an instrument expires. A carried value is a phantom print, and selling one
    is the same free money that made the Kalshi maker scan look profitable."""
    exp = D.last_friday(2025, 6)
    exp_ms = int(exp.timestamp() * 1000)
    day = 86400_000
    fake = {
        "ticks":  [exp_ms - 2 * day, exp_ms - day, exp_ms, exp_ms + 30 * day],
        "close":  [0.070,            0.065,        0.0655, 0.0655],
        "volume": [12.0,             0.0,          0.0,    0.0],
    }
    monkeypatch.setattr(D, "_get", lambda *a, **k: {"result": dict(fake, status="ok")})
    bars, dropped = D.option_history("BTC-27JUN25-100000-C", exp, cache=False)
    assert [b.ts for b in bars] == [exp_ms - 2 * day]
    assert dropped == 3


def test_option_history_treats_a_rejected_instrument_as_no_data(monkeypatch):
    """A strike that was never listed answers with an error body, not a crash:
    the grid has to be probed, so a rejection is an answer."""
    exp = D.last_friday(2025, 6)
    monkeypatch.setattr(
        D, "_get", lambda *a, **k: {"error": {"code": 400, "message": "not found"}}
    )
    bars, dropped = D.option_history("BTC-27JUN25-123456-C", exp, cache=False)
    assert bars == [] and dropped == 0


def test_dvol_defaults_to_the_bar_open(monkeypatch):
    """The close of the bar stamped day D is a number nobody has at D 00:00.
    Pairing it with a window that starts at D is a one-day look-ahead."""
    rows = {"1616544000000": [95.0, 96.0, 90.0, 91.0]}
    monkeypatch.setattr(D, "_cache", lambda path, build: rows)
    assert D.dvol_history("BTC")[1616544000000] == 95.0
    assert D.dvol_history("BTC", field="close")[1616544000000] == 91.0


def test_dvol_rejects_an_unknown_field():
    try:
        D.dvol_history("BTC", field="vwap")
    except ValueError as e:
        assert "field" in str(e)
        return
    raise AssertionError("an unknown field must raise, not pick one silently")

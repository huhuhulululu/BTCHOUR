"""H3's arithmetic, and the three traps it is built to avoid.

The premium is measured as a difference of two published numbers, so most of
what can go wrong here is alignment rather than algebra: pairing an implied
reading with a realized window that started before it, tiling windows that
overlap, or annualizing the two legs by different factors. Those get tests.
"""

from __future__ import annotations

import math

from btchour.research import volprem as V

DAY = V.DAY_MS


def flat(n, start=0, step=DAY):
    return {start + i * step: 100.0 for i in range(n)}


def alternating(n, pct, start=0, step=DAY):
    """A price that jumps +/-pct every bar: realized vol is known in closed form."""
    out = {}
    p = 100.0
    for i in range(n):
        out[start + i * step] = p
        p = p * (1 + pct) if i % 2 == 0 else p / (1 + pct)
    return out


def test_flat_price_has_zero_realized_vol():
    rv, n = V.realized_vol(flat(40), 0, 30 * DAY)
    assert n == 30
    assert rv == 0.0


def test_realized_vol_annualizes_by_the_sampling_frequency():
    """The same path sampled hourly and labelled hourly must annualize to the
    same number as when it is sampled daily and labelled daily."""
    d = alternating(31, 0.01, step=DAY)
    h = alternating(31, 0.01, step=3_600_000)
    a, _ = V.realized_vol(d, 0, 30 * DAY, periods_per_year=365.0)
    b, _ = V.realized_vol(h, 0, 30 * 3_600_000, periods_per_year=365.0 * 24.0)
    assert abs(a / math.sqrt(365.0) - b / math.sqrt(365.0 * 24.0)) < 1e-9


def test_realized_vol_needs_enough_returns():
    rv, n = V.realized_vol(flat(3), 0, 30 * DAY)
    assert math.isnan(rv) and n == 0
    rv, n = V.realized_vol(flat(40), 0, 30 * DAY, min_returns=400)
    assert math.isnan(rv)


def test_premium_is_implied_minus_realized():
    w = V.VolWindow("BTC", 0, 30 * DAY, implied=60.0, realized=50.0, n_returns=30)
    assert abs(w.vol_premium - 10.0) < 1e-12
    # variance units: (60^2 - 50^2) / (2*60)
    assert abs(w.var_swap_ret - (3600 - 2500) / 120.0) < 1e-12


def test_var_swap_and_vol_premium_disagree_in_sign_nowhere_but_in_size():
    """Both are zero together and share a sign, but the variance figure is the
    one a short variance position is paid, and it is larger when vol is high."""
    hi = V.VolWindow("BTC", 0, 1, 80.0, 70.0, 30)
    lo = V.VolWindow("BTC", 0, 1, 30.0, 20.0, 30)
    assert abs(hi.vol_premium - lo.vol_premium) < 1e-12  # same 10 vol points
    assert hi.var_swap_ret > lo.var_swap_ret  # not the same variance premium
    flat_ = V.VolWindow("BTC", 0, 1, 50.0, 50.0, 30)
    assert flat_.vol_premium == 0.0 and flat_.var_swap_ret == 0.0


def test_windows_never_overlap():
    dvol = {i * DAY: 60.0 for i in range(200)}
    ws = V.build_windows("BTC", dvol, flat(200), hold_days=30)
    assert len(ws) >= 5
    for a, b in zip(ws, ws[1:]):
        assert a.t1 <= b.t0


def test_window_is_skipped_when_the_implied_reading_is_missing():
    """No index on that exact day means no window. Reaching for a neighbour
    would quietly pair an implied level with the wrong month."""
    dvol = {i * DAY: 60.0 for i in range(200)}
    del dvol[30 * DAY]
    ws = V.build_windows("BTC", dvol, flat(200), hold_days=30)
    assert all(w.t0 != 30 * DAY for w in ws)
    # and the tape resumes rather than stopping
    assert any(w.t0 == 60 * DAY for w in ws)


def test_realized_window_never_starts_before_the_implied_reading():
    """Doctoring prices before t0 must not move a window's realized vol."""
    dvol = {i * DAY: 60.0 for i in range(200)}
    px = flat(200)
    a = V.build_windows("BTC", dvol, px, hold_days=30, start_ms=60 * DAY)
    px2 = dict(px)
    for i in range(0, 60):
        px2[i * DAY] = 5.0  # violent history, entirely before the first window
    b = V.build_windows("BTC", dvol, px2, hold_days=30, start_ms=60 * DAY)
    assert [w.realized for w in a] == [w.realized for w in b]


def test_hourly_prices_and_daily_implied_still_build_windows():
    """The two series live on different grids; an intersection would be empty."""
    dvol = {i * DAY: 60.0 for i in range(120)}
    hourly = {i * 3_600_000: 100.0 for i in range(120 * 24)}
    ws = V.build_windows(
        "BTC", dvol, hourly, hold_days=30, periods_per_year=365.0 * 24, min_returns=400
    )
    assert ws and ws[0].n_returns > 700


def test_straddle_pnl_is_premium_minus_the_inverse_settled_payoff():
    s = V.Straddle(
        "BTC", 0, 0, strike=100, spot_entry=100.0, settle=120.0,
        call_premium=0.06, put_premium=0.05, call_volume=1.0, put_volume=1.0,
    )
    assert abs(s.premium - 0.11) < 1e-12
    assert abs(s.payoff - 20.0 / 120.0) < 1e-12  # inverse settled: divide by S_T
    assert abs(s.pnl - (0.11 - 20.0 / 120.0)) < 1e-12


def test_straddle_settling_at_the_strike_keeps_the_whole_premium():
    s = V.Straddle(
        "BTC", 0, 0, 100, 100.0, 100.0, 0.06, 0.05, 1.0, 1.0,
    )
    assert s.payoff == 0.0
    assert abs(s.pnl - 0.11) < 1e-12


def test_spread_is_charged_on_the_premium_not_the_payoff():
    s = V.Straddle("BTC", 0, 0, 100, 100.0, 120.0, 0.06, 0.05, 1.0, 1.0)
    # 10% of mid quoted, so half of that crossed
    after = s.pnl_after_spread(10.0)
    assert after < s.pnl
    assert abs(after - (0.11 * 0.95 - 20.0 / 120.0)) < 1e-12
    assert abs(s.pnl_after_spread(0.0) - s.pnl) < 1e-12


def test_moneyness_is_signed_off_the_entry_spot():
    s = V.Straddle("BTC", 0, 0, 110, 100.0, 100.0, 0.0, 0.0, 1.0, 1.0)
    assert abs(s.moneyness - 0.10) < 1e-12


def test_summaries_on_empty_input_are_not_errors():
    assert V.summarize([])["n"] == 0
    assert V.summarize_straddles([])["n"] == 0


def test_summarize_counts_windows_as_the_unit():
    dvol = {i * DAY: 60.0 for i in range(400)}
    ws = V.build_windows("BTC", dvol, flat(400), hold_days=30)
    s = V.summarize(ws, label="x")
    assert s["n"] == len(ws)
    assert s["realized_mean"] == 0.0
    assert abs(s["vol_premium_mean"] - 60.0) < 1e-9

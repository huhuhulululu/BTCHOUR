"""The carry decomposition has to be an identity, not an estimate.

Decision 016's lesson was that a number which looks right is worth nothing
without a test that the arithmetic closes. So the first test here is the
identity itself: the three components must add back to the P&L computed
straight from the four prices, with no slack.
"""

from __future__ import annotations

import os
import tempfile

from btchour.research import carry


HOUR = carry.HOUR_MS
DAY = carry.DAY_MS


def _flat(t0: int, n: int, price: float) -> dict[int, float]:
    return {t0 + i * HOUR: price for i in range(n)}


def test_norm_ts_handles_both_eras():
    # 2024-12 spot file, milliseconds.
    assert carry._norm_ts("1733011200000") == 1733011200000
    # 2025-01 spot file, microseconds for the same kind of bar.
    assert carry._norm_ts("1735689600000000") == 1735689600000


def test_rows_drops_header_only_when_present():
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "a.csv")
        with open(a, "w") as f:
            f.write("open_time,open,high,low,close\n1,2,3,4,5\n")
        b = os.path.join(d, "b.csv")
        with open(b, "w") as f:
            f.write("1,2,3,4,5\n6,7,8,9,10\n")
        assert carry._rows(a) == [["1", "2", "3", "4", "5"]]
        assert len(carry._rows(b)) == 2


def _one_window(**over):
    t0 = 0
    n = 24 * 40
    spot = dict(_flat(t0, n, 100.0))
    perp = dict(_flat(t0, n, 100.0))
    spot.update(over.get("spot", {}))
    perp.update(over.get("perp", {}))
    mark = dict(perp)
    funding = over.get("funding", [])
    return carry.build_windows(
        "X",
        over.get("hold_days", 30),
        spot=spot,
        perp=perp,
        mark=mark,
        funding=funding,
        costs=over.get("costs", carry.Costs()),
    )


def test_decomposition_is_an_identity():
    """funding + basis - cost must equal the raw four-price P&L exactly."""
    spot = {30 * DAY: 120.0}
    perp = {0: 101.0, 30 * DAY: 119.0}
    stamps = [
        carry.Funding(ts=h * 8 * HOUR, rate=0.0001, interval_hours=8.0)
        for h in range(1, 91)
    ]
    wins = _one_window(spot=spot, perp=perp, funding=stamps)
    assert wins
    w = wins[0]
    raw = (w.spot_out - w.spot_in) - (w.perp_out - w.perp_in) + w.funding_usd
    assert abs(raw - w.gross_usd) < 1e-9
    assert abs(w.net_usd - (raw - w.cost_usd)) < 1e-9


def test_basis_term_is_not_assumed_zero():
    """Entering rich and exiting flat pays the basis; 021's account missed it."""
    wins = _one_window(perp={0: 105.0})
    w = wins[0]
    assert abs(w.basis_in - 5.0) < 1e-9
    assert abs(w.basis_out - 0.0) < 1e-9
    assert w.basis_usd > 4.9  # the whole 5 dollars, net of nothing


def test_entering_cheap_loses_the_basis():
    """A positive funding tape still loses when the basis goes against us."""
    stamps = [
        carry.Funding(ts=h * 8 * HOUR, rate=0.0001, interval_hours=8.0)
        for h in range(1, 91)
    ]
    wins = _one_window(perp={0: 99.0, 30 * DAY: 101.0}, funding=stamps)
    w = wins[0]
    assert w.funding_usd > 0
    assert w.basis_usd < 0
    assert w.net_usd < 0  # -2 of basis swamps +0.27 of funding


def test_short_perp_receives_positive_funding_and_pays_negative():
    pos = _one_window(
        funding=[carry.Funding(ts=8 * HOUR, rate=0.001, interval_hours=8.0)]
    )[0]
    neg = _one_window(
        funding=[carry.Funding(ts=8 * HOUR, rate=-0.001, interval_hours=8.0)]
    )[0]
    assert pos.funding_usd > 0
    assert neg.funding_usd < 0
    assert abs(pos.funding_usd + neg.funding_usd) < 1e-12


def test_funding_stamps_outside_the_window_are_not_paid():
    inside = carry.Funding(ts=8 * HOUR, rate=0.001, interval_hours=8.0)
    after = carry.Funding(ts=40 * DAY, rate=0.001, interval_hours=8.0)
    w = _one_window(funding=[inside, after])[0]
    assert w.n_stamps == 1


def test_entry_stamp_is_excluded_exit_stamp_included():
    """A funding settlement at t0 belongs to whoever held before us."""
    at_entry = carry.Funding(ts=0, rate=0.001, interval_hours=8.0)
    at_exit = carry.Funding(ts=30 * DAY, rate=0.001, interval_hours=8.0)
    w = _one_window(funding=[at_entry, at_exit])[0]
    assert w.n_stamps == 1
    assert w.funding_usd > 0


def test_costs_are_four_executions():
    c = carry.Costs(spot_bp=10.0, perp_bp=2.0)
    assert c.round_trip_bp() == 24.0
    w = _one_window(costs=c)[0]
    # All four prices are 100, so cost = 2*100*10bp + 2*100*2bp = 0.24.
    assert abs(w.cost_usd - 0.24) < 1e-9
    assert abs(w.cost_ret - 0.0024) < 1e-9


def test_windows_do_not_overlap():
    wins = _one_window(hold_days=5)
    assert len(wins) >= 7
    for a, b in zip(wins, wins[1:]):
        assert a.t1 == b.t0  # back to back, never sharing an hour of tape


def test_window_dropped_when_a_leg_is_missing_rather_than_filled_forward():
    t0 = 0
    n = 24 * 40
    spot = _flat(t0, n, 100.0)
    perp = _flat(t0, n, 100.0)
    del perp[10 * DAY]  # the exit hour of the first 10-day window
    wins = carry.build_windows(
        "X",
        10,
        spot=spot,
        perp=perp,
        mark=dict(perp),
        funding=[],
    )
    assert all(w.t1 != 10 * DAY for w in wins)


def test_annualization():
    w = _one_window(hold_days=30)[0]
    assert abs(w.net_annual - w.net_ret * 365 / 30) < 1e-12


def test_concentration_flags_a_mean_carried_by_a_few_windows():
    spiky = [-0.01] * 20 + [1.0]
    assert carry._concentration(spiky, 5) > 0.9
    even = [0.01] * 50
    assert carry._concentration(even, 5) < 0.2


def test_concentration_returns_one_when_total_is_negative():
    assert carry._concentration([-1.0, 0.5], 5) == 1.0


def test_summarize_reports_excess_over_the_tbill():
    wins = _one_window(hold_days=30)
    s = carry.summarize(wins, label="t")
    assert s["n"] == len(wins)
    assert abs(s["excess_annual"] - (s["net_annual_mean"] - carry.TBILL_ANNUAL)) < 1e-12
    assert s["net_annual_ci"][0] <= s["net_annual_mean"] <= s["net_annual_ci"][1]


def test_summarize_on_empty_is_not_an_error():
    assert carry.summarize([], label="x")["n"] == 0


def test_hold_days_must_be_positive():
    try:
        _one_window(hold_days=0)
    except ValueError:
        return
    raise AssertionError("hold_days=0 should raise")


def test_bar_root_keys_on_the_interval():
    """Two intervals of one symbol must not share a cache path.

    They did once, and an hourly file sat behind a daily-looking call with no
    error anywhere: the average of 365 *hours* of volume simply looked like a
    quiet coin, and put major names in the wrong liquidity bucket.
    """
    h = carry.bar_root("klines", "AVAXUSDT", "1h", root="r")
    d = carry.bar_root("klines", "AVAXUSDT", "1d", root="r")
    assert h != d
    assert h.endswith(os.path.join("1h", "AVAXUSDT"))
    assert d.endswith(os.path.join("1d", "AVAXUSDT"))
    # Funding is not on a bar grid we choose, so it gets its own fixed slot.
    assert carry.bar_root("fundingRate", "X", "1h", root="r") == carry.bar_root(
        "fundingRate", "X", "1d", root="r"
    )


def _write_bars(root, kind, interval, symbol, step_ms, n):
    d = carry.bar_root(kind, symbol, interval, root=root)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "2026-01.csv"), "w") as f:
        for i in range(n):
            t = i * step_ms
            f.write(f"{t},1,1,1,{100 + i},1,{t + step_ms - 1},1000,10,1,1,0\n")


def test_load_closes_rejects_a_mislabelled_interval():
    with tempfile.TemporaryDirectory() as root:
        # Hourly bars sitting in the 1d slot: the exact failure that happened.
        _write_bars(root, "klines", "1d", "X", carry.HOUR_MS, 200)
        try:
            carry.load_closes("klines", "X", interval="1d", root=root)
        except ValueError as e:
            assert "spaced" in str(e)
            return
        raise AssertionError("a mislabelled interval must not load silently")


def test_load_closes_accepts_the_matching_interval():
    with tempfile.TemporaryDirectory() as root:
        _write_bars(root, "klines", "1h", "X", carry.HOUR_MS, 200)
        out = carry.load_closes("klines", "X", interval="1h", root=root)
        assert len(out) == 200
        _write_bars(root, "klines", "1d", "Y", carry.DAY_MS, 200)
        assert len(carry.load_closes("klines", "Y", interval="1d", root=root)) == 200

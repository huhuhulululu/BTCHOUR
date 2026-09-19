"""The cost and capacity layer is the whole contribution, so it gets the tests.

Two of these guard against the failure modes that make published crypto
cross-sections look profitable: a coin that disappears mid-period being
dropped from the sample rather than counted, and a bootstrap that treats
each coin-period as an independent draw.
"""

from __future__ import annotations

from btchour.research import xsection as X
from btchour.research.carry import DAY_MS


def bars(prices, vols=None, start=0):
    vols = vols or [1e6] * len(prices)
    return [
        X.Bar(
            ts=start + i * DAY_MS,
            open=p,
            high=p,
            low=p,
            close=p,
            quote_volume=v,
            trades=100,
        )
        for i, (p, v) in enumerate(zip(prices, vols))
    ]


def test_reversal_is_the_negative_past_return():
    b = bars([100.0] * 7 + [110.0])
    assert X.signal_reversal(b, 7, 7) is not None
    assert X.signal_reversal(b, 7, 7) < 0  # it went up, so reversal says short


def test_momentum_is_the_past_return():
    b = bars([100.0] * 7 + [110.0])
    assert X.signal_momentum(b, 7, 7) > 0


def test_size_signal_is_high_for_small_coins():
    big = bars([100.0] * 40, [1e9] * 40)
    small = bars([100.0] * 40, [1e4] * 40)
    assert X.signal_size(small, 39, 30) > X.signal_size(big, 39, 30)


def test_signal_returns_none_without_enough_history():
    b = bars([100.0] * 3)
    assert X.signal_momentum(b, 2, 90) is None
    assert X.signal_illiquidity(b, 2, 30) is None


def _uni(n=20, hold=5):
    """n coins, half drifting up and half down, so a sort has something to do."""
    uni = {}
    for k in range(n):
        drift = 1.0 + (0.01 if k % 2 else -0.01)
        px = [100.0]
        for _ in range(hold * 4):
            px.append(px[-1] * drift)
        uni["C%02dUSDT" % k] = bars(px, [1e6 * (k + 1)] * len(px))
    return uni


def test_delisted_coin_is_dropped_not_counted():
    """A coin with no exit bar must not enter the period at all."""
    uni = _uni()
    victim = "C01USDT"
    uni[victim] = uni[victim][:6]  # stops printing before the first exit
    ps = X.build_periods(uni, "reversal_7d", hold_days=10)
    assert ps
    for p in ps:
        assert all(pos.symbol != victim for pos in p.positions)


def test_signal_uses_only_bars_at_or_before_entry():
    """Doctoring the future must not change the signal, only the return."""
    uni = _uni()
    a = X.build_periods(uni, "reversal_7d", hold_days=10)
    tampered = {s: list(b) for s, b in uni.items()}
    for s, b in tampered.items():
        last = b[-1]
        b[-1] = X.Bar(last.ts, 1e6, 1e6, 1e6, 1e6, last.quote_volume, last.trades)
    c = X.build_periods(tampered, "reversal_7d", hold_days=10)
    assert [pos.symbol for pos in a[0].positions] == [pos.symbol for pos in c[0].positions]


def test_min_adv_filter_removes_thin_names():
    uni = _uni()
    loose = X.build_periods(uni, "small_size_30d", hold_days=10, min_adv_usd=0)
    tight = X.build_periods(uni, "small_size_30d", hold_days=10, min_adv_usd=5e6)
    assert len(tight[0].positions) < len(loose[0].positions)


def test_spread_is_charged_per_name_and_both_sides():
    uni = _uni()
    wide = {s: 100.0 for s in uni}  # 100bp round trip
    free = X.build_periods(uni, "reversal_7d", hold_days=10)
    paid = X.build_periods(uni, "reversal_7d", hold_days=10, spread_bp_of=wide)
    g = free[0].gross_ret()
    assert abs(paid[0].gross_ret() - g) < 1e-12  # gross is untouched
    # net drops by the spread plus two fees, per name.
    drop = g - paid[0].net_ret(fee_bp=10.0)
    assert abs(drop - (100.0 + 20.0) / 10_000.0) < 1e-9


def test_capacity_cap_dilutes_when_aum_exceeds_the_tape():
    uni = _uni()
    ps = X.build_periods(uni, "small_size_30d", hold_days=10)
    p = ps[0]
    small = p.net_ret(aum_usd=1e4, adv_frac=0.01)
    huge = p.net_ret(aum_usd=1e10, adv_frac=0.01)
    assert p.deployed_frac(1e4, 0.01) > p.deployed_frac(1e10, 0.01)
    assert abs(huge) < abs(small)  # undeployed capital earns nothing


def test_deployed_fraction_is_one_when_aum_is_tiny():
    uni = _uni()
    p = X.build_periods(uni, "reversal_7d", hold_days=10)[0]
    assert abs(p.deployed_frac(1.0, 0.01) - 1.0) < 1e-9


def test_periods_do_not_overlap():
    uni = _uni(hold=10)
    ps = X.build_periods(uni, "reversal_7d", hold_days=5)
    assert len(ps) >= 2
    for a, b in zip(ps, ps[1:]):
        assert a.t1 <= b.t0


def test_summarize_bootstraps_periods_not_coin_periods():
    """n must be the number of rebalances, not the number of positions held."""
    uni = _uni(n=40, hold=10)
    ps = X.build_periods(uni, "reversal_7d", hold_days=5)
    s = X.summarize_periods(ps, hold_days=5)
    assert s["n"] == len(ps)
    assert s["n_names_median"] > s["n"]  # far more coin-periods than periods
    assert s["n_names_median"] == len(ps[0].positions)


def test_cost_drag_is_gross_minus_net():
    uni = _uni(n=30, hold=10)
    sp = {s: 50.0 for s in uni}
    ps = X.build_periods(uni, "reversal_7d", hold_days=5, spread_bp_of=sp)
    s = X.summarize_periods(ps, hold_days=5, fee_bp=10.0)
    assert abs(s["cost_drag_annual"] - (s["gross_annual"] - s["net_annual"])) < 1e-12
    # 50bp of spread + 20bp of fees over a 5-day hold, annualized.
    assert abs(s["cost_drag_annual"] - 0.0070 * 365 / 5) < 1e-9


def test_summarize_on_empty_is_not_an_error():
    assert X.summarize_periods([], hold_days=5)["n"] == 0


def test_long_and_short_legs_are_equal_sized():
    uni = _uni(n=25, hold=10)
    p = X.build_periods(uni, "reversal_7d", hold_days=5, quantile=0.2)[0]
    assert len(p.longs) == len(p.shorts) == max(1, int(25 * 0.2))


def test_short_leg_gross_flips_sign():
    uni = _uni(n=20, hold=10)
    p = X.build_periods(uni, "reversal_7d", hold_days=5)[0]
    for pos in p.shorts:
        assert pos.side == -1
        expected = -(pos.exit / pos.entry - 1.0)
        assert abs(pos.gross - expected) < 1e-12


def test_load_daily_rejects_hourly_bars():
    import os, tempfile
    from btchour.research import carry

    with tempfile.TemporaryDirectory() as root:
        d = carry.bar_root("klines", "X", "1d", root=root)
        os.makedirs(d)
        with open(os.path.join(d, "2026-01.csv"), "w") as f:
            for i in range(200):
                t = i * carry.HOUR_MS
                f.write(f"{t},1,1,1,100,1,{t},1000,10,1,1,0\n")
        try:
            X.load_daily("X", kind="klines", root=root)
        except ValueError as e:
            assert "daily" in str(e)
            return
        raise AssertionError("hourly bars must not load as daily")


def test_short_receives_positive_funding_and_pays_negative():
    """On a perp the short leg's funding is the mirror of the long leg's.

    The cross-section of alt funding is *negative*, so shorts pay longs, and a
    portfolio short the speculative tail is paying a large published carry a
    price-only backtest never charges.
    """
    long_ = X.Position("A", +1, 100.0, 100.0, 1e6, 0.0, funding_sum=0.01)
    short = X.Position("A", -1, 100.0, 100.0, 1e6, 0.0, funding_sum=0.01)
    assert long_.funding == -0.01  # longs pay when funding is positive
    assert short.funding == +0.01
    neg = X.Position("A", -1, 100.0, 100.0, 1e6, 0.0, funding_sum=-0.01)
    assert neg.funding == -0.01  # a short pays when funding is negative


def test_funding_enters_gross_and_net():
    uni = _uni(n=20, hold=10)
    # The first tradeable period starts on day 10, not day 0: the signal needs
    # its lookback first, so a stamp at day 3 falls before any period.
    fake = {
        s: [type("F", (), {"rate": 0.01, "ts": 12 * DAY_MS})()] for s in uni
    }
    plain = X.build_periods(uni, "reversal_7d", hold_days=5)[0]
    withf = X.build_periods(uni, "reversal_7d", hold_days=5, funding_of=fake)[0]
    # Equal long and short legs, so the funding nets out across the portfolio.
    assert abs(withf.gross_ret() - plain.gross_ret()) < 1e-12
    assert abs(withf.funding_ret()) < 1e-12
    # But each leg carries it, and the price-only view excludes it.
    assert any(p.funding != 0 for p in withf.positions)
    assert abs(withf.gross_ret(with_funding=False) - plain.gross_ret()) < 1e-12


def test_funding_stamps_outside_the_hold_are_not_charged():
    uni = _uni(n=20, hold=10)
    fake = {s: [type("F", (), {"rate": 0.01, "ts": 99 * DAY_MS})()] for s in uni}
    p = X.build_periods(uni, "reversal_7d", hold_days=5, funding_of=fake)[0]
    assert all(pos.funding == 0 for pos in p.positions)

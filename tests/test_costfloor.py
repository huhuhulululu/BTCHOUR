import math

import pytest

from btchour.research.costfloor import (
    CARRIES,
    HORIZONS,
    MAKER_BOTH,
    MAKER_IN_TAKER_OUT,
    RISKFREE_1Y,
    TAKER_BOTH,
    VENUES,
    Carry,
    Horizon,
    Venue,
    breakeven_fraction,
    quoting_ratio,
    round_trip_cost_bp,
)


def _venue(**kw):
    base = dict(name="v", spread_bp=1.0, taker_bp=5.0, maker_bp=2.0, source="test")
    base.update(kw)
    return Venue(**base)


def test_taker_round_trip_pays_the_spread_once_and_both_fees():
    v = _venue(spread_bp=1.0, taker_bp=5.0)
    assert round_trip_cost_bp(v, TAKER_BOTH) == pytest.approx(11.0)


def test_quoting_both_sides_earns_the_spread_and_pays_both_maker_fees():
    v = _venue(spread_bp=1.0, maker_bp=2.0)
    assert round_trip_cost_bp(v, MAKER_BOTH) == pytest.approx(3.0)


def test_maker_in_taker_out_nets_the_spread_to_zero():
    v = _venue(spread_bp=7.0, maker_bp=2.0, taker_bp=5.0)
    assert round_trip_cost_bp(v, MAKER_IN_TAKER_OUT) == pytest.approx(7.0)


def test_no_maker_fee_makes_the_quoting_ratio_infinite():
    """Kalshi charges no maker fee on `quadratic` series, and still had no
    money in it. An infinite ratio here clears rule 1 and says nothing at all
    about adverse selection, which is rule 4's job."""

    assert quoting_ratio(_venue(maker_bp=0.0)) == math.inf


def test_unknown_execution_style_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        round_trip_cost_bp(_venue(), "guess")


def test_negative_cost_input_is_rejected():
    with pytest.raises(ValueError):
        _venue(spread_bp=-1.0)


def test_sigma_scales_with_the_square_root_of_the_period():
    hourly = Horizon("h", 0.50, 365 * 24, "test")
    daily = Horizon("d", 0.50, 365, "test")
    assert daily.sigma_bp / hourly.sigma_bp == pytest.approx(math.sqrt(24.0))


def test_breakeven_fraction_falls_as_the_holding_period_lengthens():
    v = _venue()
    fractions = [
        breakeven_fraction(v, Horizon(str(n), 0.50, 365 * 24 / n, "test"), TAKER_BOTH)
        for n in (1, 4, 24)
    ]
    assert fractions == sorted(fractions, reverse=True)


def test_binance_perp_quoting_is_negative_before_adverse_selection():
    """The headline paper elimination: at the retail tier the maker fee alone
    is an order of magnitude wider than the whole spread, so quoting loses
    money against a counterparty that knows nothing."""

    perp = next(v for v in VENUES if v.name.startswith("Binance USDT-M"))
    assert quoting_ratio(perp) < 0.1
    assert round_trip_cost_bp(perp, MAKER_BOTH) > 0.0


def test_a_carry_trade_is_negative_when_costs_outrun_the_stream():
    c = Carry("c", funding_bp_per_day=3.0, entry_cost_bp=12.0, exit_cost_bp=12.0, source="test")
    assert c.net_bp(8.0) == pytest.approx(0.0)
    assert c.net_bp(4.0) < 0.0
    assert c.prize_over_cost(30.0) == pytest.approx(3.75)


def test_carry_is_judged_against_the_risk_free_rate_not_against_zero():
    c = next(c for c in CARRIES if "2026-09-09" in c.name)
    assert c.net_annualized(30.0) > 0.0
    assert c.excess_over_riskfree(30.0, RISKFREE_1Y) < 0.02


def test_every_table_entry_carries_its_source():
    for row in (*VENUES, *HORIZONS, *CARRIES):
        assert row.source

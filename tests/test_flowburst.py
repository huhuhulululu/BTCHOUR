"""Flow bursts: the imbalance arithmetic, and the two look-ahead guards.

A conditional-return study is the shape that produced every false positive in
this repository, so the tests here are mostly about the ways a burst study
cheats: entering at the extreme of the bar that defined the signal, and
stepping over a gap in the tape as if it were a minute.
"""

from __future__ import annotations

from btchour.research import flowburst as B

MIN = B.MINUTE_MS


def bar(i, open_, close, vol=1000.0, taker_buy=None):
    if taker_buy is None:
        taker_buy = vol / 2
    return B.Bar(ts=i * MIN, open=open_, close=close,
                 quote_volume=vol, taker_buy_quote=taker_buy)


def test_imbalance_spans_minus_one_to_one():
    assert bar(0, 100, 100, 1000, 1000).imbalance == 1.0
    assert bar(0, 100, 100, 1000, 0).imbalance == -1.0
    assert bar(0, 100, 100, 1000, 500).imbalance == 0.0


def test_an_empty_bar_is_treated_as_balanced_not_as_a_signal():
    b = bar(0, 100, 100, 0.0, 0.0)
    assert b.taker_buy_share == 0.5
    assert b.imbalance == 0.0


def test_return_is_open_to_close_in_bp():
    assert abs(bar(0, 100.0, 101.0).ret_bp - 100.0) < 1e-9


def _tape(n=200, quiet_vol=100.0):
    """A flat, quiet, balanced tape with no bursts in it."""
    return [bar(i, 100.0, 100.0, quiet_vol) for i in range(n)]


def _with_burst(i, direction, reversion, n=200, quiet_vol=100.0):
    """Insert one burst at index `i` that moves `direction` bp, then reverts."""
    bars = _tape(n, quiet_vol)
    px = 100.0 * (1 + direction / 10_000.0)
    bars[i] = bar(i, 100.0, px, quiet_vol * 10,
                  taker_buy=quiet_vol * 10 if direction > 0 else 0.0)
    # the level stays moved, then reverts by `reversion` bp over the next bars
    back = px * (1 - reversion / 10_000.0) if direction > 0 else px * (1 + reversion / 10_000.0)
    for j in range(i + 1, n):
        bars[j] = bar(j, px, back, quiet_vol)
    return bars


def test_a_quiet_tape_has_no_bursts():
    assert B.find_bursts("X", _tape()) == []


def test_a_burst_is_found_when_all_three_conditions_hold():
    bars = _with_burst(100, +50.0, 30.0)
    out = B.find_bursts("X", bars, imbalance_min=0.6, move_min_bp=20.0, horizon=10)
    assert len(out) == 1
    assert out[0].ts == 100 * MIN
    assert out[0].imbalance > 0.9


def test_each_condition_alone_is_not_enough():
    # one-sided and busy, but the move is tiny
    bars = _with_burst(100, +1.0, 0.0)
    assert B.find_bursts("X", bars, move_min_bp=20.0) == []
    # large move and busy, but balanced aggression
    bars = _tape()
    bars[100] = bar(100, 100.0, 100.5, 1000.0, 500.0)
    for j in range(101, 200):
        bars[j] = bar(j, 100.5, 100.5, 100.0)
    assert B.find_bursts("X", bars, imbalance_min=0.6) == []
    # one-sided and large, but no more volume than usual
    bars = _tape(quiet_vol=1000.0)
    bars[100] = bar(100, 100.0, 100.5, 1000.0, 1000.0)
    for j in range(101, 200):
        bars[j] = bar(j, 100.5, 100.5, 1000.0)
    assert B.find_bursts("X", bars, volume_mult=3.0) == []


def test_aggression_and_move_must_point_the_same_way():
    """A big move against the aggressors is a repricing, not flow pushing."""
    bars = _tape()
    bars[100] = bar(100, 100.0, 99.5, 1000.0, 1000.0)  # buyers, price down
    for j in range(101, 200):
        bars[j] = bar(j, 99.5, 99.5, 100.0)
    assert B.find_bursts("X", bars) == []


def test_entry_is_the_next_bar_open_not_the_burst_close():
    """Filling at the extreme of the signal bar is the wick-fill model that
    paid decision 018 imaginary money."""
    bars = _with_burst(100, +50.0, 30.0)
    out = B.find_bursts("X", bars, horizon=10)
    assert out[0].entry == bars[101].open
    assert out[0].exit == bars[111].close


def test_a_gap_in_the_tape_is_never_stepped_over():
    bars = _with_burst(100, +50.0, 30.0)
    del bars[101]  # the minute right after the burst never printed
    assert B.find_bursts("X", bars, horizon=10) == []


def test_absorbing_side_is_opposite_the_aggressors():
    up = B.Burst("X", 0, +0.9, +50.0, 1e6, entry=100.0, exit=99.0)
    assert up.side == -1  # buyers pushed it up, so absorb by selling
    assert up.gross_bp > 0  # and the reversion pays
    down = B.Burst("X", 0, -0.9, -50.0, 1e6, entry=100.0, exit=101.0)
    assert down.side == +1
    assert down.gross_bp > 0


def test_continuation_rather_than_reversion_loses():
    up = B.Burst("X", 0, +0.9, +50.0, 1e6, entry=100.0, exit=101.0)
    assert up.gross_bp < 0
    assert up.net_bp < up.gross_bp


def test_net_charges_the_spread_and_two_fees():
    b = B.Burst("X", 0, +0.9, +50.0, 1e6, entry=100.0, exit=100.0)
    assert abs(b.gross_bp) < 1e-9
    assert abs(b.net_bp - -(B.ROUND_TRIP_SPREAD_BP + 2 * B.TAKER_FEE_BP)) < 1e-9


def test_the_unit_is_the_symbol_day():
    day = 86_400_000
    bursts = [B.Burst("X", d * day + k * 3_600_000, 0.9, 50.0, 1e6, 100.0, 99.0)
              for d in range(3) for k in range(20)]
    assert len(B.by_symbol_day(bursts)) == 3
    s = B.summarize(bursts)
    assert s["n"] == 60 and s["n_symbol_days"] == 3


def test_summarize_on_empty_is_not_an_error():
    assert B.summarize([])["n"] == 0


def test_load_bars_skips_a_header_row(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
        "1780272000000,100.0,101,99,100.5,48,1780272059999,3561022.1,2445,21,1603482.4,0\n"
    )
    bars = B.load_bars(str(p))
    assert len(bars) == 1
    assert bars[0].ts == 1780272000000
    assert abs(bars[0].taker_buy_share - 1603482.4 / 3561022.1) < 1e-9

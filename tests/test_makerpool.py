"""The maker markout's sign convention, and the reference-price bias it fixes.

Getting the sign backwards would turn adverse selection into an edge and be
invisible in every downstream number, so it is pinned with hand-built tapes
where the right answer is known by construction.
"""

from __future__ import annotations

from btchour.research import makerpool as M

SEC = 1000


def tape(rows):
    """rows: (t_seconds, price, qty, buyer_was_maker)."""
    return [(p, q, int(t * SEC), m) for t, p, q, m in rows]


def flat_then_move(direction, buyer_maker, n=400):
    """A long flat stretch at 100, then a step to 100*(1+direction)."""
    rows = [(i, 100.0, 1.0, buyer_maker) for i in range(n)]
    moved = 100.0 * (1.0 + direction)
    rows += [(n + i, moved, 1.0, buyer_maker) for i in range(n)]
    return tape(rows)


def test_maker_who_bought_gains_when_price_rises():
    s = M.summarize_day("X", "d", flat_then_move(0.01, True))
    assert s.markout_bp[1_000] > 0
    assert s.markout_bp_vwap[1_000] > 0


def test_maker_who_sold_loses_when_price_rises():
    s = M.summarize_day("X", "d", flat_then_move(0.01, False))
    assert s.markout_bp[1_000] < 0
    assert s.markout_bp_vwap[1_000] < 0


def test_sign_is_symmetric_in_the_price_direction():
    up = M.summarize_day("X", "d", flat_then_move(0.01, True))
    down = M.summarize_day("X", "d", flat_then_move(-0.01, True))
    assert up.markout_bp[1_000] > 0 > down.markout_bp[1_000]


def test_flat_tape_has_zero_markout():
    rows = tape([(i, 100.0, 1.0, i % 2 == 0) for i in range(500)])
    s = M.summarize_day("X", "d", rows)
    assert abs(s.markout_bp[1_000]) < 1e-9
    assert abs(s.markout_bp_vwap[1_000]) < 1e-9


def _bounce_tape(seed=5, n=4000):
    """A tape with no information in it: price bounces between a bid and an
    ask, aggressor side persists in runs, and arrivals are irregular.

    The irregularity matters. With perfectly regular one-second arrivals and
    strictly alternating prices, both references are parity-locked to the fill
    -- the trade exactly `h` later has the same parity, so the single-trade
    reference reads zero bias by accident and a narrow VWAP window reads a
    large one. Neither number means anything on that tape, which is why this
    one jitters the clock.
    """
    import random

    rng = random.Random(seed)
    rows = []
    t = 0.0
    side = True
    run = 0
    for _ in range(n):
        t += rng.expovariate(1.0)
        if run == 0:
            side = rng.random() < 0.5
            run = rng.randint(1, 9)
        run -= 1
        px = 100.1 if side else 100.0  # aggressive buys lift the ask
        rows.append((t, px, 1.0, not side))
    return tape(rows)


def test_markout_recovers_the_half_spread_when_nobody_is_informed():
    """The calibration test for the whole module.

    On a tape where the maker always rests at the bid or the ask and no
    aggressor knows anything, the maker captures the half-spread and nothing
    is taken back. So the markout must come out at the half-spread -- here a
    10bp spread, so 5bp -- and not at zero. That is worth pinning because it
    fixes what the real numbers mean: a measured markout *below* the
    half-spread is adverse selection, and a measured markout of -0.6bp on BTC
    means adverse selection exceeded the spread captured, not that the maker
    earned -0.6bp of spread.
    """
    s = M.summarize_day("X", "d", _bounce_tape(), horizons=(60_000,))
    half_spread_bp = 0.1 / 100.05 * 10_000 / 2
    for got in (s.markout_bp[60_000], s.markout_bp_vwap[60_000]):
        assert abs(got - half_spread_bp) < 1.0


def test_the_two_references_agree_on_an_uninformative_tape():
    """They disagreeing would mean bid-ask bounce is driving the result, which
    is exactly the condition the module reports both of them to detect."""
    s = M.summarize_day("X", "d", _bounce_tape(), horizons=(60_000,))
    assert abs(s.markout_bp[60_000] - s.markout_bp_vwap[60_000]) < 0.75


def test_adverse_selection_shows_up_as_markout_below_the_half_spread():
    """Add informed flow to the same tape and the markout has to fall.

    Every aggressive buy is now followed by an uptick larger than the spread,
    so the maker who sold at the ask is run over. The quoted half-spread is
    unchanged at 5bp, so the drop is adverse selection and nothing else.
    """
    import random

    rng = random.Random(11)
    rows = []
    t = 0.0
    level = 100.0
    for i in range(4000):
        t += rng.expovariate(1.0)
        buy = rng.random() < 0.5
        if buy:
            rows.append((t, level + 0.05, 1.0, False))  # maker sold the ask
            level += 0.08  # and the price keeps going, well past the spread
        else:
            rows.append((t, level - 0.05, 1.0, True))  # maker bought the bid
            level -= 0.08
    s = M.summarize_day("X", "d", tape(rows), horizons=(60_000,))
    clean = M.summarize_day("X", "d", _bounce_tape(), horizons=(60_000,))
    assert s.markout_bp_vwap[60_000] < clean.markout_bp_vwap[60_000]
    assert s.markout_bp_vwap[60_000] < 0  # the drift more than eats the spread


def test_notional_weighting_follows_the_big_fills():
    """One large informed fill must dominate many small uninformed ones."""
    rows = [(i, 100.0, 0.01, True) for i in range(400)]
    rows.append((400, 100.0, 1000.0, False))  # big maker sale
    rows += [(401 + i, 110.0, 0.01, True) for i in range(400)]  # price runs up
    s = M.summarize_day("X", "d", tape(rows), horizons=(60_000,))
    assert s.markout_bp[60_000] < 0  # the big fill was on the losing side
    assert s.markout_bp_unweighted[60_000] > s.markout_bp[60_000]


def test_short_tape_is_reported_as_empty_not_guessed():
    s = M.summarize_day("X", "d", tape([(i, 100.0, 1.0, True) for i in range(20)]))
    assert s.n_trades == 20
    assert s.markout_bp == {}
    assert s.dollar_volume == 0.0


def test_fee_tiers_move_the_net_by_exactly_the_fee():
    s = M.summarize_day("X", "d", flat_then_move(0.01, True), horizons=(60_000,))
    gross = s.gross_bp(60_000)
    for tier, fee in M.FEE_TIERS_BP.items():
        assert abs(s.net_bp(tier=tier, horizon_ms=60_000) - (gross - fee)) < 1e-12
    # the top tier is a rebate, so it *adds* to the gross
    assert s.net_bp(tier="vip9", horizon_ms=60_000) > gross


def test_round_trip_through_json_preserves_every_horizon():
    s = M.summarize_day("X", "d", flat_then_move(0.01, True))
    back = M.DaySummary.from_json(s.to_json())
    assert back.markout_bp == s.markout_bp
    assert back.markout_bp_vwap == s.markout_bp_vwap
    assert back.dollar_volume == s.dollar_volume


def test_pool_summary_weights_days_by_notional():
    """A day that traded a thousand times more must count a thousand times
    more, or the screen recommends the corner that cannot absorb anything."""
    big = M.DaySummary("BIG", "d1", 1000, 1e9, 100.0)
    big.markout_bp_vwap = {60_000: -1.0}
    small = M.DaySummary("SMALL", "d1", 1000, 1e3, 100.0)
    small.markout_bp_vwap = {60_000: +9.0}
    s = M.summarize_pool([big, small], tier="retail")
    assert s["n"] == 2
    assert abs(s["gross_bp_mean"] - 4.0) < 1e-9  # equal weighting would say +4
    assert s["gross_bp_volweighted"] < -0.9  # notional weighting says -1
    assert abs(s["net_bp_volweighted"] - (s["gross_bp_volweighted"] - 2.0)) < 1e-9


def test_pool_summary_on_empty_is_not_an_error():
    assert M.summarize_pool([])["n"] == 0
    assert M.summarize_pool([M.DaySummary("X", "d", 10, 0.0, 0.0)])["n"] == 0

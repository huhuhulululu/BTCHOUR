"""The triangle identity, the aggressor requirement, and the staleness guard.

The staleness guard gets a test of its own because the version without it ran
first and reported violations worth 50 basis points that were entirely an
artifact of microsecond timestamps being read as milliseconds.
"""

from __future__ import annotations

from btchour.research import triangle as T


def q(price, ts, taker_bought=True, notional=1000.0):
    return T.Quote(price=price, ts=ts, taker_bought=taker_bought, notional=notional)


def test_norm_ts_converts_microseconds_and_leaves_milliseconds_alone():
    assert T.norm_ts(1781049600078441) == 1781049600078
    assert T.norm_ts(1781049600078) == 1781049600078
    assert T.norm_ts(0) == 0


def test_a_consistent_triangle_has_zero_edge():
    # ETH/USDT = ETH/BTC * BTC/USDT exactly
    assert abs(T.loop_edge_bp(100_000.0, 2_500.0, 0.025)) < 1e-9


def test_edge_sign_says_which_side_is_rich():
    rich = T.loop_edge_bp(100_000.0, 2_510.0, 0.025)  # ETH/USDT above synthetic
    cheap = T.loop_edge_bp(100_000.0, 2_490.0, 0.025)
    assert rich > 0 > cheap
    assert abs(rich - 40.0) < 0.1  # 10 on 2500 is 40bp


def test_a_degenerate_leg_does_not_divide_by_zero():
    assert T.loop_edge_bp(0.0, 2_500.0, 0.025) == 0.0
    assert T.loop_edge_bp(100_000.0, 2_500.0, 0.0) == 0.0


def _tape(edge_bp, n=400, step=10, taker_pattern=None):
    """A tape where every instant carries the same loop edge."""
    aq, bq, ba = [], [], []
    for i in range(n):
        t = i * step
        tb = True if taker_pattern is None else taker_pattern(i)
        aq.append(q(100_000.0, t, taker_bought=tb))
        bq.append(q(2_500.0 * (1 + edge_bp / 10_000.0), t, taker_bought=not tb))
        ba.append(q(0.025, t, taker_bought=tb))
    return aq, bq, ba


def test_a_persistent_violation_is_found_and_priced():
    aq, bq, ba = _tape(60.0)
    v, st = T.scan(aq, bq, ba, window_ms=1000)
    assert st["observations"] == len(ba)
    assert st["stale_frac"] == 0.0
    assert v
    assert all(x.direction == "forward" for x in v)
    assert abs(v[0].edge_bp - 60.0) < 0.2
    s = T.summarize(v, st)
    # 60bp clears vip9's 5.1bp floor but not retail's 30bp... it clears both.
    assert s["survivors_vip9"] == len(v)
    assert s["cost_floor_bp_retail"] == 30.0


def test_a_violation_below_the_fee_floor_has_no_survivors():
    aq, bq, ba = _tape(4.0)
    v, st = T.scan(aq, bq, ba, window_ms=1000)
    s = T.summarize(v, st)
    assert s["crossable_candidates"] > 0  # the edge exists
    assert s["survivors_retail"] == 0  # and it is not reachable
    assert s["survivors_vip9"] == 0  # not even at 5.1bp


def test_the_aggressor_requirement_rejects_an_uncrossable_violation():
    """A 60bp gap you cannot cross is not an opportunity.

    Here the B/Q leg printed on the ask (a taker bought it) while the trade
    needs to *sell* it, so nothing is crossable even though the arithmetic gap
    is large.
    """
    aq, bq, ba = [], [], []
    for i in range(300):
        t = i * 10
        aq.append(q(100_000.0, t, taker_bought=True))
        bq.append(q(2_515.0, t, taker_bought=True))  # wrong side for the trade
        ba.append(q(0.025, t, taker_bought=True))
    strict, st = T.scan(aq, bq, ba, window_ms=1000)
    loose, _ = T.scan(aq, bq, ba, window_ms=1000, require_crossable=False)
    assert strict == []
    assert len(loose) == 300
    assert st["observations"] == 300


def test_a_stale_leg_is_dropped_not_used():
    aq = [q(100_000.0, 0)]  # one print at the very start and never again
    bq = [q(2_500.0, t) for t in range(0, 5000, 10)]
    ba = [q(0.025, t) for t in range(0, 5000, 10)]
    v, st = T.scan(aq, bq, ba, window_ms=1000, max_stale_frac=1.0)
    # only the prints within 1000ms of the single A leg print survive
    assert st["observations"] == 101
    assert st["dropped_stale"] == len(ba) - 101


def test_a_mostly_stale_scan_raises_instead_of_reporting():
    """The bug this guard exists for: stamps a thousand times too large make a
    one-second window a one-millisecond window, and the survivors are bursts."""
    aq = [q(100_000.0, 0)]
    bq = [q(2_500.0, t) for t in range(0, 100_000, 10)]
    ba = [q(0.025, t) for t in range(0, 100_000, 10)]
    try:
        T.scan(aq, bq, ba, window_ms=1000)
    except ValueError as e:
        assert "stale" in str(e) and "norm_ts" in str(e)
        return
    raise AssertionError("a mostly-stale scan must refuse to report")


def test_allow_stale_is_available_but_reports_the_fraction():
    aq = [q(100_000.0, 0)]
    bq = [q(2_500.0, t) for t in range(0, 100_000, 10)]
    ba = [q(0.025, t) for t in range(0, 100_000, 10)]
    v, st = T.scan(aq, bq, ba, window_ms=1000, allow_stale=True)
    assert st["stale_frac"] > 0.9


def test_capacity_is_the_thinnest_leg():
    """Decision 018's lesson in one field: a real crossing on a dust print is
    worth dust."""
    aq, bq, ba = _tape(60.0, n=10)
    ba = [T.Quote(x.price, x.ts, x.taker_bought, 0.5) for x in ba]
    v, st = T.scan(aq, bq, ba, window_ms=1000)
    assert all(x.min_notional == 0.5 for x in v)
    s = T.summarize(v, st)
    assert s["capacity_usd_vip9"] == 0.5


def test_net_edge_subtracts_three_fees_not_one():
    v = T.Violation(ts=0, edge_bp=40.0, direction="forward", min_notional=1.0,
                    staleness_ms=0)
    assert abs(v.net_bp("retail") - (40.0 - 30.0)) < 1e-12
    assert abs(v.net_bp("vip9") - (40.0 - 5.1)) < 1e-9


def test_empty_scan_is_not_an_error():
    v, st = T.scan([], [], [], window_ms=1000)
    assert v == [] and st["observations"] == 0
    assert T.summarize([], st)["crossable_candidates"] == 0

"""Funding-stamp flow: the signed prediction, the funding charge, the unit.

The whole reason this mechanism is worth testing is that its sign is fixed by
a published number rather than fitted, so the tests that matter are the ones
pinning that the orientation really is driven by the funding rate and that a
zero rate carries no prediction at all.
"""

from __future__ import annotations

from btchour.research import fundingflow as F

MIN = F.MINUTE_MS


class Fund:
    def __init__(self, ts, rate):
        self.ts = ts
        self.rate = rate


def minutes(prices, start=0):
    """prices indexed by minute from `start`."""
    return {start + i * MIN: p for i, p in enumerate(prices)}


def stamp(rate, before, at, after, lead=30 * MIN, lag=30 * MIN, settled=None):
    return F.Stamp(
        symbol="X", ts=10 * 3_600_000, rate=rate,
        settled_rate=rate if settled is None else settled,
        price_before=before, price_at=at, price_after=after,
        lead_ms=lead, lag_ms=lag,
    )


def test_into_and_out_returns_are_plain_returns():
    s = stamp(0.0001, 100.0, 99.0, 100.5)
    assert abs(s.into_bp - (-100.0)) < 1e-9
    assert abs(s.out_bp - (1.5 / 99.0 * 10_000)) < 1e-6


def test_positive_funding_predicts_a_dip_into_the_stamp():
    """Longs pay, so the crowd avoiding the payment is long and sells into the
    stamp. A dip is therefore a *confirming* observation."""
    s = stamp(+0.0010, 100.0, 99.0, 100.0)
    assert s.into_bp < 0
    assert s.signed_into_bp > 0  # confirms
    assert s.signed_out_bp > 0  # and the recovery confirms too


def test_negative_funding_flips_the_whole_prediction():
    s = stamp(-0.0010, 100.0, 99.0, 100.0)
    assert s.into_bp < 0
    assert s.signed_into_bp < 0  # the same dip now contradicts


def test_a_zero_rate_carries_no_prediction():
    s = stamp(0.0, 100.0, 99.0, 100.0)
    assert s.signed_into_bp == 0.0
    assert s.signed_out_bp == 0.0
    assert s.reversal_bp() == 0.0


def test_the_trade_receives_funding_because_it_takes_the_other_side():
    """The crowd that is exiting is the crowd that pays, so the position on
    the other side of it collects the funding rather than paying it."""
    flat = stamp(+0.0010, 100.0, 100.0, 100.0)  # no price move at all
    costs = F.ROUND_TRIP_SPREAD_BP + 2 * F.TAKER_FEE_BP
    assert abs(flat.reversal_bp() - (10.0 - costs)) < 1e-9
    assert abs(flat.reversal_bp(pay_funding=False) - (-costs)) < 1e-9
    neg = stamp(-0.0010, 100.0, 100.0, 100.0)
    assert abs(neg.reversal_bp() - (10.0 - costs)) < 1e-9  # symmetric


def test_the_trade_is_long_when_funding_is_positive():
    up = stamp(+0.0010, 100.0, 100.0, 101.0)
    down = stamp(+0.0010, 100.0, 100.0, 99.0)
    assert up.reversal_bp() > down.reversal_bp()
    flipped = stamp(-0.0010, 100.0, 100.0, 101.0)
    assert flipped.reversal_bp() < up.reversal_bp()  # short loses on the rise


def test_costs_are_always_charged():
    """A gross reversal smaller than the round trip must come out negative."""
    s = stamp(+0.00001, 100.0, 100.0, 100.02)  # 2bp move, 0.1bp funding
    assert s.reversal_bp() < 0
    assert abs(s.reversal_bp() - (2.0 + 0.1 - 13.99)) < 0.02


def test_a_stamp_missing_a_price_is_dropped_whole():
    closes = minutes([100.0] * 200)
    ts = 100 * MIN
    del closes[ts]  # the price at the stamp itself
    out = F.build_stamps("X", closes, [Fund(ts, 0.001)], lead_ms=30 * MIN,
                         lag_ms=30 * MIN, signal="realized")
    assert out == []


def test_stamps_snap_to_the_minute_grid():
    closes = minutes([100.0 + i for i in range(200)])
    ts = 100 * MIN + 37_000  # a settlement 37 seconds past the minute
    out = F.build_stamps("X", closes, [Fund(ts, 0.001)], lead_ms=30 * MIN,
                         lag_ms=30 * MIN, signal="realized")
    assert len(out) == 1
    s = out[0]
    assert s.price_at == closes[100 * MIN]
    assert s.price_before == closes[70 * MIN]
    assert s.price_after == closes[130 * MIN]


def test_the_unit_is_the_symbol_day_not_the_stamp():
    """Six settlements a day on one price path are not six independent draws."""
    day = 86_400_000
    st = []
    for d in range(4):
        for k in range(6):
            st.append(
                F.Stamp("X", d * day + k * 4 * 3_600_000, 0.001, 0.001,
                        100.0, 99.0, 100.0, 30 * MIN, 30 * MIN)
            )
    groups = F.by_symbol_day(st)
    assert len(groups) == 4
    assert all(len(g) == 6 for g in groups)
    s = F.summarize(st)
    assert s["n"] == 24
    assert s["n_symbol_days"] == 4


def test_two_symbols_on_the_same_day_are_separate_groups():
    st = [F.Stamp("A", 0, 0.001, 0.001, 100.0, 99.0, 100.0, MIN, MIN),
          F.Stamp("B", 0, 0.001, 0.001, 100.0, 99.0, 100.0, MIN, MIN)]
    assert len(F.by_symbol_day(st)) == 2


def test_the_rate_threshold_filters_and_is_reported():
    st = [stamp(0.00001, 100.0, 99.0, 100.0), stamp(0.0010, 100.0, 99.0, 100.0)]
    loose = F.summarize(st, min_rate_bp=0.0)
    tight = F.summarize(st, min_rate_bp=5.0)
    assert loose["n"] == 2
    assert tight["n"] == 1
    assert tight["min_rate_bp"] == 5.0


def test_summarize_on_empty_is_not_an_error():
    assert F.summarize([])["n"] == 0


def test_the_lagged_signal_uses_the_previous_settlement_not_this_one():
    """The whole result hinges on this.

    Binance's realized rate at `ts` is a time-weighted average over the
    interval *ending* at `ts`, so it is not final until the settlement and it
    embeds the price action of the last thirty minutes -- the first half of the
    trading window. The default therefore trades on the previous settlement's
    rate, which was published hours earlier.
    """
    closes = minutes([100.0] * 400)
    stamps = [Fund(100 * MIN, 0.001), Fund(200 * MIN, -0.005)]
    lagged = F.build_stamps("X", closes, stamps, lead_ms=30 * MIN, lag_ms=30 * MIN)
    # the first settlement has nothing before it, so it is dropped
    assert len(lagged) == 1
    assert lagged[0].ts == 200 * MIN
    assert lagged[0].rate == 0.001  # the EARLIER rate chose the side
    assert lagged[0].settled_rate == -0.005  # but this is what settles

    realized = F.build_stamps("X", closes, stamps, lead_ms=30 * MIN,
                              lag_ms=30 * MIN, signal="realized")
    assert len(realized) == 2
    assert realized[1].rate == realized[1].settled_rate == -0.005


def test_a_flipped_rate_means_the_position_pays_rather_than_receives():
    """A stale signal does not guarantee collecting the funding."""
    s = stamp(+0.0010, 100.0, 100.0, 100.0, settled=-0.0010)
    costs = F.ROUND_TRIP_SPREAD_BP + 2 * F.TAKER_FEE_BP
    # long because the signal said +, but the settled rate went negative, so
    # the long pays 10bp instead of receiving it
    assert abs(s.reversal_bp() - (-10.0 - costs)) < 1e-9


def test_build_stamps_rejects_an_unknown_signal():
    try:
        F.build_stamps("X", {}, [], signal="predicted")
    except ValueError as e:
        assert "signal" in str(e)
        return
    raise AssertionError("an unknown signal mode must raise")

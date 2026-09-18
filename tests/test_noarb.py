"""Relations the exchange guarantees -- and the two ways of inventing one."""

import unittest

from btchour.research.noarb import exhaustive_edges, ladder_crosses, rung_subject


def _m(ticker, bid, ask, *, event="EV", strike=None, sub=None, bid_sz=100.0, ask_sz=100.0):
    row = {
        "ticker": ticker,
        "event_ticker": event,
        "yes_bid_dollars": None if bid is None else f"{bid:.4f}",
        "yes_ask_dollars": None if ask is None else f"{ask:.4f}",
        "yes_bid_size_fp": f"{bid_sz:.2f}",
        "yes_ask_size_fp": f"{ask_sz:.2f}",
    }
    if strike is not None:
        row["floor_strike"] = strike
        row["strike_type"] = "greater"
        row["yes_sub_title"] = sub or f"${strike:,.2f} or above"
    return row


class ExclusiveBasketTests(unittest.TestCase):
    def test_bids_summing_above_one_is_a_locked_profit(self):
        markets = [_m("A-1", 0.60, 0.62), _m("A-2", 0.55, 0.57)]
        edges = exhaustive_edges(markets)
        sell = [e for e in edges if e.kind == "sell_all"]
        self.assertEqual(len(sell), 1)
        self.assertAlmostEqual(sell[0].gross, 0.15)
        self.assertGreater(sell[0].net, 0.0)

    def test_buying_the_basket_is_not_reported_by_default(self):
        """`mutually_exclusive` promises at most one winner, not exactly one.

        A candidate list can omit whoever actually wins, and a suspended leg
        drops out of the snapshot while its siblings still quote. Either way
        the basket can pay nothing, so a cheap `sum(ask)` is not an
        arbitrage and must not be reported as one.
        """
        markets = [_m("A-1", 0.10, 0.12), _m("A-2", 0.10, 0.12)]
        kinds = {e.kind for e in exhaustive_edges(markets)}
        self.assertNotIn("buy_all", kinds)
        kinds = {e.kind for e in exhaustive_edges(markets, include_buy=True)}
        self.assertIn("buy_all", kinds)

    def test_a_leg_with_no_bid_kills_the_short_basket(self):
        markets = [_m("A-1", 0.60, 0.62), _m("A-2", None, 0.57)]
        self.assertEqual([e for e in exhaustive_edges(markets) if e.kind == "sell_all"], [])

    def test_fees_are_charged_per_leg(self):
        two = exhaustive_edges([_m("A-1", 0.60, 0.62), _m("A-2", 0.55, 0.57)])[0]
        four = exhaustive_edges(
            [_m("A-1", 0.30, 0.32), _m("A-2", 0.30, 0.32), _m("A-3", 0.30, 0.32), _m("A-4", 0.30, 0.32)]
        )[0]
        self.assertEqual(four.legs, 4)
        self.assertGreater(four.fees, two.fees)


class LadderTests(unittest.TestCase):
    def test_a_bid_above_a_lower_strikes_ask_is_a_cross(self):
        markets = [_m("K-T100", 0.40, 0.42, strike=100.0), _m("K-T200", 0.50, 0.52, strike=200.0)]
        crosses = ladder_crosses(markets)
        self.assertEqual(len(crosses), 1)
        self.assertAlmostEqual(crosses[0].gross, 0.08)

    def test_an_ordered_ladder_produces_nothing(self):
        markets = [_m("K-T100", 0.50, 0.52, strike=100.0), _m("K-T200", 0.40, 0.42, strike=200.0)]
        self.assertEqual(ladder_crosses(markets), [])

    def test_opposing_ladders_in_one_event_are_never_paired(self):
        """The bug this scan shipped with: 18,082 crossings out of 19,479.

        A spread event lists both teams. "Oregon by over 7.5" quoted at 0.99
        against "Portland St. by over 2.5" offered at 0.01 is not a crossed
        ladder; it is one team being a heavy favourite. They are different
        questions and must not be paired.
        """
        markets = [
            _m("G-PRST3", 0.00, 0.01, strike=2.5, sub="Portland St. wins 1H by over 2.5 points"),
            _m("G-ORE8", 0.99, 1.00, strike=7.5, sub="Oregon wins 1H by over 7.5 points"),
        ]
        self.assertEqual(ladder_crosses(markets), [])

    def test_rungs_of_one_question_share_a_subject_key(self):
        a = _m("K-T85799.99", 0.5, 0.5, strike=85799.99)
        b = _m("K-T85899.99", 0.5, 0.5, strike=85899.99)
        self.assertEqual(rung_subject(a), rung_subject(b))

    def test_named_outcomes_are_not_rungs(self):
        markets = [_m("G-CHC", 0.40, 0.42), _m("G-CIN", 0.60, 0.62)]
        self.assertEqual(ladder_crosses(markets), [])


if __name__ == "__main__":
    unittest.main()

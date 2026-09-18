"""The maker-pool arithmetic, including the sign conventions it turns on."""

import unittest

from btchour.research.venue import (
    Print,
    by_cost_bucket,
    settle_leg,
    summarize_pool,
    taker_fee_per_contract,
)


def _print(yes_price, taker_side, count=1.0, event="E1", ticker="T1"):
    return Print(ticker=ticker, event=event, series="KXTEST", ts="2026-09-18T00:00:00Z",
                 yes_price=yes_price, count=count, taker_side=taker_side)


class MakerPoolTests(unittest.TestCase):
    def test_aggressor_buying_yes_leaves_the_maker_holding_no(self):
        leg = settle_leg(_print(0.30, "yes"), "no")
        assert leg.maker_side == "no"
        # The maker sold YES at 0.30, so it paid 0.70 for the NO that just settled.
        self.assertAlmostEqual(leg.maker_cost, 0.70)
        self.assertAlmostEqual(leg.maker_pnl, 0.30)

    def test_aggressor_buying_no_leaves_the_maker_holding_yes(self):
        leg = settle_leg(_print(0.30, "no"), "no")
        assert leg.maker_side == "yes"
        assert leg.maker_cost == 0.30
        self.assertAlmostEqual(leg.maker_pnl, -0.30)

    def test_taker_pays_the_fee_so_the_two_sides_do_not_sum_to_zero(self):
        leg = settle_leg(_print(0.40, "yes"), "yes")
        fee = taker_fee_per_contract(0.40)
        self.assertAlmostEqual(leg.maker_pnl + leg.taker_pnl, -fee)
        assert fee > 0

    def test_a_quoted_offer_at_a_low_price_is_a_high_cost_holding(self):
        """018's retracted bucketing: resting 0.10 is a 0.90 purchase.

        Selling YES at 0.10 is economically buying NO at 0.90, so the leg belongs
        in the expensive bucket. Filing it by the quote puts the two tails in one
        basket, and they point opposite ways.
        """
        leg = settle_leg(_print(0.10, "yes"), "no")
        self.assertAlmostEqual(leg.maker_cost, 0.90)
        rows = by_cost_bucket([leg])
        assert rows[0]["bucket"] == "0.85-0.95"

    def test_unsettled_or_void_markets_are_dropped_not_scored_as_zero(self):
        assert settle_leg(_print(0.40, "yes"), "") is None
        assert settle_leg(_print(0.40, "yes"), "void") is None

    def test_pool_is_size_weighted_not_print_weighted(self):
        """One 1000-lot has to outweigh one 1-lot, or the mean is a fiction."""
        legs = [
            settle_leg(_print(0.20, "no", count=1.0, event="A"), "no"),      # -0.20 each
            settle_leg(_print(0.20, "no", count=999.0, event="B"), "yes"),   # +0.80 each
        ]
        pool = summarize_pool("KXTEST", legs)
        assert pool.contracts == 1000.0
        assert pool.maker_cents_per_contract > 79.0

    def test_interval_resamples_events_so_one_event_cannot_carry_the_result(self):
        """Every leg of one settlement rides the same outcome.

        Ten thousand contracts on a single event are one draw. An interval that
        counts them as ten thousand would call any single lucky settlement
        significant, which is the mistake 019 was written to stop.
        """
        legs = []
        for i in range(20):
            result = "yes" if i == 0 else "no"
            legs.append(settle_leg(_print(0.50, "no", count=500.0, event=f"E{i}"), result))
        pool = summarize_pool("KXTEST", legs)
        assert pool.events == 20
        assert pool.maker_ci95[0] < pool.maker_cents_per_contract < pool.maker_ci95[1]
        # 19 of 20 events lost; the interval has to admit the one that did not.
        assert pool.maker_ci95[1] > pool.maker_ci95[0] + 5.0


if __name__ == "__main__":
    unittest.main()

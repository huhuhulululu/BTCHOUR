from __future__ import annotations

import unittest

from btchour.broker import live_flatten, live_submit, yes_book_exit, yes_book_quote
from btchour.strategy import Opportunity


def _coupon(side: str = "no") -> Opportunity:
    return Opportunity(
        ticker="KXBTCD-26AUG2819-T77599.99",
        event_ticker="KXBTCD-26AUG2819",
        subtitle="$77,599.99 or above",
        side=side,
        book_side="bid" if side == "yes" else "ask",
        strike=77599.99,
        spot=77573,
        seconds_left=1500,
        model_p=0.40,
        ask=0.37,
        max_price=0.70,
        limit_price=0.25,
        taker=False,
        b=3.0,
        if_win_roi=3.0,
        expected_roi=0.6,
        ev=0.6,
        fee=0.0,
        count=1.0,
        reason="dump_gap test",
        play="impulse_wait",
        lock_price=0.32,
    )


class YesBookQuoteTests(unittest.TestCase):
    def test_yes_rest_is_a_yes_bid(self):
        self.assertEqual(yes_book_quote("yes", 0.25), ("bid", 0.25))

    def test_no_rest_is_sell_yes_at_one_minus_rest(self):
        self.assertEqual(yes_book_quote("no", 0.25), ("ask", 0.75))

    def test_flatten_mirrors_the_entry_leg(self):
        self.assertEqual(yes_book_exit("yes", 0.31), ("ask", 0.31))
        self.assertEqual(yes_book_exit("no", 0.31), ("bid", 0.69))


class LiveSubmitTests(unittest.TestCase):
    def test_live_submit_sends_yes_leg_price_for_a_no_coupon(self):
        seen = {}

        class Fake:
            def create_order(self, **kwargs):
                seen.update(kwargs)
                return {"order_id": "ord-1", "fill_count": "0.00", "remaining_count": "1.00"}

        trade = live_submit(Fake(), _coupon("no"))
        self.assertEqual(seen["side"], "ask")
        self.assertAlmostEqual(seen["price"], 0.75)
        self.assertEqual(seen["count"], 1.0)
        self.assertEqual(seen["time_in_force"], "good_till_canceled")
        self.assertTrue(seen["post_only"])
        self.assertEqual(trade["side"], "no")
        self.assertAlmostEqual(trade["price"], 0.25)
        self.assertEqual(trade["mode"], "live")

    def test_live_submit_sends_a_yes_bid_for_a_yes_coupon(self):
        seen = {}

        class Fake:
            def create_order(self, **kwargs):
                seen.update(kwargs)
                return {"order_id": "ord-2", "fill_count": "0.00", "remaining_count": "1.00"}

        live_submit(Fake(), _coupon("yes"))
        self.assertEqual(seen["side"], "bid")
        self.assertAlmostEqual(seen["price"], 0.25)
        self.assertTrue(seen["post_only"])

    def test_live_flatten_no_buys_yes_at_one_minus_mark(self):
        seen = {}

        class Fake:
            def create_order(self, **kwargs):
                seen.update(kwargs)
                return {}

        live_flatten(Fake(), {"ticker": "KXBTCD-26AUG2819-T77599.99", "side": "no", "count": 1}, 0.31)
        self.assertEqual(seen["side"], "bid")
        self.assertAlmostEqual(seen["price"], 0.69)
        self.assertEqual(seen["time_in_force"], "immediate_or_cancel")

from __future__ import annotations

import unittest
from unittest.mock import patch

from btchour.config import Settings
from btchour.hang import _pick_wait
from btchour.kalshi import KalshiClient
from btchour.strategy import Opportunity


def _wait(ticker: str, side: str, ask: float) -> dict:
    return Opportunity(
        ticker=ticker,
        event_ticker="KXBTCD-26AUG2819",
        subtitle="",
        side=side,
        book_side="bid" if side == "yes" else "ask",
        strike=float(ticker.rsplit("T", 1)[-1]),
        spot=77573,
        seconds_left=1500,
        model_p=0.4,
        ask=ask,
        max_price=0.7,
        limit_price=0.25,
        taker=False,
        b=3.0,
        if_win_roi=3.0,
        expected_roi=0.6,
        ev=0.6,
        fee=0.0,
        count=10.0,
        reason="test",
        play="impulse_wait",
    ).as_dict()


class HangPickTests(unittest.TestCase):
    def test_picks_the_in_band_coupon_and_forces_one_contract(self):
        settings = Settings(playbook="flex")
        scan = {
            "opportunities": [
                _wait("KXBTCD-26AUG2819-T77499.99", "yes", 0.71),
                _wait("KXBTCD-26AUG2819-T77599.99", "yes", 0.37),
            ]
        }
        picked = _pick_wait(scan, settings, None, None)
        self.assertEqual(picked.ticker, "KXBTCD-26AUG2819-T77599.99")
        self.assertAlmostEqual(picked.count, 1.0)
        self.assertFalse(picked.taker)
        self.assertAlmostEqual(picked.limit_price, 0.25)

    def test_refuses_an_atm_pad_when_that_is_the_only_book(self):
        settings = Settings(playbook="flex")
        scan = {
            "opportunities": [
                _wait("KXBTCD-26AUG2819-T77499.99", "yes", 0.51),
                _wait("KXBTCD-26AUG2819-T77599.99", "no", 0.68),
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "ATM pad"):
            _pick_wait(scan, settings, None, None)

    def test_create_order_payload_uses_fixed_point_and_post_only(self):
        seen = {}

        class Fake(KalshiClient):
            def __init__(self):
                super().__init__()

            def post(self, path, payload):
                seen["path"] = path
                seen["payload"] = payload
                return {"order_id": "x"}

        Fake().create_order(
            "KXBTCD-26AUG2819-T77599.99",
            "bid",
            0.25,
            1,
            time_in_force="good_till_canceled",
            client_order_id="cid",
            post_only=True,
        )
        self.assertEqual(seen["path"], "/portfolio/events/orders")
        self.assertEqual(seen["payload"]["count"], "1.00")
        self.assertEqual(seen["payload"]["price"], "0.2500")
        self.assertEqual(seen["payload"]["side"], "bid")
        self.assertTrue(seen["payload"]["post_only"])
        self.assertEqual(seen["payload"]["time_in_force"], "good_till_canceled")

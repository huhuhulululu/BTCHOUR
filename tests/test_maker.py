import unittest

from btchour.replay import EventTape
from btchour.research.maker import scan_tape, scan_tapes
from btchour.research.metrics import bootstrap_ci, mean_ci


def _stick(ask: str, bid: str, *, low: str = "", high: str = "", volume: str = "10") -> dict:
    price = {}
    if low:
        price["low_dollars"] = low
    if high:
        price["high_dollars"] = high
    return {
        "yes_ask": {"close_dollars": ask},
        "yes_bid": {"close_dollars": bid},
        **({"price": price} if price else {}),
        "volume_fp": volume,
    }


def _tape(sticks: dict, result: str = "yes") -> EventTape:
    maturity_ms = 10_000_000
    return EventTape(
        "KXBTCD-26SEP1414",
        {(10_000_000 // 1000 - 3600) * 1000 - 60_000: 78_000.0},
        {78_000.0: sticks},
        {78_000.0: result},
        maturity_ms,
        (77_900.0, 78_100.0),
    )


class MakerFillTests(unittest.TestCase):
    def setUp(self):
        self.base = 10_000_000 // 1000 - 3600  # one hour before maturity

    def _sticks(self, **kwargs) -> dict:
        return {self.base: kwargs["quote"], self.base + 60: kwargs["nxt"], self.base + 120: kwargs["later"]}

    def test_a_bid_fills_when_the_tape_trades_through_it(self):
        tape = _tape(
            self._sticks(
                quote=_stick("0.41", "0.40"),
                nxt=_stick("0.41", "0.40", low="0.40", high="0.41"),
                later=_stick("0.41", "0.40"),
            )
        )
        fills = scan_tape(tape)
        sides = {f.side for f in fills}
        self.assertIn("buy_yes", sides)
        buy = next(f for f in fills if f.side == "buy_yes")
        self.assertAlmostEqual(buy.price, 0.40)
        # Settled YES, so buying at 0.40 pays 0.60.
        self.assertAlmostEqual(buy.settle_pnl, 0.60, places=9)

    def test_no_print_means_no_fill(self):
        tape = _tape(
            self._sticks(
                quote=_stick("0.41", "0.40"),
                nxt=_stick("0.41", "0.40", low="0.41", high="0.41", volume="0"),
                later=_stick("0.41", "0.40"),
            )
        )
        self.assertEqual(scan_tape(tape), [])

    def test_a_print_above_the_bid_does_not_fill_the_bid(self):
        tape = _tape(
            self._sticks(
                quote=_stick("0.41", "0.40"),
                nxt=_stick("0.41", "0.40", low="0.41", high="0.41"),
                later=_stick("0.41", "0.40"),
            )
        )
        self.assertEqual([f.side for f in scan_tape(tape)], ["sell_yes"])

    def test_the_two_sides_carry_opposite_settlement_pnl(self):
        tape = _tape(
            self._sticks(
                quote=_stick("0.41", "0.40"),
                nxt=_stick("0.41", "0.40", low="0.40", high="0.41"),
                later=_stick("0.41", "0.40"),
            ),
            result="no",
        )
        fills = {f.side: f for f in scan_tape(tape)}
        self.assertAlmostEqual(fills["buy_yes"].settle_pnl, -0.40, places=9)
        self.assertAlmostEqual(fills["sell_yes"].settle_pnl, +0.41, places=9)

    def test_markout_marks_against_the_later_mid(self):
        tape = _tape(
            self._sticks(
                quote=_stick("0.41", "0.40"),
                nxt=_stick("0.41", "0.40", low="0.40", high="0.41"),
                later=_stick("0.51", "0.50"),
            )
        )
        buy = next(f for f in scan_tape(tape) if f.side == "buy_yes")
        self.assertAlmostEqual(buy.markout_pnl, 0.505 - 0.40, places=9)

    def test_summary_splits_the_sides(self):
        tape = _tape(
            self._sticks(
                quote=_stick("0.41", "0.40"),
                nxt=_stick("0.41", "0.40", low="0.40", high="0.41"),
                later=_stick("0.41", "0.40"),
            )
        )
        report = scan_tapes([tape])
        self.assertEqual(report["hours"], 1)
        self.assertEqual(report["sides"]["both"]["settle"]["fills"], 2)
        self.assertEqual(report["sides"]["buy_yes"]["settle"]["fills"], 1)


class MeanCiTests(unittest.TestCase):
    def test_small_samples_use_the_bootstrap(self):
        values = [0.1, -0.2, 0.3, 0.05]
        self.assertEqual(mean_ci(values), bootstrap_ci(values))

    def test_large_samples_get_a_normal_interval_around_the_mean(self):
        values = [0.01 * ((i % 7) - 3) for i in range(8000)]
        lo, hi = mean_ci(values)
        mean = sum(values) / len(values)
        self.assertLess(lo, mean)
        self.assertGreater(hi, mean)
        self.assertAlmostEqual((lo + hi) / 2, mean, places=9)


if __name__ == "__main__":
    unittest.main()


class FillRuleTests(unittest.TestCase):
    """`touch` is an upper bound; `through` is the queue-aware read of it."""

    def setUp(self):
        self.base = 10_000_000 // 1000 - 3600

    def _sticks(self, quote, nxt, later) -> dict:
        return {self.base: quote, self.base + 60: nxt, self.base + 120: later}

    def test_touching_the_bid_fills_under_touch_but_not_under_through(self):
        # The tape reached 0.40 and stopped there: we may well have been
        # behind the whole queue that traded.
        tape = _tape(
            self._sticks(
                _stick("0.41", "0.40"),
                _stick("0.41", "0.40", low="0.40", high="0.41"),
                _stick("0.41", "0.40"),
            )
        )
        self.assertTrue(any(f.side == "buy_yes" for f in scan_tape(tape, fill_rule="touch")))
        self.assertFalse(any(f.side == "buy_yes" for f in scan_tape(tape, fill_rule="through")))

    def test_trading_past_the_bid_fills_under_both(self):
        tape = _tape(
            self._sticks(
                _stick("0.41", "0.40"),
                _stick("0.41", "0.40", low="0.38", high="0.41"),
                _stick("0.41", "0.40"),
            )
        )
        for rule in ("touch", "through"):
            with self.subTest(rule=rule):
                self.assertTrue(any(f.side == "buy_yes" for f in scan_tape(tape, fill_rule=rule)))

    def test_unknown_fill_rule_is_refused(self):
        tape = _tape(self._sticks(_stick("0.41", "0.40"), _stick("0.41", "0.40"), _stick("0.41", "0.40")))
        with self.assertRaises(ValueError):
            scan_tape(tape, fill_rule="optimistic")

    def test_min_volume_filters_thin_prints(self):
        tape = _tape(
            self._sticks(
                _stick("0.41", "0.40"),
                _stick("0.41", "0.40", low="0.38", high="0.41", volume="5"),
                _stick("0.41", "0.40"),
            )
        )
        self.assertTrue(scan_tape(tape, min_volume=1.0))
        self.assertFalse(scan_tape(tape, min_volume=50.0))


class CostBucketTests(unittest.TestCase):
    """A rested ask at 0.10 is a long NO at 0.90, not a 10c lottery ticket."""

    def setUp(self):
        self.base = 10_000_000 // 1000 - 3600

    def _sticks(self, quote, nxt, later) -> dict:
        return {self.base: quote, self.base + 60: nxt, self.base + 120: later}

    def test_a_sold_yes_costs_one_minus_the_quote(self):
        tape = _tape(
            self._sticks(
                _stick("0.10", "0.09"),
                _stick("0.10", "0.09", low="0.09", high="0.12"),
                _stick("0.10", "0.09"),
            ),
            result="no",
        )
        sell = next(f for f in scan_tape(tape) if f.side == "sell_yes")
        self.assertAlmostEqual(sell.price, 0.10)
        self.assertAlmostEqual(sell.cost, 0.90)
        # Settled NO, so the sold YES keeps the whole 0.10.
        self.assertAlmostEqual(sell.settle_pnl, 0.10, places=9)

    def test_a_bought_yes_costs_what_we_bid(self):
        tape = _tape(
            self._sticks(
                _stick("0.91", "0.90"),
                _stick("0.91", "0.90", low="0.88", high="0.91"),
                _stick("0.91", "0.90"),
            )
        )
        buy = next(f for f in scan_tape(tape) if f.side == "buy_yes")
        self.assertAlmostEqual(buy.cost, 0.90)

    def test_bands_report_by_cost_not_by_quote(self):
        # Only the ask gets hit: the low never reaches the 0.09 bid.
        tape = _tape(
            self._sticks(
                _stick("0.10", "0.09"),
                _stick("0.10", "0.09", low="0.10", high="0.12"),
                _stick("0.10", "0.09"),
            ),
            result="no",
        )
        bands = scan_tapes([tape])["bands"]
        self.assertEqual(bands["极高尾 >0.95"]["fills"], 0)
        self.assertEqual(bands["便宜彩票 <0.15"]["fills"], 0)
        self.assertEqual(bands["高尾 0.85-0.95"]["fills"], 1)

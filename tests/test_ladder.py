import unittest

from btchour.replay import EventTape
from btchour.research.ladder import pair_edge, scan_tape, scan_tapes


def _stick(ask: str, bid: str, volume: str = "10") -> dict:
    return {
        "yes_ask": {"close_dollars": ask, "high_dollars": ask, "low_dollars": ask},
        "yes_bid": {"close_dollars": bid, "high_dollars": bid, "low_dollars": bid},
        "volume_fp": volume,
    }


def _tape(rungs: dict, volume: str = "10") -> EventTape:
    maturity_ms = 4_600_000
    candles = {
        strike: {1000: _stick(ask, bid, volume)} for strike, (ask, bid) in rungs.items()
    }
    results = {strike: "no" for strike in rungs}
    return EventTape(
        "KXBTCD-26SEP1414", {0: 78_000.0}, candles, results, maturity_ms,
        (min(rungs), max(rungs)),
    )


class PairEdgeTests(unittest.TestCase):
    def test_a_crossed_ladder_locks_a_profit(self):
        # Buying YES(K1) at 0.30 and NO(K2) at 1-0.60 costs 0.70 and always
        # pays at least 1.
        edge, fees = pair_edge(0.30, 0.60)
        self.assertGreater(edge, 0.0)
        self.assertAlmostEqual(edge, 0.60 - 0.30 - fees, places=9)

    def test_an_ordered_ladder_has_no_edge(self):
        edge, _ = pair_edge(0.60, 0.30)
        self.assertLess(edge, 0.0)

    def test_fees_are_charged_on_both_legs(self):
        from btchour.fees import taker_fee

        _, fees = pair_edge(0.40, 0.45)
        self.assertAlmostEqual(fees, taker_fee(0.40, 1.0) + taker_fee(0.55, 1.0), places=9)


class ScanTests(unittest.TestCase):
    def test_finds_an_injected_cross(self):
        # A bid at the higher strike above the ask at the lower one.
        found = scan_tape(_tape({78_000.0: ("0.30", "0.29"), 78_100.0: ("0.60", "0.59")}), min_seconds=0.0)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].low_strike, 78_000.0)
        self.assertEqual(found[0].high_strike, 78_100.0)
        self.assertGreater(found[0].edge, 0.0)

    def test_a_normal_ladder_is_clean(self):
        found = scan_tape(_tape({78_000.0: ("0.60", "0.59"), 78_100.0: ("0.30", "0.29")}), min_seconds=0.0)
        self.assertEqual(found, [])

    def test_a_quote_nobody_traded_is_not_evidence(self):
        crossed = {78_000.0: ("0.30", "0.29"), 78_100.0: ("0.60", "0.59")}
        self.assertEqual(scan_tape(_tape(crossed, volume="0"), min_seconds=0.0), [])
        self.assertEqual(
            len(scan_tape(_tape(crossed, volume="0"), min_seconds=0.0, require_volume=False)), 1
        )

    def test_the_last_minute_is_skipped(self):
        crossed = _tape({78_000.0: ("0.30", "0.29"), 78_100.0: ("0.60", "0.59")})
        self.assertEqual(scan_tape(crossed, min_seconds=4_000.0), [])

    def test_summary_counts_hours_and_edges(self):
        report = scan_tapes(
            [_tape({78_000.0: ("0.30", "0.29"), 78_100.0: ("0.60", "0.59")})], min_seconds=0.0
        )
        self.assertEqual(report["hours"], 1)
        self.assertEqual(report["crosses"], 1)
        self.assertEqual(report["hours_with_a_cross"], 1)
        self.assertGreater(report["max_edge"], 0.0)


if __name__ == "__main__":
    unittest.main()

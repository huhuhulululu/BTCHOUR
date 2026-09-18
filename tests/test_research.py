import unittest
from datetime import datetime, timezone

from btchour.config import Settings, load_settings
from btchour.model import SECONDS_PER_YEAR, TWAP_SECONDS, digital_prob, variance_seconds
from btchour.replay import ReplayBar, replay_bars, replay_tape
from btchour.research.evaluate import Variant, run_variant, split_tapes
from btchour.research.metrics import bootstrap_ci, max_drawdown, summarize_takes, t_stat
from btchour.research.sim import SimConfig, regime_config, simulate_tape, simulate_tapes


class VarianceSecondsTests(unittest.TestCase):
    def test_full_window_costs_two_thirds_of_a_minute(self):
        self.assertAlmostEqual(variance_seconds(600.0), 600.0 - 40.0)
        self.assertAlmostEqual(variance_seconds(TWAP_SECONDS), TWAP_SECONDS / 3.0)

    def test_inside_the_window_variance_falls_as_the_cube(self):
        self.assertAlmostEqual(variance_seconds(30.0), 30.0**3 / (3.0 * 60.0**2))
        self.assertLess(variance_seconds(10.0), variance_seconds(30.0))
        self.assertEqual(variance_seconds(0.0), 0.0)

    def test_legacy_mode_restores_the_old_floor(self):
        self.assertEqual(variance_seconds(10.0, twap_seconds=0.0), 45.0)

    def test_a_decided_contract_is_priced_as_decided_near_the_close(self):
        # $50 in the money with a minute to go: the old floor said 80/20, the
        # settlement rule says the print is nearly locked.
        twap = digital_prob(78_050, 78_000, 60.0, 0.55)
        legacy = digital_prob(78_050, 78_000, 60.0, 0.55, twap_seconds=0.0)
        self.assertGreater(twap, 0.90)
        self.assertLess(legacy, 0.85)


class SimTests(unittest.TestCase):
    def test_tape_is_replayable(self):
        tape = simulate_tape(
            SimConfig(seed=3), datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
        )
        self.assertIsNone(tape.error)
        self.assertEqual(len(tape.spots), 60)
        self.assertIsNotNone(tape.band)
        report = replay_tape(tape, load_settings())
        self.assertNotIn("error", report)
        self.assertEqual(report["event_ticker"], tape.event_ticker)

    def test_quotes_are_a_book_not_a_crossed_mess(self):
        tape = simulate_tape(
            SimConfig(seed=5), datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
        )
        for sticks in tape.candles.values():
            for stick in sticks.values():
                self.assertLess(stick["yes_bid"]["close_dollars"], stick["yes_ask"]["close_dollars"])

    def test_hours_share_one_price_path(self):
        tapes = simulate_tapes(3, SimConfig(seed=9))
        chronological = list(reversed(tapes))
        for earlier, later in zip(chronological, chronological[1:]):
            last = earlier.spots[max(earlier.spots)]
            first = later.spots[min(later.spots)]
            self.assertLess(abs(last - first) / last, 0.01)


class WickFillTests(unittest.TestCase):
    """A rest filled at the minute's wick and marked at the close is free money."""

    def _bars(self, strike: float, maturity: float, wick: bool) -> list[ReplayBar]:
        # NO ask is 1 - yes_bid. A NO rest at 0.25 needs the yes bid at 0.75:
        # `wick` only touches it inside the minute, the other closes there.
        touch = (
            {"yes_ask": 0.65, "yes_bid": 0.64, "yes_bid_high": 0.76}
            if wick
            else {"yes_ask": 0.76, "yes_bid": 0.75}
        )
        return [
            ReplayBar(int(maturity - 1800), 78800, 0.55, {strike: {"yes_ask": 0.65, "yes_bid": 0.64}}, impulse=0),
            ReplayBar(int(maturity - 1740), 78790, 0.55, {strike: {"yes_ask": 0.64, "yes_bid": 0.63}}, impulse=-160),
            ReplayBar(int(maturity - 1680), 78720, 0.55, {strike: touch}, impulse=-180),
            ReplayBar(int(maturity - 1620), 78640, 0.55, {strike: {"yes_ask": 0.60, "yes_bid": 0.59}}, impulse=-120),
        ]

    def test_default_replay_does_not_fill_on_a_wick_alone(self):
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        self.assertFalse(settings.replay_wick_fill)
        report = replay_bars(
            "KXBTCD-26AUG2520", self._bars(78699.99, maturity, True), {78699.99: "no"}, maturity, settings
        )
        self.assertEqual(report["takes"], [])

    def test_a_close_at_the_rest_still_fills(self):
        maturity = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc).timestamp()
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        report = replay_bars(
            "KXBTCD-26AUG2520", self._bars(78699.99, maturity, False), {78699.99: "no"}, maturity, settings
        )
        self.assertEqual(len(report["takes"]), 1)
        self.assertEqual(report["takes"][0]["play"], "impulse_wait")

    def test_a_fair_book_does_not_hand_out_perfect_records(self):
        base = load_settings()
        tapes = simulate_tapes(12, regime_config("fair", seed=1))
        wick = run_variant(tapes, Variant("wick", extras={"replay_wick_fill": True}), base)
        honest = run_variant(tapes, Variant("close", extras={"replay_wick_fill": False}), base)
        # Same tapes, same rules: only the fill model differs. Nothing is
        # supposed to be profitable here, so the gap is the backtester's.
        self.assertGreater(wick["trades"], honest["trades"])
        self.assertGreater(wick["total_pnl"], honest["total_pnl"])


class MetricsTests(unittest.TestCase):
    def test_bootstrap_brackets_the_mean(self):
        values = [1.0, -1.0, 2.0, -2.0, 0.5, 0.25, -0.75, 1.5]
        lo, hi = bootstrap_ci(values)
        mean = sum(values) / len(values)
        self.assertLessEqual(lo, mean)
        self.assertGreaterEqual(hi, mean)

    def test_empty_and_single_samples_do_not_explode(self):
        self.assertEqual(bootstrap_ci([]), (0.0, 0.0))
        self.assertEqual(bootstrap_ci([1.25]), (1.25, 1.25))
        self.assertEqual(t_stat([]), 0.0)

    def test_drawdown_is_peak_to_trough(self):
        self.assertAlmostEqual(max_drawdown([1.0, 1.0, -3.0, 0.5]), 3.0)
        self.assertEqual(max_drawdown([1.0, 2.0]), 0.0)

    def test_summary_counts_reasons_and_wins(self):
        takes = [
            {"pnl": 1.0, "roi": 0.2, "exit_reason": "t_clip"},
            {"pnl": -0.5, "roi": -0.1, "exit_reason": "t_stop"},
            {"pnl": 0.25, "roi": 0.05, "exit_reason": "t_clip"},
        ]
        summary = summarize_takes(takes, hours=10)
        self.assertEqual(summary.trades, 3)
        self.assertEqual(summary.wins, 2)
        self.assertAlmostEqual(summary.trades_per_hour, 0.3)
        self.assertEqual(summary.exit_reasons, {"t_clip": 2, "t_stop": 1})


class ArchiveTests(unittest.TestCase):
    def test_round_trip_keeps_the_tape_replayable(self):
        import tempfile
        from pathlib import Path

        from btchour.research import dataset

        with tempfile.TemporaryDirectory() as tmp:
            original = dataset.archive_dir
            dataset.archive_dir = lambda: Path(tmp)
            try:
                tape = simulate_tape(
                    SimConfig(seed=4), datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
                )
                self.assertTrue(dataset.store_tape(tape))
                self.assertEqual(dataset.archived_tickers(), [tape.event_ticker])
                loaded = dataset.load_archive()
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].results, tape.results)
                self.assertNotIn("error", replay_tape(loaded[0], load_settings()))
                self.assertEqual(dataset.archive_summary()["hours"], 1)
            finally:
                dataset.archive_dir = original

    def test_a_broken_hour_is_not_archived(self):
        import tempfile
        from pathlib import Path

        from btchour.replay import EventTape
        from btchour.research import dataset

        with tempfile.TemporaryDirectory() as tmp:
            original = dataset.archive_dir
            dataset.archive_dir = lambda: Path(tmp)
            try:
                broken = EventTape("KXBTCD-26SEP0115", {}, {}, {}, 0, None, error="incomplete data")
                self.assertFalse(dataset.store_tape(broken))
                self.assertEqual(dataset.archived_tickers(), [])
            finally:
                dataset.archive_dir = original


class CompareTests(unittest.TestCase):
    def test_compare_reports_both_halves(self):
        from btchour.research.evaluate import compare

        tapes = simulate_tapes(8, regime_config("fair", seed=6))
        payload = compare(tapes, [Variant("flex", "flex")], load_settings(), train_fraction=0.5)
        self.assertEqual(payload["train_hours"] + payload["test_hours"], 8)
        row = payload["variants"][0]
        self.assertIn("train", row)
        self.assertIn("test", row)
        self.assertEqual(row["train"]["hours"], payload["train_hours"])


class BaselineTests(unittest.TestCase):
    def test_scores_model_and_mid_on_the_same_rungs(self):
        from btchour.research.baseline import score_tapes

        report = score_tapes(simulate_tapes(6, regime_config("fair", seed=8)))
        self.assertEqual(report["hours"], 6)
        self.assertGreater(report["observations"], 0)
        self.assertEqual(
            report["observations"], sum(row["observations"] for row in report["buckets"])
        )
        for key in ("model_brier", "mid_brier"):
            self.assertGreaterEqual(report[key], 0.0)
            self.assertLessEqual(report[key], 1.0)

    def test_an_empty_sample_does_not_crash(self):
        from btchour.research.baseline import render, score_tapes

        report = score_tapes([])
        self.assertEqual(report["observations"], 0)
        self.assertIsNone(report["model_beats_mid"])
        self.assertIn("没有可评分的读数", render(report))

    def test_buckets_split_on_the_model_mid_gap(self):
        from btchour.research.baseline import BUCKETS

        edges = [lo for lo, _, _ in BUCKETS]
        self.assertEqual(edges, sorted(edges))
        # The 20% gate needs a 5c+ gap, so those buckets have to exist.
        self.assertIn("5-10¢", [label for _, _, label in BUCKETS])


class SplitTests(unittest.TestCase):
    def test_train_is_the_older_half(self):
        tapes = simulate_tapes(10, SimConfig(seed=2))
        train, test = split_tapes(tapes, 0.5)
        self.assertEqual(len(train), 5)
        self.assertEqual(len(test), 5)
        newest_train = max(t.maturity_ms for t in train)
        oldest_test = min(t.maturity_ms for t in test)
        self.assertLess(newest_train, oldest_test)

    def test_fraction_must_be_a_fraction(self):
        with self.assertRaises(ValueError):
            split_tapes(simulate_tapes(4, SimConfig(seed=2)), 1.0)


if __name__ == "__main__":
    unittest.main()


class ClusterCITests(unittest.TestCase):
    """Fills inside one hour ride one BTC path, so hours are the draw."""

    def test_clustering_widens_the_interval_when_hours_disagree(self):
        from btchour.research.metrics import bootstrap_ci, cluster_ci

        # Five hours, each internally unanimous and wildly different from the
        # next. Resampling fills calls that precise; resampling hours does not.
        groups = [[v] * 40 for v in (-1.0, -0.5, 0.0, 0.5, 1.0)]
        flat = [v for g in groups for v in g]
        naive_lo, naive_hi = bootstrap_ci(flat)
        clus_lo, clus_hi = cluster_ci(groups)
        self.assertGreater(clus_hi - clus_lo, naive_hi - naive_lo)

    def test_a_single_hour_falls_back_to_the_plain_bootstrap(self):
        from btchour.research.metrics import cluster_ci

        lo, hi = cluster_ci([[0.3, 0.4, 0.5]])
        self.assertLessEqual(lo, 0.4)
        self.assertGreaterEqual(hi, 0.4)

    def test_empty_groups_are_dropped(self):
        from btchour.research.metrics import cluster_ci

        lo, hi = cluster_ci([[], [1.0], [], [1.0]])
        self.assertAlmostEqual(lo, 1.0)
        self.assertAlmostEqual(hi, 1.0)

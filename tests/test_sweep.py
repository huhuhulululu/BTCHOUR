from __future__ import annotations

import unittest
from datetime import datetime, timezone

from btchour.config import Settings, apply_playbook
from btchour.learn import journal_line
from btchour.replay import EventTape, ReplayBar, bars_from_tape, summarize_replays, tape_from_bars
from btchour.sweep import compact_run, sweep_tapes


class ApplyPlaybookTests(unittest.TestCase):
    def test_lock_disables_early_exit_and_uses_lock_p(self):
        settings = apply_playbook(Settings(lock_min_p=0.998), "lock")
        self.assertEqual(settings.playbook, "lock")
        self.assertFalse(settings.allow_early_exit)
        self.assertEqual(settings.min_win_prob, 0.998)
        self.assertTrue(settings.allow_maker)

    def test_can_turn_skip_off_without_changing_playbook_gates(self):
        settings = apply_playbook(Settings(playbook="flex"), "flex", skip_after_loss=False)
        self.assertEqual(settings.playbook, "flex")
        self.assertFalse(settings.skip_after_loss)
        self.assertTrue(settings.allow_early_exit)

    def test_extras_override_impulse_gates(self):
        settings = apply_playbook(
            Settings(),
            "flex",
            extras={"impulse_min_p": 0.30, "impulse_max_ask": 0.35},
        )
        self.assertEqual(settings.impulse_min_p, 0.30)
        self.assertEqual(settings.impulse_max_ask, 0.35)

    def test_extras_can_disable_impulse_wait(self):
        settings = apply_playbook(Settings(), "flex", extras={"impulse_wait": 0})
        self.assertFalse(settings.impulse_wait)
        settings = apply_playbook(Settings(), "flex", extras={"impulse_wait": False})
        self.assertFalse(settings.impulse_wait)


class JournalLineTests(unittest.TestCase):
    def test_empty_candidates_are_not_none_ask_none(self):
        self.assertEqual(journal_line({"status": "no_impulse", "candidates": []}), "")
        self.assertEqual(
            journal_line({"status": "blocked", "candidates": []}),
            "blocked_no_hourly_candidates",
        )
        self.assertEqual(
            journal_line({"status": "blocked", "candidates": [{}]}),
            "blocked_no_hourly_candidates",
        )

    def test_blocked_row_keeps_ticker_ask_and_reasons(self):
        line = journal_line(
            {
                "status": "blocked",
                "candidates": [
                    {
                        "ticker": "KXBTCD-26AUG2522-T78799.99",
                        "ask": 0.56,
                        "p": 0.51,
                        "reasons": ["ask 0.56>0.52", "p 0.51<0.52"],
                    }
                ],
            }
        )
        self.assertIn("T78799.99", line)
        self.assertIn("0.56", line)
        self.assertIn("ask 0.56>0.52", line)


class TapeTests(unittest.TestCase):
    def test_lock_uses_ask_low_flex_uses_close(self):
        maturity_s = 1_800_000_000.0
        minute_ms = int((maturity_s - 600) * 1000)
        end_ts = minute_ms // 1000 + 60
        tape = EventTape(
            event_ticker="KXBTCD-26AUG2516",
            spots={minute_ms: 79000.0},
            candles={
                79099.99: {
                    end_ts: {
                        "yes_ask": {"close_dollars": 0.90, "low_dollars": 0.80},
                        "yes_bid": {"close_dollars": 0.79},
                    }
                }
            },
            results={79099.99: "no"},
            maturity_ms=int(maturity_s * 1000),
            band=(78999.99, 79099.99),
        )
        flex = bars_from_tape(tape, Settings(playbook="flex"))
        lock = bars_from_tape(tape, Settings(playbook="lock"))
        self.assertEqual(flex[0].quotes[79099.99]["yes_ask"], 0.90)
        self.assertEqual(lock[0].quotes[79099.99]["yes_ask"], 0.80)

    def test_event_tape_round_trip(self):
        tape = EventTape(
            event_ticker="KXBTCD-26AUG2516",
            spots={1_800_000_000_000: 79000.0},
            candles={79099.99: {1_800_000_060: {"yes_ask": {"close_dollars": 0.5}}}},
            results={79099.99: "yes"},
            maturity_ms=1_800_003_600_000,
            band=(78999.99, 79099.99),
        )
        loaded = EventTape.from_dict(tape.to_dict())
        self.assertEqual(loaded.event_ticker, tape.event_ticker)
        self.assertEqual(loaded.spots, tape.spots)
        self.assertEqual(loaded.results, tape.results)
        self.assertEqual(loaded.band, tape.band)
        self.assertEqual(loaded.candles[79099.99][1_800_000_060]["yes_ask"]["close_dollars"], 0.5)


class SweepTests(unittest.TestCase):
    def test_compact_run_counts_wins(self):
        reports = [
            {
                "event_ticker": "KXBTCD-26AUG2516",
                "takes": [
                    {
                        "event_ticker": "KXBTCD-26AUG2516",
                        "side": "no",
                        "ask": 0.20,
                        "play": "impulse_t",
                        "exit_reason": "t_clip",
                        "roi": 0.15,
                        "pnl": 0.7,
                    }
                ],
            }
        ]
        summary = summarize_replays(reports, Settings(playbook="flex"), 1, write=False)
        compact = compact_run(summary, "flex_skip")
        self.assertEqual(compact["wins"], 1)
        self.assertEqual(compact["take_count"], 1)
        self.assertEqual(compact["takes"][0]["exit"], "t_clip")

    def test_skip_off_takes_the_hour_after_a_stop(self):
        settings = Settings(playbook="flex", max_contracts=1, max_notional=10, allow_early_exit=True)
        maturity_a = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc).timestamp()
        maturity_b = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc).timestamp()
        strike = 78799.99
        loss_bars = [
            ReplayBar(int(maturity_a - 1980), 78920, 0.55, {strike: {"yes_ask": 0.51, "yes_bid": 0.50}}),
            ReplayBar(int(maturity_a - 1920), 78880, 0.55, {strike: {"yes_ask": 0.51, "yes_bid": 0.50}}),
            ReplayBar(int(maturity_a - 1860), 78840, 0.55, {strike: {"yes_ask": 0.51, "yes_bid": 0.50}}),
            ReplayBar(int(maturity_a - 1800), 78720, 0.55, {strike: {"yes_ask": 0.51, "yes_bid": 0.50}}),
            ReplayBar(int(maturity_a - 1740), 78940, 0.55, {strike: {"yes_ask": 0.85, "yes_bid": 0.84}}),
        ]
        win_bars = [
            ReplayBar(int(maturity_b - 1980), 78680, 0.55, {strike: {"yes_ask": 0.48, "yes_bid": 0.47}}),
            ReplayBar(int(maturity_b - 1920), 78720, 0.55, {strike: {"yes_ask": 0.48, "yes_bid": 0.47}}),
            ReplayBar(int(maturity_b - 1860), 78760, 0.55, {strike: {"yes_ask": 0.48, "yes_bid": 0.47}}),
            ReplayBar(int(maturity_b - 1800), 78900, 0.55, {strike: {"yes_ask": 0.48, "yes_bid": 0.47}}),
            ReplayBar(int(maturity_b - 1740), 79020, 0.55, {strike: {"yes_ask": 0.62, "yes_bid": 0.61}}),
        ]
        tapes = [
            tape_from_bars("KXBTCD-26AUG2511", win_bars, {strike: "no"}, maturity_b, (78699.99, 78799.99)),
            tape_from_bars("KXBTCD-26AUG2510", loss_bars, {strike: "yes"}, maturity_a, (78799.99, 78899.99)),
        ]
        report = sweep_tapes(
            tapes,
            settings,
            variants=[
                {"name": "flex_skip", "playbook": "flex", "skip_after_loss": True},
                {"name": "flex_noskip", "playbook": "flex", "skip_after_loss": False},
                {"name": "lock", "playbook": "lock", "skip_after_loss": False},
            ],
            write=False,
        )
        by_name = {row["name"]: row for row in report["runs"]}
        self.assertEqual(by_name["flex_skip"]["take_count"], 1)
        self.assertEqual(by_name["flex_noskip"]["take_count"], 2)
        self.assertEqual(by_name["lock"]["take_count"], 0)
        self.assertEqual(by_name["flex_skip"]["takes"][0]["event"], "KXBTCD-26AUG2510")
        self.assertEqual(by_name["flex_skip"]["takes"][0]["exit"], "t_stop")
        noskip_events = {take["event"] for take in by_name["flex_noskip"]["takes"]}
        self.assertEqual(noskip_events, {"KXBTCD-26AUG2510", "KXBTCD-26AUG2511"})
        self.assertNotIn("KXBTCD-26AUG2511", {take["event"] for take in by_name["flex_skip"]["takes"]})

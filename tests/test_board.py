from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from btchour.board import (
    collect_board,
    is_true_coupon,
    md_table,
    render_board,
    short_hour,
    short_strike,
    working_fill_label,
)
from btchour.config import Settings
from btchour.store import Store

ET = ZoneInfo("America/New_York")


def _trade(**overrides):
    row = {
        "ticker": "KXBTCD-26AUG2722-T80799.99",
        "event_ticker": "KXBTCD-26AUG2722",
        "side": "no",
        "price": 0.25,
        "count": 10,
        "fee": 0.0,
        "cost": 2.5,
        "mode": "paper",
        "taker": False,
        "model_p": 0.4,
        "if_win_roi": 3.0,
        "expected_roi": 0.2,
        "status": "closed",
        "result": "t_clip",
        "pnl": 0.2558,
        "raw": {
            "play": "impulse_wait",
            "rest": 0.25,
            "ask": 0.40,
            "exit_price": 0.29,
        },
    }
    row.update(overrides)
    return row


class BoardFormatTests(unittest.TestCase):
    def test_md_table_is_plain_markdown(self):
        text = md_table(["账", "pnl"], [["真 coupon", "+0.4502"]])
        self.assertEqual(
            text,
            "| 账 | pnl |\n| --- | --- |\n| 真 coupon | +0.4502 |",
        )

    def test_short_labels(self):
        self.assertEqual(short_hour("KXBTCD-26AUG2802"), "AUG2802")
        self.assertEqual(short_hour("KXBTCD-26AUG2800"), "AUG2800")
        self.assertEqual(short_strike("KXBTCD-26AUG2802-T79499.99"), "T79499")
        self.assertEqual(short_strike("KXBTCD-26AUG2722-T80799.99"), "T80799")

    def test_true_coupon_excludes_old_taker(self):
        coupon = {"raw": '{"play":"impulse_wait","rest":0.25}'}
        taker = {"raw": '{"play":"impulse_t"}'}
        self.assertTrue(is_true_coupon(coupon))
        self.assertFalse(is_true_coupon(taker))

    def test_fill_label_needs_same_dir_and_ask(self):
        settings = Settings(impulse_min=100)
        self.assertEqual(working_fill_label("no", 0.25, 0.35, -70, settings), "等动量/ask")
        self.assertEqual(working_fill_label("no", 0.25, 0.35, -120, settings), "等ask")
        self.assertEqual(working_fill_label("no", 0.25, 0.25, -70, settings), "等动量")
        self.assertEqual(working_fill_label("no", 0.25, 0.25, -120, settings), "可成交")
        self.assertEqual(working_fill_label("no", 0.25, 0.24, -120, settings), "可成交")
        self.assertEqual(working_fill_label("no", 0.25, 0.23, -120, settings), "等ask")
        self.assertEqual(working_fill_label("no", 0.25, 0.03, -120, settings), "等ask")
        self.assertEqual(working_fill_label("no", 0.25, 0.23, -70, settings), "等动量/ask")
        self.assertEqual(working_fill_label("no", 0.25, 0.35, 130, settings), "反手撤")
        self.assertEqual(working_fill_label("yes", 0.25, 0.31, 79, settings), "等动量/ask")


class BoardLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "btchour.sqlite")
        self.settings = Settings(mode="paper", playbook="flex", impulse_min=100)
        self.now = datetime(2026, 8, 28, 1, 16, tzinfo=ET)

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_splits_paper_and_true_coupon_and_marks_skip_hour(self):
        self.store.record_trade(
            _trade(
                ticker="KXBTCD-26AUG2605-T78699.99",
                event_ticker="KXBTCD-26AUG2605",
                result="t_stop",
                pnl=-1.243,
                raw={"play": "impulse_t"},
            )
        )
        self.store.record_trade(
            _trade(
                ticker="KXBTCD-26AUG2723-T79799.99",
                event_ticker="KXBTCD-26AUG2723",
                result="t_wait_stop",
                pnl=-2.2204,
                raw={"play": "impulse_wait", "rest": 0.25, "exit_price": 0.03},
            )
        )
        self.store.record_trade(
            _trade(
                created_at="2026-08-28T05:08:39+00:00",
                ticker="KXBTCD-26AUG2802-T79499.99",
                event_ticker="KXBTCD-26AUG2802",
                status="working",
                result=None,
                pnl=None,
                raw={"play": "impulse_wait", "rest": 0.25, "ask": 0.35},
            )
        )
        self.store.record_journal("KXBTCD-26AUG2802", 79664.0, 64.0, 64.0, "no_coupon", "ask 0.45>0.42")
        snapshot = {
            "spot": {"price": 79664.0, "impulse": 64.0},
            "current_hour": {
                "markets": [
                    {
                        "ticker": "KXBTCD-26AUG2802-T79499.99",
                        "yes_ask": 0.64,
                        "no_ask": 0.36,
                    }
                ]
            },
        }
        replay = {"hours": 16, "take_count": 5, "wins": 4, "realized_pnl": 4.48}
        payload = collect_board(
            self.store,
            self.settings,
            now=self.now,
            snapshot=snapshot,
            replay=replay,
        )
        self.assertEqual(payload["hour"], "AUG2802")
        self.assertEqual(payload["paper"]["n"], 2)
        self.assertEqual(payload["true"]["n"], 1)
        self.assertEqual(payload["old_taker"]["n"], 1)
        self.assertEqual(payload["slots"], "1/3")
        self.assertEqual(payload["rests"][0]["fill"], "等动量/ask")
        hours = {row["hour"]: row for row in payload["hours"]}
        self.assertEqual(hours["AUG2723"]["result"], "stop")
        self.assertEqual(hours["AUG2723"]["next"], "skip下一小时")
        self.assertEqual(hours["AUG2800"]["result"], "skip小时")
        self.assertEqual(hours["AUG2801"]["result"], "0成交")
        self.assertEqual(hours["AUG2802"]["result"], "进行中")
        text = render_board(payload)
        self.assertIn("01:16 EDT", text)
        self.assertNotIn("UTC", text)
        self.assertIn("| 真 coupon | 1 | 0 | −2.2204 |", text)
        self.assertIn("不算样本", text)
        self.assertIn("不是达成", text)
        self.assertIn("旧 taker 不进这张表", text)


if __name__ == "__main__":
    unittest.main()

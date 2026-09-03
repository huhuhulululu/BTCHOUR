import unittest
from datetime import datetime, timedelta, timezone

from btchour.config import Settings, apply_playbook
from btchour.exits import OpenPosition, evaluate_exit
from btchour.kalshi import market_from_api
from btchour.model import SpotQuote
from btchour.strategy import (
    evaluate_cushion_market,
    is_settle_play,
    pick_cushion,
    pick_flex_entries,
    scan_markets,
)

NOW = datetime(2026, 9, 3, 12, 30, tzinfo=timezone.utc)


def hourly_market(strike, yes_ask=None, yes_bid=None, no_ask=None, no_bid=None, minutes_left=30):
    close = NOW + timedelta(minutes=minutes_left)
    open_time = close - timedelta(hours=1)
    return market_from_api(
        {
            "ticker": f"KXBTCD-26SEP0313-T{strike}",
            "event_ticker": "KXBTCD-26SEP0313",
            "title": "Bitcoin price",
            "subtitle": f"${strike} or above",
            "status": "active",
            "floor_strike": strike,
            "strike_type": "greater",
            "yes_ask_dollars": yes_ask,
            "yes_bid_dollars": yes_bid,
            "no_ask_dollars": no_ask,
            "no_bid_dollars": no_bid,
            "open_time": open_time.isoformat().replace("+00:00", "Z"),
            "close_time": close.isoformat().replace("+00:00", "Z"),
        }
    )


def edge_settings(**extra):
    return apply_playbook(Settings(), "edge", extras=extra)


class CushionEntryTests(unittest.TestCase):
    def test_wide_cushion_with_a_lagging_book_is_a_take(self):
        # spot $900 above the strike with 30 minutes left is several residual sigmas,
        # but the ladder still asks 0.84 for YES.
        market = hourly_market(77000.0, yes_ask=0.84, yes_bid=0.83)
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        rows = evaluate_cushion_market(market, spot, edge_settings(), NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.play, "cushion_hold")
        self.assertEqual(row.side, "yes")
        self.assertTrue(row.taker)
        self.assertAlmostEqual(row.ask, 0.84)
        self.assertIn("cushion_hold YES", row.reason)

    def test_below_the_strike_takes_the_no_side(self):
        market = hourly_market(77900.0, yes_ask=0.17, yes_bid=0.16)
        spot = SpotQuote(77000.0, "test", annual_vol=0.55)
        rows = evaluate_cushion_market(market, spot, edge_settings(), NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].side, "no")
        self.assertAlmostEqual(rows[0].ask, 0.84)

    def test_atm_has_no_cushion(self):
        market = hourly_market(77900.0, yes_ask=0.52, yes_bid=0.51)
        spot = SpotQuote(77905.0, "test", annual_vol=0.55)
        self.assertEqual(evaluate_cushion_market(market, spot, edge_settings(), NOW), [])

    def test_book_already_caught_up_is_not_a_take(self):
        # 0.96 is above the cap: the disagreement the play trades on is gone.
        market = hourly_market(77000.0, yes_ask=0.96, yes_bid=0.95)
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        self.assertEqual(evaluate_cushion_market(market, spot, edge_settings(), NOW), [])

    def test_never_takes_a_045_070_taker(self):
        """008 stays frozen: the floor is 70c, so the 0.45-0.70 band is unreachable."""
        market = hourly_market(77000.0, yes_ask=0.62, yes_bid=0.61)
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        self.assertEqual(evaluate_cushion_market(market, spot, edge_settings(), NOW), [])

    def test_last_two_minutes_are_closed(self):
        market = hourly_market(77000.0, yes_ask=0.84, yes_bid=0.83, minutes_left=1)
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        self.assertEqual(evaluate_cushion_market(market, spot, edge_settings(), NOW), [])

    def test_off_by_default(self):
        market = hourly_market(77000.0, yes_ask=0.84, yes_bid=0.83)
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        self.assertFalse(Settings().cushion_hold)
        self.assertEqual(evaluate_cushion_market(market, spot, Settings(), NOW), [])

    def test_fifteen_minute_ladder_is_out_of_scope(self):
        close = NOW + timedelta(minutes=8)
        market = market_from_api(
            {
                "ticker": "KXBTC15M-26SEP0312-T77000",
                "event_ticker": "KXBTC15M-26SEP0312",
                "title": "Bitcoin price",
                "subtitle": "$77000 or above",
                "status": "active",
                "floor_strike": 77000.0,
                "strike_type": "greater",
                "yes_ask_dollars": 0.84,
                "yes_bid_dollars": 0.83,
                "open_time": (close - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
                "close_time": close.isoformat().replace("+00:00", "Z"),
            }
        )
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        self.assertEqual(evaluate_cushion_market(market, spot, edge_settings(), NOW), [])


class CushionSelectionTests(unittest.TestCase):
    def ladder(self):
        return [
            hourly_market(77000.0, yes_ask=0.84, yes_bid=0.83),
            hourly_market(77200.0, yes_ask=0.80, yes_bid=0.79),
            hourly_market(77400.0, yes_ask=0.74, yes_bid=0.73),
            hourly_market(77600.0, yes_ask=0.71, yes_bid=0.70),
        ]

    def test_cap_per_hour_and_widest_cushion_first(self):
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        rows = scan_markets(self.ladder(), spot, edge_settings(), NOW)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.strike for row in rows], [77000.0, 77200.0, 77400.0])

    def test_cap_is_configurable(self):
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        rows = scan_markets(self.ladder(), spot, edge_settings(cushion_max_per_hour=1), NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].strike, 77000.0)

    def test_pick_cushion_dedupes_a_repeated_rung(self):
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        settings = edge_settings()
        market = hourly_market(77000.0, yes_ask=0.84, yes_bid=0.83)
        rows = evaluate_cushion_market(market, spot, settings, NOW) * 3
        self.assertEqual(len(pick_cushion(rows, settings)), 1)

    def test_flex_prefers_cushion_over_a_coupon_rest(self):
        spot = SpotQuote(77900.0, "test", annual_vol=0.55, impulse=-180.0)
        settings = apply_playbook(Settings(), "flex", extras={"cushion_hold": True})
        opps = scan_markets(
            self.ladder() + [hourly_market(78300.0, yes_ask=0.63, yes_bid=0.62)],
            spot,
            settings,
            NOW,
        )
        chosen = pick_flex_entries(opps)
        self.assertTrue(chosen)
        self.assertTrue(all(row.play == "cushion_hold" for row in chosen))

    def test_flex_leaves_cushion_off_unless_asked(self):
        spot = SpotQuote(77900.0, "test", annual_vol=0.55)
        opps = scan_markets(self.ladder(), spot, Settings(playbook="flex"), NOW)
        self.assertFalse([row for row in opps if row.play == "cushion_hold"])


class CushionExitTests(unittest.TestCase):
    def position(self, cost=0.85):
        return OpenPosition(
            ticker="KXBTCD-26SEP0313-T77000",
            event_ticker="KXBTCD-26SEP0313",
            side="yes",
            cost=cost,
            count=1.0,
            play="cushion_hold",
            entry_p=0.94,
            held_seconds=900.0,
        )

    def test_is_a_settle_play(self):
        self.assertTrue(is_settle_play("cushion_hold"))
        self.assertTrue(is_settle_play("lock_hold"))
        self.assertFalse(is_settle_play("impulse_wait"))
        self.assertFalse(is_settle_play("swing_t"))

    def test_no_clip_when_the_bid_runs_up(self):
        market = hourly_market(77000.0, yes_ask=0.95, yes_bid=0.94)
        decision = evaluate_exit(
            self.position(), market, 0.96, 600.0, Settings(playbook="flex", allow_early_exit=True)
        )
        self.assertIsNone(decision.action)

    def test_no_stop_when_the_bid_falls(self):
        market = hourly_market(77000.0, yes_ask=0.55, yes_bid=0.54)
        decision = evaluate_exit(
            self.position(), market, 0.58, 600.0, Settings(playbook="flex", allow_early_exit=True)
        )
        self.assertIsNone(decision.action)

    def test_no_twap_flatten(self):
        market = hourly_market(77000.0, yes_ask=0.92, yes_bid=0.91, minutes_left=1)
        decision = evaluate_exit(
            self.position(), market, 0.95, 20.0, Settings(playbook="flex", allow_early_exit=True)
        )
        self.assertIsNone(decision.action)

    def test_swing_playbook_cannot_drag_it_into_做T(self):
        market = hourly_market(77000.0, yes_ask=0.55, yes_bid=0.54)
        decision = evaluate_exit(
            self.position(), market, 0.58, 600.0, Settings(playbook="swing", allow_early_exit=True)
        )
        self.assertIsNone(decision.action)

    def test_lock_on_book_still_reachable_at_a_locked_profit(self):
        # Cheap entry plus a bid that already locks 20% after exit fees: taking it is
        # never a loss, and the study shows it is P&L-neutral against holding.
        market = hourly_market(77000.0, yes_ask=0.99, yes_bid=0.98)
        decision = evaluate_exit(
            self.position(cost=0.72), market, 0.99, 600.0,
            Settings(playbook="flex", allow_early_exit=True),
        )
        self.assertIsNotNone(decision.action)
        self.assertEqual(decision.action.reason, "lock_on_book")


class CouponHoldTests(unittest.TestCase):
    """017: `impulse_wait_hold` carries a filled coupon to settlement.

    66 days of KXBTCD: the same 348 fills are -0.86c held and -4.14c (t=-4.99) once the
    clip band, the -80% stop and the 8-minute scratch run on top.
    """

    def position(self):
        return OpenPosition(
            ticker="KXBTCD-26SEP0313-T78300",
            event_ticker="KXBTCD-26SEP0313",
            side="no",
            cost=0.25,
            count=1.0,
            play="impulse_wait",
            entry_p=0.44,
            held_seconds=900.0,
        )

    def flex(self, **extra):
        return apply_playbook(Settings(), "flex", extras=extra)

    def test_default_still_clips(self):
        """005 is untouched unless the switch is set."""
        self.assertFalse(Settings().impulse_wait_hold)
        market = hourly_market(78300.0, yes_ask=0.60, yes_bid=0.59)  # NO bid 0.40
        decision = evaluate_exit(self.position(), market, 0.55, 900.0, self.flex())
        self.assertIsNotNone(decision.action)
        self.assertEqual(decision.action.reason, "t_clip")

    def test_hold_switch_drops_the_clip(self):
        market = hourly_market(78300.0, yes_ask=0.60, yes_bid=0.59)
        decision = evaluate_exit(
            self.position(), market, 0.55, 900.0, self.flex(impulse_wait_hold=True)
        )
        self.assertIsNone(decision.action)

    def test_hold_switch_drops_the_wait_stop(self):
        market = hourly_market(78300.0, yes_ask=0.97, yes_bid=0.96)  # NO bid 0.03
        settings = self.flex()
        self.assertEqual(
            evaluate_exit(self.position(), market, 0.10, 900.0, settings).action.reason,
            "t_wait_stop",
        )
        held = evaluate_exit(
            self.position(), market, 0.10, 900.0, self.flex(impulse_wait_hold=True)
        )
        self.assertIsNone(held.action)

    def test_hold_switch_drops_the_scratch(self):
        market = hourly_market(78300.0, yes_ask=0.74, yes_bid=0.73)  # NO bid 0.26
        position = OpenPosition(
            ticker="KXBTCD-26SEP0313-T78300", event_ticker="KXBTCD-26SEP0313",
            side="no", cost=0.25, count=1.0, play="impulse_wait",
            entry_p=0.44, held_seconds=600.0,
        )
        self.assertEqual(
            evaluate_exit(position, market, 0.45, 900.0, self.flex()).action.reason, "t_scratch"
        )
        self.assertIsNone(
            evaluate_exit(position, market, 0.45, 900.0, self.flex(impulse_wait_hold=True)).action
        )

    def test_hold_switch_does_not_swap_the_clip_for_lock_on_book(self):
        """A 20% lock on a 25c entry is `t_clip` under another name."""
        market = hourly_market(78300.0, yes_ask=0.60, yes_bid=0.59)
        decision = evaluate_exit(
            self.position(), market, 0.55, 900.0, self.flex(impulse_wait_hold=True)
        )
        self.assertIsNone(decision.action)

    def test_hold_switch_leaves_other_plays_alone(self):
        """A `hold_edge` position keeps `lock_on_book` with the switch on."""
        position = OpenPosition(
            ticker="KXBTCD-26SEP0313-T77000", event_ticker="KXBTCD-26SEP0313",
            side="yes", cost=0.72, count=1.0, play="hold_edge", entry_p=0.90,
        )
        market = hourly_market(77000.0, yes_ask=0.99, yes_bid=0.98)
        decision = evaluate_exit(
            position, market, 0.99, 900.0,
            apply_playbook(Settings(), "flex", extras={"impulse_wait_hold": True}),
        )
        self.assertIsNotNone(decision.action)
        self.assertEqual(decision.action.reason, "lock_on_book")

    def test_swing_t_is_untouched_by_the_coupon_switch(self):
        position = OpenPosition(
            ticker="KXBTCD-26SEP0313-T78300", event_ticker="KXBTCD-26SEP0313",
            side="no", cost=0.40, count=1.0, play="swing_t", entry_p=0.60,
            held_seconds=300.0,
        )
        market = hourly_market(78300.0, yes_ask=0.75, yes_bid=0.74)  # NO bid 0.25
        decision = evaluate_exit(
            position, market, 0.55, 900.0, self.flex(impulse_wait_hold=True)
        )
        self.assertIsNotNone(decision.action)
        self.assertEqual(decision.action.reason, "t_stop")


class CushionSettingsTests(unittest.TestCase):
    def test_edge_playbook_turns_it_on_and_early_exit_off(self):
        settings = apply_playbook(Settings(), "edge")
        self.assertTrue(settings.cushion_hold)
        self.assertFalse(settings.allow_early_exit)
        self.assertFalse(settings.allow_maker)

    def test_defaults_match_the_frozen_study_rule(self):
        settings = Settings()
        self.assertAlmostEqual(settings.cushion_min, 1.5)
        self.assertAlmostEqual(settings.cushion_min_ask, 0.70)
        self.assertAlmostEqual(settings.cushion_max_ask, 0.90)
        self.assertEqual(settings.cushion_max_per_hour, 3)

    def test_016_and_017_are_both_off_by_default(self):
        """Neither proposal changes behaviour until the user signs it off."""
        settings = Settings()
        self.assertFalse(settings.cushion_hold)
        self.assertFalse(settings.impulse_wait_hold)
        flex = apply_playbook(Settings(), "flex")
        self.assertFalse(flex.cushion_hold)
        self.assertFalse(flex.impulse_wait_hold)

    def test_cushion_floor_can_never_reach_the_008_band(self):
        """008 stays frozen by construction, not by luck."""
        self.assertGreaterEqual(Settings().cushion_min_ask, 0.70)


if __name__ == "__main__":
    unittest.main()

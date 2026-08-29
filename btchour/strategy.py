from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.fees import TICK, fill_cost, lock_exit_price, max_entry_price
from btchour.kalshi import Market
from btchour.model import SpotQuote, digital_prob, effective_vol, sigma_cushion
from btchour.tickers import is_hourly_window, next_event_ticker, next_session_event_ticker, parse_event_ticker

T_PLAYS = frozenset({"swing_t", "impulse_t", "impulse_wait"})
WAIT_PLAYS = frozenset({"lock_wait", "impulse_wait"})


def dump_wait_rest_ready(impulse: float, settings: Settings) -> bool:
    """NO coupon rest: hang unless the tape has already flipped to a rally."""
    return coupon_rest_ready("no", impulse, settings)


def coupon_sides(impulse: float, settings: Settings) -> list[str]:
    """Follow the tape. Only a real rally rests YES; dump, quiet, or weak-up rest NO.

    涨 means |impulse| ≥ impulse_min. A +$6 / +$80 print is not a rally.
    Hanging YES on that weak-up, then eating the dump, is 乱挂.
    Do not rest both sides at once.
    """
    if impulse + 1e-9 >= settings.impulse_min:
        return ["yes"]
    return ["no"]


def coupon_rest_ready(side: str, impulse: float, settings: Settings) -> bool:
    """Hang when the 32–42¢ book is visible. Dump/rally is the fill filter.

    A lonely 0.25 under a 0.50–0.70 ATM mid is not a limit — that is
    coupon_in_band, not this gate. Fill still needs the same-way
    |impulse| ≥ impulse_min and ask==rest.
    """
    if impulse_wait_flipped(side, impulse, settings):
        return False
    need = settings.impulse_wait_rest_min
    if need <= 0:
        return True
    if side == "no":
        return impulse < 0 and abs(impulse) + 1e-9 >= need
    return impulse > 0 and abs(impulse) + 1e-9 >= need


def coupon_min_ask(side: str, settings: Settings) -> float:
    """NO keeps the 32¢ knife. YES 0.28 is the live daily mid (T78499 at 16:42 ET)."""
    if side == "yes":
        return min(settings.impulse_wait_min_ask, 0.28)
    return settings.impulse_wait_min_ask


def coupon_in_band(ask: float, settings: Settings) -> bool:
    """32–42¢ is the live coupon book. 0.50–0.70 ATM mid is a lonely 0.25."""
    return float(ask) <= settings.impulse_wait_coupon_ask + 1e-12


def impulse_wait_flipped(side: str, impulse: float, settings: Settings) -> bool:
    """Pull a dump wait only when the tape has flipped, not when the 3-minute print fades."""
    if side == "no":
        return impulse + 1e-9 >= settings.impulse_min
    if side == "yes":
        return impulse - 1e-9 <= -settings.impulse_min
    return True


def impulse_wait_wrong_side(side: str, impulse: float, settings: Settings) -> bool:
    """Pull a rest that is no longer the tape side.

    Dump/quiet NO fading is not wrong-side. Weak-up YES is: only a real
    rally may sit on YES. Sitting on YES into a dump is 一边倒吃瘪.
    """
    return str(side) not in coupon_sides(impulse, settings)


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_api_time(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def tape_at_rest(
    trades: list,
    side: str,
    rest: float,
    tick: float = TICK,
    since: datetime | None = None,
) -> float:
    """Contracts that actually printed at the rest after we hung.

    Kalshi `taker_book_side` is YES-leg vocabulary: ask ≡ sell YES / buy NO,
    bid ≡ buy YES. A YES bid 0.25 fills when a seller hits 0.25 (taker ask).
    A NO bid 0.25 fills when a buyer lifts YES at 0.75 (taker bid, no 0.25).
    Quote touch with zero tape is not a counterparty. Block prints do not
    sit on our rest.
    """
    total = 0.0
    want_taker = "ask" if side == "yes" else "bid"
    for row in trades or ():
        if row.get("is_block_trade"):
            continue
        if since is not None:
            created = _parse_api_time(row.get("created_time"))
            if created is not None and created < since:
                continue
        yes = _as_float(row.get("yes_price_dollars") or row.get("yes_price"))
        no = _as_float(row.get("no_price_dollars") or row.get("no_price"))
        if yes is not None and yes > 1:
            yes = yes / 100.0
        if no is not None and no > 1:
            no = no / 100.0
        if no is None and yes is not None:
            no = max(0.0, 1.0 - yes)
        px = yes if side == "yes" else no
        if px is None:
            continue
        if not (rest - tick - 1e-12 <= px <= rest + 1e-12):
            continue
        book = str(row.get("taker_book_side") or "").lower()
        if book and book != want_taker:
            continue
        size = _as_float(row.get("count_fp") if row.get("count_fp") not in (None, "") else row.get("count"))
        if size is None or size <= 0:
            continue
        total += size
    return total


def _ask_at_rest(ask: float, rest: float, tick: float = TICK) -> bool:
    """The offer is still at the rest, not already dumped through.

    Paper AUG2802 filled T79599/T79499 NO at 0.25 after the book was 0.03.
    That is not ask==rest. One tick through still counts as the rest print.
    """
    return rest - tick - 1e-12 <= float(ask) <= rest + 1e-12


def wait_book_crossed(
    side: str,
    rest: float,
    close_ask: float | None,
    *,
    yes_bid_high: float | None = None,
    yes_ask_low: float | None = None,
    impulse: float | None = None,
    min_impulse: float | None = None,
) -> bool:
    """Maker fill at rest if the close or minute extreme is still at the rest.

    A dump NO rest must not fill on the bounce rip (AUG2520 25¢ → marked 13¢)
    or on a faded ask==rest print (AUG2604 07:41, spot already +$42, then scratch).
    Keep the rest through fade; fill only while impulse is still a dump.
    YES needs the same-way |impulse| ≥ min_impulse; a +$90 print is not a fill.
    Ask already through the rest (AUG2802 3¢) is not a fill. Do not eat taker.
    """
    if impulse is not None:
        if side == "no" and impulse >= 0:
            return False
        if side == "yes" and impulse <= 0:
            return False
        if min_impulse is not None and abs(impulse) + 1e-9 < abs(min_impulse):
            return False
    if close_ask is not None and _ask_at_rest(close_ask, rest):
        return True
    if side == "no" and yes_bid_high is not None:
        return _ask_at_rest(1.0 - float(yes_bid_high), rest)
    if side == "yes" and yes_ask_low is not None:
        return _ask_at_rest(yes_ask_low, rest)
    return False


@dataclass(frozen=True)
class Opportunity:
    ticker: str
    event_ticker: str
    subtitle: str
    side: str
    book_side: str
    strike: float
    spot: float
    seconds_left: float
    model_p: float
    ask: float
    max_price: float
    limit_price: float
    taker: bool
    b: float
    if_win_roi: float
    expected_roi: float
    ev: float
    fee: float
    count: float
    reason: str
    play: str = "hold_edge"
    lock_price: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _seconds_left(close_time: str | None, now: datetime) -> float:
    if not close_time:
        return 0.0
    close = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    return (close - now).total_seconds()


def min_entry_seconds(settings: Settings) -> float:
    if settings.allow_early_exit:
        return max(8.0, settings.flatten_seconds)
    return 8.0


def _clip_count(price: float, settings: Settings) -> float:
    if price <= 0:
        return 0.0
    by_notional = settings.max_notional / price
    return max(0.0, min(settings.max_contracts, by_notional))


def market_window_ok(open_time: str | None, close_time: str | None, settings: Settings) -> bool:
    if not open_time or not close_time:
        return False
    start = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
    end = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    seconds = (end - start).total_seconds()
    if 8 * 60 <= seconds <= 25 * 60:
        return settings.scan_15m
    if 50 * 60 <= seconds <= 70 * 60:
        return True
    if 90 * 60 <= seconds <= 36 * 3600:
        return settings.scan_daily
    if seconds > 36 * 3600:
        return settings.scan_weekly
    return not settings.hourly_only


def _window_seconds(open_time: str | None, close_time: str | None) -> float:
    if not open_time or not close_time:
        return 0.0
    start = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
    end = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    return (end - start).total_seconds()


def is_fast_window(open_time: str | None, close_time: str | None) -> bool:
    seconds = _window_seconds(open_time, close_time)
    return 8 * 60 <= seconds <= 70 * 60


def is_kxbtcd_event(event_ticker: str) -> bool:
    try:
        parse_event_ticker(event_ticker)
        return True
    except ValueError:
        return False


def is_next_session_book(market: Market, now: datetime) -> bool:
    """Coupon only on the book that closes at the next whole hour."""
    ticker = market.event_ticker or ""
    return bool(ticker) and is_kxbtcd_event(ticker) and ticker == next_session_event_ticker(now)


def is_coupon_window(seconds_left: float, settings: Settings) -> bool:
    """Use time left to the next close, not the contract's lifetime.

    The 5pm book is often tagged daily and has been open ~25 hours. At 16:13
    ET it still has ~47 minutes left — that is the hourly we trade.
    """
    return settings.swing_min_seconds - 1e-12 <= seconds_left <= 70 * 60 + 1e-12


def _eligible_market(market: Market, settings: Settings, now: datetime) -> float | None:
    if market.strike is None or market.strike_type not in {"greater", "greater_or_equal"}:
        return None
    if settings.playbook in {"lock", "flex", "swing"}:
        if not market_window_ok(market.open_time, market.close_time, settings):
            return None
        seconds = _seconds_left(market.close_time, now)
        if seconds < 8:
            return None
        return seconds
    if settings.hourly_only and not is_hourly_window(market.open_time, market.close_time):
        return None
    seconds = _seconds_left(market.close_time, now)
    if seconds < min_entry_seconds(settings):
        return None
    return seconds


def _make_opportunity(
    *,
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    seconds: float,
    side: str,
    book_side: str,
    model_p: float,
    ask: float,
    taker: bool,
    limit: float,
    cost,
    play: str,
    reason: str,
) -> Opportunity | None:
    count = _clip_count(limit, settings)
    if count < 1:
        return None
    count = int(count)
    filled = fill_cost(ask if taker else limit, count, taker=taker)
    edge = cost.edge(model_p)
    lock = lock_exit_price(filled.cost, count, settings.target_profit)
    taker_cap = max_entry_price(settings.target_profit, taker=True)
    maker_cap = max_entry_price(settings.target_profit, taker=False)
    return Opportunity(
        ticker=market.ticker,
        event_ticker=market.event_ticker,
        subtitle=market.subtitle,
        side=side,
        book_side=book_side,
        strike=market.strike or 0.0,
        spot=spot.price,
        seconds_left=seconds,
        model_p=model_p,
        ask=ask,
        max_price=taker_cap if taker else maker_cap,
        limit_price=ask if taker else limit,
        taker=taker,
        b=edge.b,
        if_win_roi=edge.b,
        expected_roi=edge.ev,
        ev=edge.ev,
        fee=filled.fee,
        count=count,
        reason=reason,
        play=play,
        lock_price=lock,
    )


def evaluate_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    seconds = _eligible_market(market, settings, now)
    if seconds is None:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    sides = [
        ("yes", "bid", p_yes, market.yes_ask_effective),
        ("no", "ask", 1.0 - p_yes, market.no_ask_effective),
    ]
    found: list[Opportunity] = []
    for side, book_side, model_p, ask in sides:
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        if model_p + 1e-12 < settings.min_win_prob:
            continue
        taker_cap = max_entry_price(settings.target_profit, taker=True)
        maker_cap = max_entry_price(settings.target_profit, taker=False)
        taker = ask <= taker_cap
        if not taker and not settings.allow_maker:
            continue
        limit = min(ask if taker else maker_cap, maker_cap)
        if limit <= 0:
            continue
        cost = fill_cost(ask if taker else limit, 1.0, taker=taker)
        edge = cost.edge(model_p)
        if edge.b + 1e-12 < settings.target_profit:
            continue
        if edge.ev + 1e-12 < settings.min_ev:
            continue
        play = "hold_edge" if taker else "maker_rest"
        row = _make_opportunity(
            market=market,
            spot=spot,
            settings=settings,
            seconds=seconds,
            side=side,
            book_side=book_side,
            model_p=model_p,
            ask=ask,
            taker=taker,
            limit=limit,
            cost=cost,
            play=play,
            reason=(
                f"{play} {side.upper()} EV={edge.ev:.1%} p={edge.p:.1%} b={edge.b:.1%} "
                f"at {'taker' if taker else 'maker'} {ask if taker else limit:.2f}; "
                f"strike {market.strike:.2f} / spot {spot.price:.2f}"
            ),
        )
        if row:
            found.append(row)
    found.sort(key=lambda row: (row.expected_roi, row.model_p), reverse=True)
    return found


def evaluate_scalp_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    seconds = _eligible_market(market, settings, now)
    if seconds is None:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    sides = [
        ("yes", "bid", p_yes, market.yes_ask_effective),
        ("no", "ask", 1.0 - p_yes, market.no_ask_effective),
    ]
    found: list[Opportunity] = []
    for side, book_side, model_p, ask in sides:
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        if seconds + 1e-12 < settings.scalp_min_seconds:
            continue
        if ask > settings.scalp_max_entry + 1e-12:
            continue
        if model_p + 1e-12 < settings.scalp_min_p:
            continue
        if (model_p - ask) + 1e-12 < settings.scalp_min_gap:
            continue
        cost = fill_cost(ask, 1.0, taker=True)
        edge = cost.edge(model_p)
        if edge.ev + 1e-12 < settings.min_ev:
            continue
        lock = lock_exit_price(cost.cost, 1.0, settings.target_profit)
        if lock is None or lock > settings.scalp_max_lock + 1e-12:
            continue
        row = _make_opportunity(
            market=market,
            spot=spot,
            settings=settings,
            seconds=seconds,
            side=side,
            book_side=book_side,
            model_p=model_p,
            ask=ask,
            taker=True,
            limit=ask,
            cost=cost,
            play="markout_scalp",
            reason=(
                f"markout_scalp {side.upper()} gap={model_p - ask:.1%} p={model_p:.1%} "
                f"ask={ask:.2f} lock>={lock:.2f} holdEV={edge.ev:.1%}; "
                f"strike {market.strike:.2f} / spot {spot.price:.2f}"
            ),
        )
        if row:
            found.append(row)
    found.sort(key=lambda row: ((row.model_p - row.ask), row.ev), reverse=True)
    return found


def evaluate_lock_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    # Flex coupon hour only. Overnight lock on the 5pm daily (AUG2717 / AUG2817)
    # is hopping, not the next hourly close. Lock playbook still scans any book.
    if settings.playbook == "flex" and not is_next_session_book(market, now):
        return []
    seconds = _eligible_market(market, settings, now)
    if seconds is None:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    sigma = sigma_cushion(spot.price, market.strike, seconds, vol)
    if sigma + 1e-12 < settings.min_sigma:
        return []
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    sides = [
        ("yes", "bid", p_yes, market.yes_ask_effective),
        ("no", "ask", 1.0 - p_yes, market.no_ask_effective),
    ]
    found: list[Opportunity] = []
    for side, book_side, model_p, ask in sides:
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        if model_p + 1e-12 < settings.lock_min_p:
            continue
        taker_cap = max_entry_price(settings.target_profit, taker=True)
        maker_cap = max_entry_price(settings.target_profit, taker=False)
        taker = ask <= taker_cap
        if not taker and not settings.allow_maker:
            continue
        limit = ask if taker else maker_cap
        if limit <= 0:
            continue
        cost = fill_cost(ask if taker else limit, 1.0, taker=taker)
        edge = cost.edge(model_p)
        if edge.b + 1e-12 < settings.target_profit:
            continue
        if edge.ev + 1e-12 < settings.min_ev:
            continue
        play = "lock_hold" if taker else "lock_wait"
        row = _make_opportunity(
            market=market,
            spot=spot,
            settings=settings,
            seconds=seconds,
            side=side,
            book_side=book_side,
            model_p=model_p,
            ask=ask,
            taker=taker,
            limit=limit,
            cost=cost,
            play=play,
            reason=(
                f"{play} {side.upper()} EV={edge.ev:.1%} p={edge.p:.1%} b={edge.b:.1%} "
                f"σ={sigma:.1f} at {'taker' if taker else 'wait'} {ask if taker else limit:.2f}; "
                f"strike {market.strike:.2f} / spot {spot.price:.2f}"
            ),
        )
        if row:
            found.append(row)
    found.sort(key=lambda row: (row.taker, row.expected_roi, row.model_p), reverse=True)
    return found


def evaluate_swing_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    if not is_fast_window(market.open_time, market.close_time):
        return []
    seconds = _eligible_market(market, settings, now)
    if seconds is None or seconds + 1e-12 < settings.swing_min_seconds:
        return []
    if abs((market.strike or 0.0) - spot.price) > settings.swing_max_distance + 1e-9:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    sides = [
        ("yes", "bid", p_yes, market.yes_ask_effective),
        ("no", "ask", 1.0 - p_yes, market.no_ask_effective),
    ]
    found: list[Opportunity] = []
    for side, book_side, model_p, ask in sides:
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        if ask + 1e-12 < settings.swing_min_ask or ask > settings.swing_max_ask + 1e-12:
            continue
        if model_p + 1e-12 < settings.swing_min_p:
            continue
        if (model_p - ask) + 1e-12 < settings.swing_min_gap:
            continue
        cost = fill_cost(ask, 1.0, taker=True)
        edge = cost.edge(model_p)
        clip = lock_exit_price(cost.cost, 1.0, settings.swing_target)
        if clip is None or clip > 0.95:
            continue
        row = _make_opportunity(
            market=market,
            spot=spot,
            settings=settings,
            seconds=seconds,
            side=side,
            book_side=book_side,
            model_p=model_p,
            ask=ask,
            taker=True,
            limit=ask,
            cost=cost,
            play="swing_t",
            reason=(
                f"swing_t {side.upper()} 做T gap={model_p - ask:.1%} p={model_p:.1%} "
                f"ask={ask:.2f} clip>={clip:.2f} band={settings.swing_target:.0%}-{settings.swing_max_clip:.0%} "
                f"holdEV={edge.ev:.1%}; strike {market.strike:.2f} / spot {spot.price:.2f}"
            ),
        )
        if row:
            found.append(row)
    found.sort(key=lambda row: ((row.model_p - row.ask), row.ev), reverse=True)
    return found


def evaluate_impulse_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    now = now or datetime.now(timezone.utc)
    if settings.playbook == "flex" and not settings.impulse_taker:
        return []
    if not is_fast_window(market.open_time, market.close_time):
        return []
    seconds = _eligible_market(market, settings, now)
    if seconds is None or seconds + 1e-12 < settings.swing_min_seconds:
        return []
    move = spot.impulse
    if abs(move) + 1e-9 < settings.impulse_min:
        return []
    if abs((market.strike or 0.0) - spot.price) > settings.swing_max_distance + 1e-9:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    want_yes = move > 0
    side = "yes" if want_yes else "no"
    book_side = "bid" if want_yes else "ask"
    model_p = p_yes if want_yes else 1.0 - p_yes
    ask = market.yes_ask_effective if want_yes else market.no_ask_effective
    if ask is None or ask <= 0 or ask >= 1.0:
        return []
    if ask + 1e-12 < settings.swing_min_ask or ask > settings.impulse_max_ask + 1e-12:
        return []
    if model_p + 1e-12 < settings.impulse_min_p:
        return []
    if (model_p - ask) + 1e-12 < settings.impulse_min_gap:
        return []
    cost = fill_cost(ask, 1.0, taker=True)
    edge = cost.edge(model_p)
    clip = lock_exit_price(cost.cost, 1.0, settings.swing_target)
    if clip is None or clip > 0.95:
        return []
    row = _make_opportunity(
        market=market,
        spot=spot,
        settings=settings,
        seconds=seconds,
        side=side,
        book_side=book_side,
        model_p=model_p,
        ask=ask,
        taker=True,
        limit=ask,
        cost=cost,
        play="impulse_t",
        reason=(
            f"impulse_t {side.upper()} 动量 {move:+.0f} p={model_p:.1%} ask={ask:.2f} "
            f"clip>={clip:.2f} holdEV={edge.ev:.1%}; strike {market.strike:.2f} / spot {spot.price:.2f}"
        ),
    )
    return [row] if row else []


def _taker_impulse_qualifies(ask: float, model_p: float, settings: Settings) -> bool:
    if ask + 1e-12 < settings.swing_min_ask or ask > settings.impulse_max_ask + 1e-12:
        return False
    if model_p + 1e-12 < settings.impulse_min_p:
        return False
    return (model_p - ask) + 1e-12 >= settings.impulse_min_gap


def evaluate_impulse_wait_market(
    market: Market,
    spot: SpotQuote,
    settings: Settings,
    now: datetime | None = None,
) -> list[Opportunity]:
    """Rest 25¢ on the next hourly ladder when the coupon book is live.

    Scan every nearby rung ($600). A real rally hangs YES; dump / quiet /
    weak-up hang NO. NO still skips the 29¢ knife. YES may hang from 28¢.
    Only the 32–42¢ book; 0.50–0.70 ATM mid is a lonely 0.25, not a hang.
    Fill still needs ask==rest (close or the minute wick), |impulse| ≥ $100,
    and a real print at the rest after the hang. Do not take 0.45–0.70.
    Paper size is min(rest, tape). Up to three nearby rests. Clip 10–50%.
    If it will not come back, scratch or stop.
    """
    now = now or datetime.now(timezone.utc)
    if not settings.impulse_wait or not settings.allow_maker:
        return []
    if settings.playbook != "flex":
        return []
    if not is_next_session_book(market, now):
        return []
    if market.strike is None or market.strike_type not in {"greater", "greater_or_equal"}:
        return []
    seconds = _seconds_left(market.close_time, now)
    if not is_coupon_window(seconds, settings):
        return []
    move = spot.impulse
    reach = settings.impulse_wait_max_distance or settings.swing_max_distance
    if abs((market.strike or 0.0) - spot.price) > reach + 1e-9:
        return []
    vol = effective_vol(spot.annual_vol, settings.annual_vol)
    p_yes = digital_prob(spot.price, market.strike, seconds, vol)
    rest = settings.impulse_rest
    found: list[Opportunity] = []
    for side in coupon_sides(move, settings):
        if not coupon_rest_ready(side, move, settings):
            continue
        want_yes = side == "yes"
        book_side = "bid" if want_yes else "ask"
        model_p = p_yes if want_yes else 1.0 - p_yes
        ask = market.yes_ask_effective if want_yes else market.no_ask_effective
        if ask is None or ask <= 0 or ask >= 1.0:
            continue
        if ask <= rest + 1e-12:
            continue
        if ask + 1e-12 < coupon_min_ask(side, settings):
            continue
        if not coupon_in_band(ask, settings):
            continue
        if model_p + 1e-12 < rest:
            continue
        cost = fill_cost(rest, 1.0, taker=False)
        clip = lock_exit_price(cost.cost, 1.0, settings.swing_target)
        if clip is None or clip > 0.95:
            continue
        row = _make_opportunity(
            market=market,
            spot=spot,
            settings=settings,
            seconds=seconds,
            side=side,
            book_side=book_side,
            model_p=model_p,
            ask=ask,
            taker=False,
            limit=rest,
            cost=cost,
            play="impulse_wait",
            reason=(
                f"dump_gap {side.upper()} 看见 {ask:.2f} rest {rest:.2f} 动量 {move:+.0f} "
                f"p={model_p:.1%} clip>={clip:.2f}; strike {market.strike:.2f} / spot {spot.price:.2f}"
            ),
        )
        if row:
            found.append(row)
    return found


@dataclass
class SwingMemory:
    """Per-event 做T state: one ticker, one clip, then hands off. No flip.

    After an impulse_t / swing_t clip the hour is dead for another taker, but a
    later dump coupon may still rest. Paper AUG2611 14:00 YES taker then 14:14
    T78399 ask 0.41 was journaled and blocked. After an impulse_wait clip,
    do not hop to a second rest.
    """

    ticker: str | None = None
    side: str | None = None
    dead: bool = False
    play: str = ""


@dataclass
class SessionMemory:
    """After a losing T, skip wait on the next hour ticker. Same-direction taker is allowed.

    Consecutive losing hours do not stack another sit-out.

    Live flex sits out the whole skip hour — wait and taker. Paper skip-hour
    same-dir takers AUG2605/AUG2606 stopped −1.89; AUG2610 clipped, but that
    sidecar is not the dump-coupon goal.

    After an isolated impulse_t / swing_t stop, a dump coupon may still rest
    on that same hour. Skip is the next ticker. Paper AUG2614: 17:32 YES
    taker t_stop, 17:47 T78399 ask 0.40 journaled wait and the same-hour
    last_loss_event gate ate the rest.
    """

    last_loss_event: str | None = None
    last_side: str | None = None
    last_play: str = ""
    skip_next: bool = False
    skipped_event: str | None = None


def remember_swing_exit(
    memory: SwingMemory, ticker: str, side: str, reason: str, play: str = ""
) -> SwingMemory:
    return SwingMemory(ticker=ticker, side=side, dead=True, play=play)


def remember_session_exit(
    session: SessionMemory | None,
    event_ticker: str,
    reason: str,
    pnl: float | None,
    side: str | None = None,
    play: str = "",
) -> SessionMemory:
    lost = (pnl is not None and pnl < 0) or reason in {
        "t_stop",
        "t_wait_stop",
        "t_scratch",
        "t_fade",
        "invalidate",
        "flatten_time",
    }
    if lost:
        if session and session.last_loss_event:
            try:
                if event_ticker == next_event_ticker(session.last_loss_event):
                    # Second hour in a losing streak. Sit-out was the first
                    # hour after the first loss; do not skip a third.
                    return SessionMemory(
                        last_loss_event=event_ticker,
                        last_side=side,
                        last_play=play,
                        skip_next=False,
                    )
            except ValueError:
                pass
        return SessionMemory(
            last_loss_event=event_ticker, last_side=side, last_play=play, skip_next=True
        )
    return SessionMemory()


def _skip_hour(session: SessionMemory) -> str | None:
    if session.skipped_event:
        return session.skipped_event
    if not session.last_loss_event:
        return None
    try:
        return next_event_ticker(session.last_loss_event)
    except ValueError:
        return None


def refresh_session(session: SessionMemory | None, current_event: str | None) -> SessionMemory:
    """Skip waits on the hour after a loss. Paper rebuilds this from trades each scan,
    so the skip hour is the next ticker after last_loss_event — not whatever hour
    happens to be live the first time we notice the loss."""
    session = session or SessionMemory()
    if not session.skip_next or not current_event:
        return session
    if current_event == session.last_loss_event:
        return session
    skip_hour = _skip_hour(session)
    if skip_hour is None:
        return SessionMemory(
            last_loss_event=session.last_loss_event,
            last_side=session.last_side,
            last_play=session.last_play,
            skip_next=True,
            skipped_event=current_event,
        )
    if current_event == skip_hour:
        return SessionMemory(
            last_loss_event=session.last_loss_event,
            last_side=session.last_side,
            last_play=session.last_play,
            skip_next=True,
            skipped_event=skip_hour,
        )
    return SessionMemory()


def allow_swing(opportunity: Opportunity, memory: SwingMemory | None) -> bool:
    if opportunity.play not in T_PLAYS:
        return True
    if memory is None or (memory.ticker is None and not memory.dead):
        return True
    if memory.dead:
        if opportunity.play == "impulse_wait" and memory.play in {"impulse_t", "swing_t"}:
            return True
        return False
    # Live working fill/rest. Takers stay one ticker. A working coupon
    # must still allow nearby rungs — AUG2520 hung three. Engine caps at
    # 3 unique tickers. Clip-after-dead still blocks hop above.
    if opportunity.play == "impulse_wait" and (memory.play or "impulse_wait") == "impulse_wait":
        return True
    return opportunity.ticker == memory.ticker and opportunity.side != memory.side


def pick_dump_wait(
    waits: list[Opportunity], spot: SpotQuote, settings: Settings | None = None
) -> list[Opportunity]:
    """Up to three nearby rests. Only the live 32–42¢ coupon book.

    0.50–0.70 ATM mid is a lonely 0.25, not a pad. Far OTM pennies stay the knife.
    """
    chosen = list(waits)
    band = settings.impulse_wait_coupon_ask if settings is not None else 0.42
    chosen.sort(
        key=lambda row: (
            0 if row.ask <= band + 1e-12 else 1,
            abs((row.strike or 0.0) - spot.price),
            row.ask - row.limit_price,
            -row.ev,
        )
    )
    unique: list[Opportunity] = []
    seen: set[tuple[str, str]] = set()
    for row in chosen:
        key = (row.ticker, row.side)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) >= 3:
            break
    return unique


def _entry_play(item) -> str:
    return item["play"] if isinstance(item, dict) else item.play


def _entry_taker(item) -> bool:
    return bool(item["taker"] if isinstance(item, dict) else item.taker)


def pick_flex_entries(opps: list, *, working_plays: set[str] | None = None) -> list:
    """lock_hold first; dump coupon; impulse_t only if the taker flag is on.

    Paper AUG2609 12:39: T78099 ask 0.36 was the human rest, but takers[:1]
    ate T78299 @ 0.51 and t_stop. Same miss on the AUG2608 minute tape.
    """
    working_plays = working_plays or set()
    lock_takes = [row for row in opps if _entry_play(row) == "lock_hold" and _entry_taker(row)]
    dump_waits = [row for row in opps if _entry_play(row) == "impulse_wait"]
    impulse_takes = [
        row for row in opps if _entry_play(row) in {"impulse_t", "swing_t"} and _entry_taker(row)
    ]
    locks = [row for row in opps if _entry_play(row) == "lock_wait"]
    if "impulse_wait" in working_plays:
        impulse_takes = []
    if lock_takes:
        return lock_takes[:1]
    if dump_waits:
        return dump_waits[:3]
    if impulse_takes:
        return impulse_takes[:1]
    if locks:
        return locks[:3]
    if "impulse_wait" in working_plays:
        return []
    takers = [row for row in opps if _entry_taker(row)]
    return takers[:1] or list(opps)[:1]


def allow_session(opportunity: Opportunity, session: SessionMemory | None) -> bool:
    if opportunity.play not in T_PLAYS:
        return True
    if session is None:
        return True
    if session.last_loss_event and opportunity.event_ticker == session.last_loss_event:
        # After an isolated taker stop, a later dump coupon may still rest
        # this hour. Skip-wait is the next ticker. Paper AUG2614: 17:32 YES
        # impulse_t t_stop, 17:47 T78399 ask 0.40 journaled wait and this
        # same-hour gate ate the rest. After a coupon scratch/stop, or after
        # a skip-hour taker loss (skip_next already cleared), stay dead.
        if (
            opportunity.play == "impulse_wait"
            and session.skip_next
            and session.last_play in {"impulse_t", "swing_t"}
        ):
            return True
        return False
    if not session.skip_next:
        return True
    if session.skipped_event is None or opportunity.event_ticker == session.skipped_event:
        # Sit out every T play on the skip hour. Same-dir taker was the
        # live leak: AUG2605/2606 t_stop, AUG2610 t_clip. Coupon goal
        # does not need that sidecar.
        return False
    return True


def apply_swing_memory(
    opportunities: list[Opportunity],
    memories: dict[str, SwingMemory] | SwingMemory | None,
    session: SessionMemory | None = None,
) -> list[Opportunity]:
    if memories is None and session is None:
        return list(opportunities)
    out: list[Opportunity] = []
    for row in opportunities:
        if isinstance(memories, SwingMemory):
            memory = memories
        elif memories is None:
            memory = None
        else:
            memory = memories.get(row.event_ticker)
        if allow_swing(row, memory) and allow_session(row, session):
            out.append(row)
    return out


def scan_markets(markets: list[Market], spot: SpotQuote, settings: Settings, now: datetime | None = None) -> list[Opportunity]:
    locks: list[Opportunity] = []
    swings: list[Opportunity] = []
    hold: list[Opportunity] = []
    scalp: list[Opportunity] = []
    if settings.playbook in {"lock", "flex"}:
        for market in markets:
            locks.extend(evaluate_lock_market(market, spot, settings, now))
        locks.sort(key=lambda row: (row.taker, row.expected_roi, row.model_p, -row.seconds_left), reverse=True)
    if settings.playbook in {"swing", "flex"}:
        impulses: list[Opportunity] = []
        waits: list[Opportunity] = []
        for market in markets:
            impulses.extend(evaluate_impulse_market(market, spot, settings, now))
            if settings.playbook == "flex":
                waits.extend(evaluate_impulse_wait_market(market, spot, settings, now))
            if settings.playbook == "swing":
                swings.extend(evaluate_swing_market(market, spot, settings, now))
        impulses.sort(key=lambda row: (abs(spot.impulse), row.ev, -row.seconds_left), reverse=True)
        waits = pick_dump_wait(waits, spot, settings)
        swings.sort(key=lambda row: ((row.model_p - row.ask), row.ev, -row.seconds_left), reverse=True)
        swings = waits + impulses + swings
    if settings.playbook == "hold":
        for market in markets:
            hold.extend(evaluate_market(market, spot, settings, now))
        hold.sort(key=lambda row: (row.expected_roi, row.model_p, -row.seconds_left), reverse=True)
        return hold
    if settings.playbook == "scalp":
        for market in markets:
            scalp.extend(evaluate_scalp_market(market, spot, settings, now))
        scalp.sort(key=lambda row: ((row.model_p - row.ask), row.ev, -row.seconds_left), reverse=True)
        return scalp
    if settings.playbook == "lock":
        return locks
    if settings.playbook == "swing":
        return swings
    takes = [row for row in locks if row.taker] + swings
    waits = [row for row in locks if not row.taker]
    return takes + waits


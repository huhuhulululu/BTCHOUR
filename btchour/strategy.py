from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from btchour.config import Settings
from btchour.fees import fill_cost, lock_exit_price, max_entry_price
from btchour.kalshi import Market
from btchour.model import SpotQuote, digital_prob, effective_vol, sigma_cushion
from btchour.tickers import is_hourly_window

T_PLAYS = frozenset({"swing_t", "impulse_t"})


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


@dataclass
class SwingMemory:
    """Per-event 做T state: one ticker, one clip, then hands off. No flip."""

    ticker: str | None = None
    side: str | None = None
    dead: bool = False


@dataclass
class SessionMemory:
    """After a losing T, skip the next hourly event. Tired direction is the failure mode."""

    last_loss_event: str | None = None
    skip_next: bool = False
    skipped_event: str | None = None


def remember_swing_exit(
    memory: SwingMemory, ticker: str, side: str, reason: str, play: str = ""
) -> SwingMemory:
    return SwingMemory(ticker=ticker, side=side, dead=True)


def remember_session_exit(
    session: SessionMemory | None, event_ticker: str, reason: str, pnl: float | None
) -> SessionMemory:
    lost = (pnl is not None and pnl < 0) or reason in {"t_stop", "t_fade", "invalidate", "flatten_time"}
    if lost:
        return SessionMemory(last_loss_event=event_ticker, skip_next=True)
    return SessionMemory()


def refresh_session(session: SessionMemory | None, current_event: str | None) -> SessionMemory:
    session = session or SessionMemory()
    if not session.skip_next or not current_event:
        return session
    if current_event == session.last_loss_event:
        return session
    if session.skipped_event is None:
        return SessionMemory(
            last_loss_event=session.last_loss_event,
            skip_next=True,
            skipped_event=current_event,
        )
    if current_event != session.skipped_event:
        return SessionMemory()
    return session


def allow_swing(opportunity: Opportunity, memory: SwingMemory | None) -> bool:
    if opportunity.play not in T_PLAYS:
        return True
    if memory is None or (memory.ticker is None and not memory.dead):
        return True
    if memory.dead:
        return False
    return opportunity.ticker == memory.ticker and opportunity.side != memory.side


def allow_session(opportunity: Opportunity, session: SessionMemory | None) -> bool:
    if opportunity.play not in T_PLAYS:
        return True
    if session is None or not session.skip_next:
        return True
    if opportunity.event_ticker == session.last_loss_event:
        return False
    if session.skipped_event is None or opportunity.event_ticker == session.skipped_event:
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
        for market in markets:
            impulses.extend(evaluate_impulse_market(market, spot, settings, now))
            if settings.playbook == "swing":
                swings.extend(evaluate_swing_market(market, spot, settings, now))
        impulses.sort(key=lambda row: (abs(spot.impulse), row.ev, -row.seconds_left), reverse=True)
        swings.sort(key=lambda row: ((row.model_p - row.ask), row.ev, -row.seconds_left), reverse=True)
        swings = impulses + swings
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


"""15-minute broadcast tables. No charts — markdown tables only.

Score paper and true coupons on separate rows. Replay is not 达成.
Clocks are America/New_York. Storage stays UTC ISO.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from btchour.config import CATALOG_DIR, Settings, load_settings
from btchour.fees import round_trip_roi
from btchour.store import Store
from btchour.strategy import (
    _ask_at_rest,
    coupon_in_band,
    coupon_min_ask,
    coupon_rest_ready,
    coupon_sides,
    impulse_wait_flipped,
    impulse_wait_wrong_side,
)
from btchour.tickers import (
    ET,
    format_et,
    format_event_ticker,
    next_session_event_ticker,
    parse_event_ticker,
    parse_market_ticker,
)

TRUE_REST = 0.25
HOUR_WINDOW = 6


def parse_raw(row) -> dict:
    try:
        raw = json.loads(row["raw"] or "{}")
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def is_true_coupon(row, raw: dict | None = None) -> bool:
    raw = parse_raw(row) if raw is None else raw
    if raw.get("play") != "impulse_wait":
        return False
    rest = raw.get("rest")
    if rest is None:
        return False
    return abs(float(rest) - TRUE_REST) < 1e-9


def short_hour(event_ticker: str | None) -> str:
    if not event_ticker:
        return "—"
    try:
        close = parse_event_ticker(event_ticker)["close_et"]
    except ValueError:
        return event_ticker
    return close.strftime("%b%d%H").upper()


def short_strike(ticker: str | None) -> str:
    if not ticker:
        return "—"
    try:
        strike = parse_market_ticker(ticker)["strike"]
    except ValueError:
        if "-T" in ticker:
            return "T" + ticker.rsplit("-T", 1)[1]
        return ticker
    return f"T{int(strike)}"


def fmt_pnl(value: float | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    if abs(number) < 5e-5:
        return "0"
    sign = "+" if number > 0 else "−"
    return f"{sign}{abs(number):.4f}"


def fmt_px(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def fmt_impulse(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.0f}"


def fmt_roi(value: float | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    if abs(number) < 5e-4:
        return "0%"
    sign = "+" if number > 0 else "−"
    return f"{sign}{abs(number):.1%}"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        rows = [["—"] * len(headers)]
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def coupon_roi(row, raw: dict | None = None) -> float | None:
    raw = parse_raw(row) if raw is None else raw
    exit_price = raw.get("exit_price")
    if exit_price is None:
        return None
    cost = _row_get(row, "cost")
    count = _row_get(row, "count") or 1.0
    if cost is None:
        return None
    return round_trip_roi(float(cost), float(exit_price), float(count))


def result_label(result: str | None, status: str | None = None) -> str:
    if status == "working":
        return "挂着"
    if status == "open":
        return "持仓"
    if status == "cancelled":
        return "反手撤" if result == "wait_invalid" else (result or "撤")
    labels = {
        "t_clip": "clip",
        "t_scratch": "scratch",
        "t_wait_stop": "stop",
        "t_stop": "stop",
        "t_fade": "fade",
        "wait_invalid": "反手撤",
    }
    if result in labels:
        return labels[result]
    return result or (status or "—")


def recent_hour_tickers(current: str, count: int = HOUR_WINDOW) -> list[str]:
    tickers = [current]
    ticker = current
    for _ in range(count - 1):
        close = parse_event_ticker(ticker)["close_et"]
        ticker = format_event_ticker(close - timedelta(hours=1))
        tickers.append(ticker)
    return tickers


def market_strike(market: dict | None) -> float | None:
    if not market:
        return None
    for key in ("strike", "floor_strike", "cap_strike"):
        value = market.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    ticker = market.get("ticker")
    if not ticker:
        return None
    try:
        return float(parse_market_ticker(ticker)["strike"])
    except (ValueError, KeyError, TypeError):
        return None


def current_hour_markets(snapshot: dict | None, event_ticker: str) -> list[dict]:
    """All rungs of the next-hour KXBTCD book. That ladder is the work surface."""
    if not snapshot or not event_ticker:
        return []
    found: list[dict] = []
    seen: set[str] = set()

    def add(markets) -> None:
        for market in markets or []:
            ticker = market.get("ticker")
            event = market.get("event_ticker") or ""
            if event and event != event_ticker:
                continue
            if not event and ticker and not str(ticker).startswith(event_ticker):
                continue
            if ticker and ticker not in seen:
                seen.add(ticker)
                found.append(market)

    current = snapshot.get("current_hour") or {}
    current_event = (current.get("event") or {}).get("event_ticker")
    if current_event in {None, "", event_ticker}:
        add(current.get("markets"))
    for block in snapshot.get("tradable") or []:
        event = ((block.get("event") or {}).get("event_ticker")) or ""
        if event == event_ticker:
            add(block.get("markets"))
    if not found:
        add(snapshot.get("markets"))
    return found


def coupon_ask_live(side: str, ask: float | None, settings: Settings) -> bool:
    if ask is None:
        return False
    price = float(ask)
    if price <= settings.impulse_rest + 1e-12:
        return False
    if price + 1e-12 < coupon_min_ask(side, settings):
        return False
    return coupon_in_band(price, settings)


def ladder_posture(
    *,
    skip: bool,
    clipped: bool,
    resting: int,
    live_n: int,
    ready_n: int,
) -> str:
    if skip:
        return "skip小时"
    if clipped:
        return "已clip不hop"
    if resting:
        return f"挂着{resting}"
    if live_n > 0 and ready_n > 0:
        return "空仓·阶梯活着"
    if live_n > 0:
        return "空仓·带在边未到"
    return "空仓·带塌了"


def ladder_census(
    markets: list[dict],
    spot: float | None,
    impulse: float | None,
    settings: Settings,
    *,
    skip: bool = False,
    clipped: bool = False,
    resting: int = 0,
) -> dict:
    """Count the hourly ladder. Empty + live ATM band is a strategy miss."""
    reach = settings.impulse_wait_max_distance or settings.swing_max_distance
    move = None if impulse is None else float(impulse)
    yes_n = 0
    no_n = 0
    ready_n = 0
    atm_n = 0
    live: list[dict] = []
    for market in markets:
        strike = market_strike(market)
        dist = None
        if strike is not None and spot is not None:
            dist = abs(float(strike) - float(spot))
            if dist <= reach + 1e-9:
                atm_n += 1
        if dist is None or dist > reach + 1e-9:
            continue
        for side in ("yes", "no"):
            ask = side_ask(market, side)
            if not coupon_ask_live(side, ask, settings):
                continue
            ready = (
                move is not None
                and side in coupon_sides(move, settings)
                and coupon_rest_ready(side, move, settings)
            )
            if side == "yes":
                yes_n += 1
            else:
                no_n += 1
            if ready:
                ready_n += 1
            live.append(
                {
                    "strike": short_strike(market.get("ticker")),
                    "side": side.upper(),
                    "dist": dist,
                    "ask": ask,
                    "ready": ready,
                }
            )
    live.sort(key=lambda row: (row["dist"] if row["dist"] is not None else 1e18, row["ask"] or 1.0))
    live_n = yes_n + no_n
    return {
        "n": len(markets),
        "atm": atm_n,
        "yes": yes_n,
        "no": no_n,
        "ready": ready_n,
        "live": live_n,
        "posture": ladder_posture(
            skip=skip,
            clipped=clipped,
            resting=resting,
            live_n=live_n,
            ready_n=ready_n,
        ),
        "rungs": live[:8],
    }


def _index_markets(snapshot: dict | None) -> dict[str, dict]:
    if not snapshot:
        return {}
    found: dict[str, dict] = {}
    current = (snapshot.get("current_hour") or {}).get("markets") or []
    for market in current:
        ticker = market.get("ticker")
        if ticker:
            found[ticker] = market
    for block in snapshot.get("tradable") or []:
        for market in block.get("markets") or []:
            ticker = market.get("ticker")
            if ticker and ticker not in found:
                found[ticker] = market
    return found


def side_ask(market: dict | None, side: str) -> float | None:
    if not market:
        return None
    key = "yes_ask" if side == "yes" else "no_ask"
    value = market.get(key)
    return None if value is None else float(value)


def load_snapshot(path: Path | None = None) -> dict | None:
    target = path or (CATALOG_DIR / "snapshot" / "latest.json")
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text())
    except Exception:
        return None


def load_replay(path: Path | None = None) -> dict | None:
    target = path or (CATALOG_DIR / "snapshot" / "replay.json")
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text())
    except Exception:
        return None


def _last_journal(store: Store) -> dict | None:
    row = store.conn.execute("SELECT * FROM journal ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _clock(now: datetime) -> tuple[str, str]:
    local = now.astimezone(ET)
    return format_et(now), local.strftime("%H:%M %Z")


def _left_label(event_ticker: str, now: datetime) -> str:
    close = parse_event_ticker(event_ticker)["close_et"]
    seconds = (close - now.astimezone(ET)).total_seconds()
    if seconds <= 0:
        return "已收盘"
    minutes = int(seconds // 60)
    return f"{minutes}m"


def _same_dir(side: str, impulse: float | None, minimum: float) -> bool:
    if impulse is None:
        return False
    if side == "yes":
        return impulse >= minimum
    return impulse <= -minimum


def working_fill_label(
    side: str,
    rest: float | None,
    ask: float | None,
    impulse: float | None,
    settings: Settings,
    tape: float | None = None,
    live_one: bool = False,
) -> str:
    if impulse_wait_wrong_side(side, float(impulse or 0.0), settings):
        if impulse_wait_flipped(side, float(impulse or 0.0), settings):
            return "反手撤"
        return "错边撤"
    if live_one:
        return "等交易所"
    same = _same_dir(side, impulse, settings.impulse_min)
    ask_ok = (
        ask is not None
        and rest is not None
        and _ask_at_rest(float(ask), float(rest))
    )
    if same and ask_ok:
        if tape is not None and float(tape) <= 0:
            return "等成交"
        return "可成交"
    if not same and not ask_ok:
        return "等动量/ask"
    if not same:
        return "等动量"
    return "等ask"


def _lost_fills(fills: list[dict]) -> bool:
    for item in fills:
        pnl = item.get("pnl")
        if pnl is not None and float(pnl) < 0:
            return True
        if item.get("result") in {"t_stop", "t_wait_stop", "t_scratch", "t_fade"}:
            return True
    return False


def _hour_note(
    event: str,
    fills: list[dict],
    prev_fills: list[dict],
    current: str,
    now: datetime,
) -> tuple[str, str]:
    prev_lost = _lost_fills(prev_fills)
    if not fills:
        if prev_lost:
            return "skip小时", "0成交不叠"
        if event == current and now.astimezone(ET) < parse_event_ticker(event)["close_et"]:
            return "进行中", "—"
        return "0成交", "不叠skip"
    results = [fill["result"] for fill in fills]
    if results and all(item == "t_clip" for item in results):
        return "clip", "可做"
    if any(item in {"t_wait_stop", "t_stop", "t_scratch", "t_fade"} for item in results):
        return result_label(results[-1]), "skip下一小时"
    return result_label(results[-1]), "—"


def collect_board(
    store: Store,
    settings: Settings | None = None,
    now: datetime | None = None,
    snapshot: dict | None = None,
    replay: dict | None = None,
) -> dict:
    settings = settings or Settings()
    now = now or datetime.now(timezone.utc)
    snapshot = snapshot if snapshot is not None else load_snapshot()
    replay = replay if replay is not None else load_replay()
    trades = list(store.conn.execute("SELECT * FROM trades ORDER BY id"))
    journal = _last_journal(store)
    current = next_session_event_ticker(now)
    session = store.session_memory()
    books = _index_markets(snapshot)
    clock, clock_short = _clock(now)

    spot = None
    impulse = None
    if journal and journal.get("event_ticker") == current:
        spot = journal.get("spot")
        impulse = journal.get("impulse")
    if spot is None and snapshot:
        spot = (snapshot.get("spot") or {}).get("price")
        if impulse is None:
            impulse = (snapshot.get("spot") or {}).get("impulse")

    closed = [row for row in trades if _row_get(row, "status") in {"closed", "settled"}]
    paper_wins = sum(1 for row in closed if (_row_get(row, "pnl") or 0) > 0)
    paper_pnl = sum(float(_row_get(row, "pnl") or 0) for row in closed)
    true_closed = [row for row in closed if is_true_coupon(row)]
    true_wins = sum(1 for row in true_closed if (_row_get(row, "pnl") or 0) > 0)
    true_pnl = sum(float(_row_get(row, "pnl") or 0) for row in true_closed)
    old = [row for row in closed if not is_true_coupon(row)]
    old_wins = sum(1 for row in old if (_row_get(row, "pnl") or 0) > 0)
    old_pnl = sum(float(_row_get(row, "pnl") or 0) for row in old)

    working = [
        row
        for row in trades
        if _row_get(row, "status") in {"working", "open"}
        and _row_get(row, "event_ticker") == current
        and is_true_coupon(row)
    ]
    rests: list[dict] = []
    for row in working:
        raw = parse_raw(row)
        ticker = _row_get(row, "ticker")
        side = _row_get(row, "side")
        rest = raw.get("rest")
        ask = side_ask(books.get(ticker), side)
        rests.append(
            {
                "id": _row_get(row, "id"),
                "side": (side or "").upper(),
                "strike": short_strike(ticker),
                "rest": rest,
                "ask": ask,
                "fill": working_fill_label(
                    side,
                    rest,
                    ask,
                    impulse,
                    settings,
                    tape=raw.get("tape_at_rest"),
                    live_one=bool(raw.get("live_one")),
                ),
                "status": (
                    "实盘1张"
                    if raw.get("live_one") and _row_get(row, "status") == "working"
                    else result_label(raw.get("result") or _row_get(row, "result"), _row_get(row, "status"))
                ),
            }
        )

    hours = []
    fills_by_event: dict[str, list] = {}
    for row in true_closed:
        event = _row_get(row, "event_ticker")
        raw = parse_raw(row)
        fills_by_event.setdefault(event, []).append(
            {
                "id": _row_get(row, "id"),
                "result": _row_get(row, "result"),
                "pnl": _row_get(row, "pnl"),
            }
        )
    for event in recent_hour_tickers(current):
        fills = fills_by_event.get(event) or []
        prev = format_event_ticker(parse_event_ticker(event)["close_et"] - timedelta(hours=1))
        result, nxt = _hour_note(event, fills, fills_by_event.get(prev) or [], current, now)
        hours.append(
            {
                "hour": short_hour(event),
                "event": event,
                "fills": len(fills),
                "result": result,
                "pnl": sum(float(item["pnl"] or 0) for item in fills) if fills else None,
                "next": nxt,
            }
        )

    coupons = []
    for row in true_closed:
        raw = parse_raw(row)
        coupons.append(
            {
                "id": _row_get(row, "id"),
                "hour": short_hour(_row_get(row, "event_ticker")),
                "side": (_row_get(row, "side") or "").upper(),
                "strike": short_strike(_row_get(row, "ticker")),
                "result": result_label(_row_get(row, "result"), _row_get(row, "status")),
                "exit": raw.get("exit_price"),
                "roi": coupon_roi(row, raw),
                "pnl": _row_get(row, "pnl"),
            }
        )

    replay_row = None
    if replay:
        replay_row = {
            "hours": replay.get("hours"),
            "takes": replay.get("take_count"),
            "wins": replay.get("wins"),
            "pnl": replay.get("realized_pnl"),
        }

    hour_fills = fills_by_event.get(current) or []
    clipped = bool(hour_fills) and all(item.get("result") == "t_clip" for item in hour_fills)
    ladder = ladder_census(
        current_hour_markets(snapshot, current),
        None if spot is None else float(spot),
        None if impulse is None else float(impulse),
        settings,
        skip=session.skipped_event == current,
        clipped=clipped,
        resting=len(rests),
    )

    return {
        "clock": clock,
        "clock_short": clock_short,
        "mode": settings.mode,
        "playbook": settings.playbook,
        "event": current,
        "hour": short_hour(current),
        "left": _left_label(current, now),
        "spot": spot,
        "impulse": impulse,
        "skip": session.skipped_event == current,
        "paper": {"n": len(closed), "wins": paper_wins, "pnl": paper_pnl},
        "true": {"n": len(true_closed), "wins": true_wins, "pnl": true_pnl},
        "old_taker": {"n": len(old), "wins": old_wins, "pnl": old_pnl},
        "replay": replay_row,
        "slots": f"{len(rests)}/3",
        "rests": rests,
        "ladder": ladder,
        "hours": hours,
        "coupons": coupons,
    }


def render_board(payload: dict) -> str:
    tape = md_table(
        ["纽约时间", "小时盘", "距收盘", "模式", "现货", "动量", "skip"],
        [
            [
                payload["clock_short"],
                payload["hour"],
                payload["left"],
                payload.get("mode") or "paper",
                f"{payload['spot']:.0f}" if payload.get("spot") is not None else "—",
                fmt_impulse(payload.get("impulse")),
                "是" if payload.get("skip") else "否",
            ]
        ],
    )
    replay = payload.get("replay") or {}
    books = md_table(
        ["账", "完成", "赢", "pnl", "说明"],
        [
            [
                "纸盘全部",
                str(payload["paper"]["n"]),
                str(payload["paper"]["wins"]),
                fmt_pnl(payload["paper"]["pnl"]),
                "含旧 taker",
            ],
            [
                "真 coupon",
                str(payload["true"]["n"]),
                str(payload["true"]["wins"]),
                fmt_pnl(payload["true"]["pnl"]),
                "impulse_wait / rest 0.25",
            ],
            [
                "旧 taker",
                str(payload["old_taker"]["n"]),
                str(payload["old_taker"]["wins"]),
                fmt_pnl(payload["old_taker"]["pnl"]),
                "不算样本",
            ],
            [
                f"回放 {replay.get('hours') or 16}h",
                str(replay.get("takes") if replay.get("takes") is not None else "—"),
                str(replay.get("wins") if replay.get("wins") is not None else "—"),
                fmt_pnl(replay.get("pnl")),
                "不是达成",
            ],
        ],
    )
    ladder = payload.get("ladder") or {}
    ladder_table = md_table(
        ["整点档", "$600内", "YES 28–42", "NO 32–42", "可挂", "姿态"],
        [
            [
                str(ladder.get("n") if ladder.get("n") is not None else "—"),
                str(ladder.get("atm") if ladder.get("atm") is not None else "—"),
                str(ladder.get("yes") if ladder.get("yes") is not None else "—"),
                str(ladder.get("no") if ladder.get("no") is not None else "—"),
                str(ladder.get("ready") if ladder.get("ready") is not None else "—"),
                ladder.get("posture") or "—",
            ]
        ],
    )
    rung_rows = [
        [
            row["strike"],
            row["side"],
            f"{row['dist']:.0f}" if row.get("dist") is not None else "—",
            fmt_px(row.get("ask")),
            "是" if row.get("ready") else "否",
        ]
        for row in ladder.get("rungs") or []
    ]
    if not rung_rows:
        rung_rows = [["—", "—", "—", "—", "—"]]
    rungs = md_table(["档", "边", "距", "ask", "可挂"], rung_rows)
    rest_rows = [
        [
            str(row["id"]),
            row["side"],
            row["strike"],
            fmt_px(row.get("rest")),
            fmt_px(row.get("ask")),
            row["fill"],
            row["status"],
        ]
        for row in payload.get("rests") or []
    ]
    if not rest_rows:
        rest_rows = [["—", "—", "—", "—", "—", "空仓", "—"]]
    rests = md_table(
        ["id", "边", "档", "rest", "现ask", "成交", "状态"],
        rest_rows,
    )
    hours = md_table(
        ["小时", "真成交", "结果", "pnl", "下一小时"],
        [
            [
                row["hour"],
                str(row["fills"]),
                row["result"],
                fmt_pnl(row["pnl"]),
                row["next"],
            ]
            for row in payload.get("hours") or []
        ],
    )
    coupons = md_table(
        ["id", "小时", "边", "档", "结果", "出", "roi", "pnl"],
        [
            [
                str(row["id"]),
                row["hour"],
                row["side"],
                row["strike"],
                row["result"],
                fmt_px(row.get("exit")),
                fmt_roi(row.get("roi")),
                fmt_pnl(row["pnl"]),
            ]
            for row in payload.get("coupons") or []
        ],
    )
    title = (
        f"档位 {payload.get('slots') or '0/3'} · "
        f"{ladder.get('n', '—')}档 / $600内{ladder.get('atm', '—')} / "
        f"活档{ladder.get('live', '—')} · {payload.get('event')} · "
        f"成交要同向 |impulse|≥$100 且 ask==rest"
    )
    note = (
        "10%–50% 是兑现带，不是每笔保证。回放绿不是达成。默认 paper，不切 live。"
        " 空仓而阶梯活着是策略失败，不是没机会。"
    )
    return "\n\n".join(
        [
            f"**{payload['clock']}**  {title}",
            tape,
            f"本小时阶梯（工作盘，不是 15m 坐等）\n\n{ladder_table}\n\n{rungs}",
            books,
            f"本小时挂单（最多 3）\n\n{rests}",
            f"近几小时\n\n{hours}",
            f"真 coupon（旧 taker 不进这张表）\n\n{coupons}",
            note,
        ]
    )


def build_board(
    store: Store | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    snapshot: dict | None = None,
    replay: dict | None = None,
) -> tuple[dict, str]:
    payload = collect_board(
        store or Store(),
        settings or load_settings(),
        now=now,
        snapshot=snapshot,
        replay=replay,
    )
    return payload, render_board(payload)


def print_board(**kwargs) -> dict:
    payload, text = build_board(**kwargs)
    print(text)
    return payload

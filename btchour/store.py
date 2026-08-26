from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from btchour.config import DATA_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    count REAL NOT NULL,
    fee REAL NOT NULL,
    cost REAL NOT NULL,
    mode TEXT NOT NULL,
    taker INTEGER NOT NULL,
    model_p REAL NOT NULL,
    if_win_roi REAL NOT NULL,
    expected_roi REAL NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    pnl REAL,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_ticker TEXT,
    spot REAL,
    opportunity_count INTEGER,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_ticker TEXT,
    spot REAL,
    impulse REAL,
    tape_impulse REAL,
    status TEXT,
    reject TEXT
);
"""


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA_DIR / "btchour.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def record_scan(self, event_ticker: str | None, spot: float | None, opportunities: list[dict]) -> None:
        self.conn.execute(
            "INSERT INTO scans (created_at, event_ticker, spot, opportunity_count, payload) VALUES (?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                event_ticker,
                spot,
                len(opportunities),
                json.dumps(opportunities),
            ),
        )
        self.conn.commit()

    def tape_points(self, event_ticker: str | None = None, limit: int = 200) -> list[tuple[datetime, float]]:
        if event_ticker:
            rows = self.conn.execute(
                "SELECT created_at, spot FROM scans WHERE event_ticker = ? AND spot IS NOT NULL ORDER BY id DESC LIMIT ?",
                (event_ticker, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT created_at, spot FROM scans WHERE spot IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        points = []
        for row in reversed(rows):
            try:
                ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            except Exception:
                continue
            points.append((ts, float(row["spot"])))
        return points

    def record_journal(self, event_ticker: str | None, spot: float, impulse: float, tape_impulse: float, status: str, reject: str) -> None:
        self.conn.execute(
            "INSERT INTO journal (created_at, event_ticker, spot, impulse, tape_impulse, status, reject) VALUES (?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                event_ticker,
                spot,
                impulse,
                tape_impulse,
                status,
                reject,
            ),
        )
        self.conn.commit()

    def recent_journal(self, limit: int = 12) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)))

    def record_trade(self, trade: dict) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO trades (
                created_at, ticker, event_ticker, side, price, count, fee, cost, mode,
                taker, model_p, if_win_roi, expected_roi, status, result, pnl, raw
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.get("created_at") or datetime.now(timezone.utc).isoformat(),
                trade["ticker"],
                trade["event_ticker"],
                trade["side"],
                trade["price"],
                trade["count"],
                trade["fee"],
                trade["cost"],
                trade["mode"],
                1 if trade.get("taker") else 0,
                trade["model_p"],
                trade["if_win_roi"],
                trade["expected_roi"],
                trade.get("status") or "open",
                trade.get("result"),
                trade.get("pnl"),
                json.dumps(trade.get("raw") or {}),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def open_trades(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM trades WHERE status = 'open'"))

    def working_trades(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM trades WHERE status = 'working'"))

    def has_open(self, ticker: str, side: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM trades WHERE ticker = ? AND side = ? AND status IN ('open', 'working') LIMIT 1",
            (ticker, side),
        ).fetchone()
        return row is not None

    def promote_working(self, trade_id: int, price: float, fee: float, cost: float, if_win_roi: float) -> None:
        self.conn.execute(
            """
            UPDATE trades SET status = 'open', taker = 1, price = ?, fee = ?, cost = ?, if_win_roi = ?
            WHERE id = ? AND status = 'working'
            """,
            (price, fee, cost, if_win_roi, trade_id),
        )
        self.conn.commit()

    def update_raw(self, trade_id: int, raw: dict) -> None:
        self.conn.execute("UPDATE trades SET raw = ? WHERE id = ?", (json.dumps(raw), trade_id))
        self.conn.commit()

    def cancel_trade(self, trade_id: int, reason: str = "cancelled") -> None:
        self.conn.execute(
            "UPDATE trades SET status = 'cancelled', result = ? WHERE id = ?",
            (reason, trade_id),
        )
        self.conn.commit()

    def settle_trade(self, trade_id: int, result: str, pnl: float) -> None:
        self.conn.execute(
            "UPDATE trades SET status = 'settled', result = ?, pnl = ? WHERE id = ?",
            (result, pnl, trade_id),
        )
        self.conn.commit()

    def close_trade(self, trade_id: int, reason: str, pnl: float, raw: dict | None = None) -> None:
        if raw is None:
            self.conn.execute(
                "UPDATE trades SET status = 'closed', result = ?, pnl = ? WHERE id = ?",
                (reason, pnl, trade_id),
            )
        else:
            self.conn.execute(
                "UPDATE trades SET status = 'closed', result = ?, pnl = ?, raw = ? WHERE id = ?",
                (reason, pnl, json.dumps(raw), trade_id),
            )
        self.conn.commit()

    def swing_memories(self) -> dict:
        from btchour.strategy import SwingMemory, remember_swing_exit

        memories: dict = {}
        rows = self.conn.execute(
            "SELECT event_ticker, ticker, side, status, result, raw FROM trades ORDER BY id"
        ).fetchall()
        for row in rows:
            raw = {}
            try:
                raw = json.loads(row["raw"] or "{}")
            except Exception:
                raw = {}
            if raw.get("play") not in {"swing_t", "impulse_t"}:
                continue
            event = row["event_ticker"]
            current = memories.get(event) or SwingMemory()
            if row["status"] in {"closed", "settled"}:
                memories[event] = remember_swing_exit(
                    current, row["ticker"], row["side"], row["result"] or "", raw.get("play") or ""
                )
            elif row["status"] in {"open", "working"}:
                memories[event] = SwingMemory(ticker=row["ticker"], side=row["side"], dead=current.dead)
        return memories

    def session_memory(self):
        from btchour.strategy import SessionMemory, remember_session_exit

        mem = SessionMemory()
        rows = self.conn.execute(
            "SELECT event_ticker, status, result, pnl, raw FROM trades ORDER BY id"
        ).fetchall()
        for row in rows:
            raw = {}
            try:
                raw = json.loads(row["raw"] or "{}")
            except Exception:
                raw = {}
            if raw.get("play") not in {"swing_t", "impulse_t"}:
                continue
            if row["status"] in {"closed", "settled"}:
                mem = remember_session_exit(mem, row["event_ticker"], row["result"] or "", row["pnl"])
        return mem

    def summary(self) -> dict:
        open_n = self.conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'").fetchone()[0]
        settled = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS pnl, COALESCE(SUM(cost),0) AS cost FROM trades WHERE status = 'settled'"
        ).fetchone()
        closed = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS pnl, COALESCE(SUM(cost),0) AS cost FROM trades WHERE status = 'closed'"
        ).fetchone()
        wins = self.conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status IN ('settled', 'closed') AND pnl > 0"
        ).fetchone()[0]
        working_n = self.conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'working'").fetchone()[0]
        return {
            "open": open_n,
            "working": working_n,
            "settled": settled["n"],
            "closed": closed["n"],
            "completed": settled["n"] + closed["n"],
            "wins": wins,
            "realized_pnl": settled["pnl"] + closed["pnl"],
            "settled_cost": settled["cost"] + closed["cost"],
        }

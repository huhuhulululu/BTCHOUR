from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from btchour.config import DATA_DIR, Settings, load_settings
from btchour.kalshi import KalshiClient


def _money(value) -> float:
    return float(value or 0)


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _slim_fill(item: dict) -> dict:
    keep = (
        "created_time",
        "ticker",
        "action",
        "side",
        "outcome_side",
        "book_side",
        "is_taker",
        "count_fp",
        "yes_price_dollars",
        "no_price_dollars",
        "fee_cost",
    )
    return {key: item.get(key) for key in keep if key in item}


def _same_side_clips(fills: list[dict]) -> list[dict]:
    """FIFO same-side buy→sell clips only. Complementary hedges are not clips."""
    lots: dict[tuple[str, str], list[dict]] = defaultdict(list)
    clips: list[dict] = []
    for fill in sorted(fills, key=lambda row: row.get("created_time") or ""):
        ticker = fill.get("ticker") or ""
        side = (fill.get("side") or "").lower()
        action = (fill.get("action") or "").lower()
        qty = abs(_money(fill.get("count_fp")))
        px = _money(fill.get("yes_price_dollars") if side == "yes" else fill.get("no_price_dollars"))
        fee = _money(fill.get("fee_cost"))
        key = (ticker, side)
        rec = {
            "ts": fill.get("created_time"),
            "qty": qty,
            "px": px,
            "fee": fee,
            "taker": bool(fill.get("is_taker")),
        }
        if action == "buy":
            lots[key].append(rec)
            continue
        if action != "sell":
            continue
        remain = qty
        while remain > 1e-9 and lots[key]:
            lot = lots[key][0]
            take = min(remain, lot["qty"])
            fee_in = lot["fee"] * (take / lot["qty"]) if lot["qty"] else 0
            fee_out = fee * (take / qty) if qty else 0
            cost = take * lot["px"] + fee_in
            proceeds = take * px - fee_out
            pnl = proceeds - cost
            clips.append(
                {
                    "ticker": ticker,
                    "side": side,
                    "qty": take,
                    "buy": lot["px"],
                    "sell": px,
                    "buy_taker": lot["taker"],
                    "sell_taker": rec["taker"],
                    "open": lot["ts"],
                    "close": fill.get("created_time"),
                    "hold_s": (_ts(fill["created_time"]) - _ts(lot["ts"])).total_seconds()
                    if lot["ts"] and fill.get("created_time")
                    else 0,
                    "pnl": pnl,
                    "roi": (pnl / cost) if cost else 0,
                    "maker_round": (not lot["taker"]) and (not rec["taker"]),
                }
            )
            lot["qty"] -= take
            lot["fee"] -= fee_in
            remain -= take
            if lot["qty"] <= 1e-9:
                lots[key].pop(0)
    return clips


def summarize_fills(client: KalshiClient, settings: Settings | None = None, hours: int = 36) -> dict:
    settings = settings or load_settings()
    if not settings.can_sign:
        return {"error": "signed portfolio read needs a local key; nothing was written"}
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    fills = client.fills(min_ts=int(since.timestamp()))
    crypto = [
        row
        for row in fills
        if str(row.get("ticker") or "").startswith(("KXBTCD", "KXBTC15M", "KXBTC-"))
    ]
    clips = _same_side_clips(crypto)
    dest = DATA_DIR / "account-fills.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        __import__("json").dumps(
            {
                "pulled_at": datetime.now(timezone.utc).isoformat(),
                "hours": hours,
                "fill_count": len(fills),
                "crypto_fills": len(crypto),
                "fills": [_slim_fill(row) for row in crypto],
            },
            indent=2,
        )
        + "\n"
    )
    bands = Counter()
    for clip in clips:
        roi = clip["roi"]
        if roi >= 0.50:
            bands["ge50"] += 1
        elif roi >= 0.10:
            bands["10to50"] += 1
        elif roi >= 0:
            bands["0to10"] += 1
        else:
            bands["loss"] += 1
    return {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "hours": hours,
        "fill_count": len(fills),
        "crypto_fills": len(crypto),
        "same_side_clips": len(clips),
        "clip_wins": sum(1 for clip in clips if clip["pnl"] > 0),
        "clip_pnl": sum(clip["pnl"] for clip in clips),
        "roi_bands": dict(bands),
        "maker_round_clips": sum(1 for clip in clips if clip["maker_round"]),
        "wrote": str(Path("data") / dest.name),
        "note": "Same-side buy→sell only. 10–50% is the normal clip band; flips and strike hops are not.",
        "clips": clips[:24],
    }

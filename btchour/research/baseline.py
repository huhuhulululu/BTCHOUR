"""The decisive test: is our model better than the price we could have read?

Every entry in this repo is an assertion that `digital_prob` knows something
the book does not. That assertion has never been scored. This module scores
it, on the only thing that settles the argument -- what the rung actually paid.

The disagreement buckets are the point. A 20% EV gate only fires where the
model and the mid are 5c or more apart (`catalog/rules/ev.md`), so the honest
question is not "is the model calibrated on average" but "in the moments the
gate fires, which of the two is closer to the outcome". If the mid wins there,
no taker rule built on this model can work, whatever its thresholds.
"""

from __future__ import annotations

from btchour.config import Settings, load_settings
from btchour.model import digital_prob
from btchour.replay import EventTape, bars_from_tape

BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.00, 0.02, "0-2¢"),
    (0.02, 0.05, "2-5¢"),
    (0.05, 0.10, "5-10¢"),
    (0.10, 1.01, ">10¢"),
)


def _mid(quotes: dict) -> float | None:
    bid = quotes.get("yes_bid")
    ask = quotes.get("yes_ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def _brier(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def score_tapes(
    tapes: list[EventTape],
    settings: Settings | None = None,
    *,
    reach: float = 600.0,
    min_seconds: float = 180.0,
) -> dict:
    """Model vs mid on every near-ATM rung of every minute, against settlement.

    `min_seconds` drops the last few minutes, where the two agree anyway and a
    stale quote would dominate the count.
    """
    settings = settings or load_settings()
    model: list[tuple[float, float]] = []
    mid: list[tuple[float, float]] = []
    by_bucket: dict[str, dict] = {label: {"model": [], "mid": []} for _, _, label in BUCKETS}
    hours = 0
    for tape in tapes:
        bars = bars_from_tape(tape, settings)
        if not bars:
            continue
        hours += 1
        maturity = tape.maturity_ms / 1000.0
        for bar in bars:
            left = maturity - bar.end_ts
            if left < min_seconds:
                continue
            for strike, quotes in bar.quotes.items():
                if abs(strike - bar.spot) > reach:
                    continue
                result = tape.results.get(strike)
                if result not in {"yes", "no"}:
                    continue
                book = _mid(quotes)
                if book is None:
                    continue
                outcome = 1.0 if result == "yes" else 0.0
                p = digital_prob(bar.spot, strike, max(left, 1.0), bar.vol)
                model.append((p, outcome))
                mid.append((book, outcome))
                gap = abs(p - book)
                for lo, hi, label in BUCKETS:
                    if lo <= gap < hi:
                        by_bucket[label]["model"].append((p, outcome))
                        by_bucket[label]["mid"].append((book, outcome))
                        break
    rows = []
    for _, _, label in BUCKETS:
        pack = by_bucket[label]
        model_brier = _brier(pack["model"])
        mid_brier = _brier(pack["mid"])
        rows.append(
            {
                "disagreement": label,
                "observations": len(pack["model"]),
                "model_brier": model_brier,
                "mid_brier": mid_brier,
                "model_wins": (
                    None
                    if model_brier is None or mid_brier is None
                    else bool(model_brier < mid_brier)
                ),
            }
        )
    return {
        "experiment": "baseline",
        "hours": hours,
        "observations": len(model),
        "model_brier": _brier(model),
        "mid_brier": _brier(mid),
        "model_beats_mid": (
            None
            if not model
            else bool((_brier(model) or 1.0) < (_brier(mid) or 0.0))
        ),
        "buckets": rows,
    }


def render(report: dict) -> str:
    lines = [
        f"模型 vs 市场中价（{report['hours']} 小时 / {report['observations']} 个近 ATM 读数）",
        "",
        f"全样本 Brier：模型 {report['model_brier']:.5f}   中价 {report['mid_brier']:.5f}"
        if report["observations"]
        else "没有可评分的读数",
        "",
        f"{'分歧':<10}{'读数':>9}{'模型 Brier':>13}{'中价 Brier':>13}{'谁更准':>9}",
    ]
    for row in report["buckets"]:
        if not row["observations"]:
            lines.append(f"{row['disagreement']:<10}{0:>9}{'—':>13}{'—':>13}{'—':>9}")
            continue
        lines.append(
            f"{row['disagreement']:<10}{row['observations']:>9}"
            f"{row['model_brier']:>13.5f}{row['mid_brier']:>13.5f}"
            f"{('模型' if row['model_wins'] else '中价'):>9}"
        )
    lines.append("")
    lines.append(
        "20% 门只在分歧 ≥5¢ 时开火。那两行里中价更准，就说明门开的是模型误差，"
        "不是市场错价 —— 任何 taker 类提案到此为止。"
    )
    return "\n".join(lines)

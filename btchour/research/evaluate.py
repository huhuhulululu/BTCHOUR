"""Run a settings variant over a set of tapes and score it honestly.

Three habits are wired in on purpose:

1. every run reports a bootstrap interval and a t-stat, so a mean that rests
   on four trades cannot read like a result;
2. every comparison is split in time -- tune on the first half, report the
   second -- so a variant that only fits the sample it was picked on is
   visible as the gap between the two;
3. maker fills carry the model read at the hang *and* at the fill, so the
   cost of resting under the book is a measured number.
"""

from __future__ import annotations

from dataclasses import dataclass

from btchour.config import Settings, apply_playbook
from btchour.fees import fill_cost
from btchour.replay import EventTape, replay_tapes
from btchour.research.metrics import bootstrap_ci, summarize_takes


def collect_takes(summary: dict) -> list[dict]:
    return [take for event in summary.get("events") or [] for take in event.get("takes") or []]


def maker_edge_realized(takes: list[dict], rest: float) -> dict:
    """The same question as `maker_edge_at_fill`, asked of the outcome.

    `maker_edge_at_fill` scores a fill against `fill_model_p`. On a synthetic
    tape that is sound, because the book there reports fair value by
    construction. On a real tape it is not: 017 measured `digital_prob` losing
    to the book on 92 hours, and the miss is one-sided -- the 0.55 vol floor
    drags p toward 0.5, so on the cheap side a rest fills on, the model reads
    HIGH. Pooled over the 0.20-0.35 mid band the model says 0.347 where
    settlement says 0.261, which is +0.086 of pure optimism. A gate built on
    it passes trades that lose money.

    So score the fill against what the rung paid. No model, no exit rule: just
    the settled value of the side we bought, less what the rest cost.
    """
    rows = [t for t in takes if t.get("settle_value") is not None]
    if not rows:
        return {"fills": 0}
    paid = fill_cost(rest, 1.0, taker=False).cost
    edges = [float(t["settle_value"]) - paid for t in rows]
    wins = sum(1 for t in rows if float(t["settle_value"]) > 0.5)
    return {
        "fills": len(rows),
        "rest": rest,
        "paid": paid,
        "settle_rate": wins / len(rows),
        "edge": sum(edges) / len(edges),
        "edge_ci": bootstrap_ci(edges),
    }


def maker_edge_at_fill(takes: list[dict], rest: float) -> dict:
    """What a maker rest was worth the instant it filled.

    `fill_model_p` is the model's probability for the side we bought, read on
    the bar that filled us. `paid` is the rest plus its fee. When the average
    `fill_model_p - paid` is negative the rule is buying above fair *at the
    moment of the fill*: no signal can repair that, because it is the fill
    rule itself selecting the bad half of the distribution.
    """
    rows = [t for t in takes if t.get("fill_model_p") is not None]
    if not rows:
        return {"fills": 0}
    paid = fill_cost(rest, 1.0, taker=False).cost
    edges = [float(t["fill_model_p"]) - paid for t in rows]
    walked = [
        float(t["ask"]) - rest
        for t in rows
        if t.get("ask") is not None
    ]
    signal_to_fill = [
        float(t["fill_model_p"]) - float(t["model_p"])
        for t in rows
        if t.get("model_p") is not None
    ]
    return {
        "fills": len(rows),
        "paid": round(paid, 4),
        "mean_p_at_fill": round(sum(float(t["fill_model_p"]) for t in rows) / len(rows), 4),
        "mean_edge_at_fill": round(sum(edges) / len(edges), 4),
        "mean_p_drift_hang_to_fill": (
            round(sum(signal_to_fill) / len(signal_to_fill), 4) if signal_to_fill else None
        ),
        "mean_ask_seen_minus_rest": (
            round(sum(walked) / len(walked), 4) if walked else None
        ),
    }


@dataclass(frozen=True)
class Variant:
    name: str
    playbook: str = "flex"
    extras: dict | None = None

    def settings(self, base: Settings) -> Settings:
        extras = dict(self.extras or {})
        return apply_playbook(
            base,
            self.playbook,
            skip_after_loss=extras.get("skip_after_loss"),
            extras=extras,
        )


def run_variant(
    tapes: list[EventTape],
    variant: Variant,
    base: Settings,
    *,
    keep_takes: bool = False,
) -> dict:
    cfg = variant.settings(base)
    summary = replay_tapes(tapes, cfg, write=False)
    takes = collect_takes(summary)
    stats = summarize_takes(takes, len(tapes))
    row = {
        "name": variant.name,
        "playbook": cfg.playbook,
        **stats.as_dict(),
        "maker_edge_at_fill": maker_edge_at_fill(takes, cfg.impulse_rest),
        "maker_edge_realized": maker_edge_realized(takes, cfg.impulse_rest),
    }
    if keep_takes:
        row["takes"] = takes
    return row


def pool_runs(name: str, rows: list[dict], hours: int) -> dict:
    """Merge independent seeds into one sample. Seeds are draws, not results."""
    takes = [take for row in rows for take in row.get("takes") or []]
    stats = summarize_takes(takes, hours)
    return {"name": name, **stats.as_dict(), "takes": takes}


def split_tapes(tapes: list[EventTape], train_fraction: float = 0.5) -> tuple[list, list]:
    """Split in time. `tapes` arrives newest first, so the tail is the past.

    Train on the older half, report on the newer half. A rule tuned on the
    whole sample has no out-of-sample left to fail in.
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be between 0 and 1")
    chronological = list(reversed(tapes))
    cut = max(1, int(len(chronological) * train_fraction))
    train = list(reversed(chronological[:cut]))
    test = list(reversed(chronological[cut:]))
    return train, test


def compare(
    tapes: list[EventTape],
    variants: list[Variant],
    base: Settings,
    *,
    train_fraction: float | None = 0.5,
) -> dict:
    payload: dict = {"hours": len(tapes), "variants": []}
    if train_fraction is None:
        for variant in variants:
            payload["variants"].append({"all": run_variant(tapes, variant, base)})
        return payload
    train, test = split_tapes(tapes, train_fraction)
    payload["train_hours"] = len(train)
    payload["test_hours"] = len(test)
    for variant in variants:
        payload["variants"].append(
            {
                "name": variant.name,
                "train": run_variant(train, variant, base),
                "test": run_variant(test, variant, base),
            }
        )
    return payload


def format_runs(rows: list[dict]) -> str:
    """Plain table. The board is Chinese and tabular; research output matches."""
    head = (
        f"{'变体':<22}{'笔数':>5}{'每小时':>7}{'胜率':>7}"
        f"{'总盈亏':>10}{'每笔':>9}{'95%区间':>20}{'t':>7}{'最大回撤':>10}"
    )
    lines = [head, "-" * len(head)]
    for row in rows:
        lo, hi = row["pnl_ci95"]
        lines.append(
            f"{row['name']:<22}{row['trades']:>5}{row['trades_per_hour']:>7.2f}"
            f"{row['win_rate']:>7.0%}{row['total_pnl']:>10.2f}{row['pnl_per_trade']:>9.3f}"
            f"{f'[{lo:+.3f}, {hi:+.3f}]':>20}{row['t_stat']:>7.2f}{row['max_drawdown']:>10.2f}"
        )
    return "\n".join(lines)

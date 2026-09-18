"""Named experiments. Each one answers a question that has a wrong answer.

Run them with `python3 -m btchour research <name>`. Every number below comes
out of the same replay engine the paper loop uses, so a result here is a
statement about the engine, not about a notebook.
"""

from __future__ import annotations

from btchour.config import Settings, load_settings
from btchour.model import TWAP_SECONDS, digital_prob
from btchour.replay import bars_from_tape
from btchour.research.evaluate import Variant, format_runs, maker_edge_at_fill, pool_runs, run_variant
from btchour.research.sim import REGIMES, regime_config, simulate_tapes


def _pooled(
    name: str,
    regime: str,
    extras: dict,
    *,
    hours: int,
    seeds: range,
    base: Settings,
) -> dict:
    rows = []
    for seed in seeds:
        tapes = simulate_tapes(hours, regime_config(regime, seed=seed))
        rows.append(run_variant(tapes, Variant(name, extras=extras), base, keep_takes=True))
    pooled = pool_runs(name, rows, hours * len(seeds))
    pooled["maker_edge_at_fill"] = maker_edge_at_fill(
        pooled["takes"], extras.get("impulse_rest", base.impulse_rest)
    )
    return pooled


def fill_model_experiment(
    *,
    hours: int = 80,
    seeds: range = range(1, 9),
    base: Settings | None = None,
) -> dict:
    """Does the replay's maker fill invent money that the book never offered?

    A rest filled at the minute's best wick and then marked at that minute's
    close books the whole intra-minute swing as profit. `fair` is the control:
    the book quotes true value plus a spread, so *nothing* should make money
    there. Anything the `wick` row earns in `fair` is the backtester paying
    itself.
    """
    base = base or load_settings()
    report: dict = {"experiment": "fill_model", "hours": hours * len(seeds), "rows": []}
    for regime in ("fair", "underreact", "overreact"):
        for wick in (True, False):
            label = f"{regime}/{'wick' if wick else 'close'}"
            row = _pooled(
                label,
                regime,
                {"replay_wick_fill": wick},
                hours=hours,
                seeds=seeds,
                base=base,
            )
            row.pop("takes", None)
            row["regime"] = regime
            row["wick_fill"] = wick
            report["rows"].append(row)
    return report


def regime_experiment(
    *,
    hours: int = 80,
    seeds: range = range(1, 9),
    base: Settings | None = None,
) -> dict:
    """With an honest fill, can `flex` find the edge when the edge is real?

    `underreact` is a world where the book lags the 3-minute move, so
    following the impulse is genuinely +EV; `overreact` is the mirror. A rule
    that cannot earn in the world built for it is broken at the mechanism, and
    more data will not change that.
    """
    base = base or load_settings()
    report: dict = {"experiment": "regime", "hours": hours * len(seeds), "rows": []}
    for regime in REGIMES:
        row = _pooled(
            regime,
            regime,
            {"replay_wick_fill": False},
            hours=hours,
            seeds=seeds,
            base=base,
        )
        row.pop("takes", None)
        report["rows"].append(row)
    return report


def calibration_experiment(
    *,
    hours: int = 200,
    seeds: range = range(1, 4),
    reach: float = 600.0,
    base: Settings | None = None,
) -> dict:
    """Which settlement model actually predicts the settlement?

    No book is involved. Every rung within `reach` of spot, on every minute of
    every simulated hour, is scored against what that rung really settled at.
    The old model floors the clock at the full 60-second TWAP window, so with
    a minute to go it still calls a decided contract 80/20. The corrected one
    charges the averaging: the last minute carries a third of its seconds of
    variance, and a half-realized window carries almost none.

    Brier is the mean squared error of a probability. Lower is better; 0.25 is
    what answering 0.5 to everything scores.
    """
    base = base or load_settings()
    scored: dict[str, list[float]] = {"twap": [], "legacy": []}
    buckets: dict[int, list[float]] = {}
    late: dict[str, list[float]] = {"twap": [], "legacy": []}
    for seed in seeds:
        for tape in simulate_tapes(hours, regime_config("fair", seed=seed)):
            bars = bars_from_tape(tape, base)
            maturity = tape.maturity_ms / 1000.0
            for bar in bars:
                left = max(maturity - bar.end_ts, 1.0)
                for strike in bar.quotes:
                    if abs(strike - bar.spot) > reach:
                        continue
                    outcome = 1.0 if tape.results.get(strike) == "yes" else 0.0
                    twap = digital_prob(bar.spot, strike, left, bar.vol)
                    legacy = digital_prob(bar.spot, strike, left, bar.vol, twap_seconds=0.0)
                    scored["twap"].append((twap - outcome) ** 2)
                    scored["legacy"].append((legacy - outcome) ** 2)
                    if left <= 300.0:
                        late["twap"].append((twap - outcome) ** 2)
                        late["legacy"].append((legacy - outcome) ** 2)
                    buckets.setdefault(min(9, int(twap * 10)), []).append(outcome)
    rows = [
        {
            "model": name,
            "observations": len(errs),
            "brier": (sum(errs) / len(errs)) if errs else None,
            "brier_last_5min": (sum(late[name]) / len(late[name])) if late[name] else None,
        }
        for name, errs in scored.items()
    ]
    reliability = {
        f"{k / 10:.1f}-{(k + 1) / 10:.1f}": {"n": len(v), "realized": round(sum(v) / len(v), 4)}
        for k, v in sorted(buckets.items())
    }
    return {
        "experiment": "calibration",
        "hours": hours * len(seeds),
        "twap_seconds": TWAP_SECONDS,
        "rows": rows,
        "reliability_twap": reliability,
    }


EXPERIMENTS: dict = {
    "fill-model": fill_model_experiment,
    "regime": regime_experiment,
    "calibration": calibration_experiment,
}


def render(report: dict) -> str:
    lines = [f"实验 {report['experiment']}（合成盘 {report['hours']} 小时）", ""]
    lines.append(format_runs(report["rows"]))
    lines.append("")
    lines.append(f"{'变体':<22}{'成交笔':>7}{'成交时模型 p':>14}{'付出':>8}{'成交时 edge':>13}")
    for row in report["rows"]:
        maker = row.get("maker_edge_at_fill") or {}
        if not maker.get("fills"):
            continue
        lines.append(
            f"{row['name']:<22}{maker['fills']:>7}{maker['mean_p_at_fill']:>14.3f}"
            f"{maker['paid']:>8.3f}{maker['mean_edge_at_fill']:>+13.3f}"
        )
    return "\n".join(lines)

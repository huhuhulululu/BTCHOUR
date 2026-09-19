"""Do the published equity anomalies still pay, and where does the money sit?

Decision 021 ranked H4 (US cross-section) last because its edge story is empty:
anything we found would have to survive "what is left after Fama-French five
plus momentum", which is decision 017's criterion wearing a different hat. The
egress allowlist then took H1 and H2 off the table, so this became the one
mechanism with data. That turns out to be lucky rather than a consolation,
because H2's whole premise is harvesting the anomaly literature, and the crypto
anomalies are translations of these equity originals. If the originals stopped
paying once they were published, a crypto backtest reporting 44%/yr out of
sample is far more likely to be the same overfit than a new discovery.

Two questions, both answerable from Kenneth French's library alone:

1. **Post-publication decay.** Every anomaly here was discovered on a sample
   that ended before its paper appeared. Split each factor at its own original
   sample end and its own publication date, and the three windows are
   in-sample, out-of-sample-but-unpublished, and post-publication. Mkt-RF is
   the control: it is compensation for bearing risk nobody can diversify away,
   so it has no reason to decay, and if it decays too then the split is picking
   up a regime and not publication.

2. **Where the surviving premium lives.** A long-short spread averaged over all
   stocks can be entirely produced inside the smallest size bucket, which is
   also where the quoted spread is widest and the capacity lowest. The 5x5
   sorts let us measure the spread separately inside each size quintile, and
   the firm-count and average-market-cap blocks let us say what share of total
   market cap that quintile holds. That is the cost-and-capacity layer decision
   021 named as the one thing this repo has actually demonstrated it can do.

The discipline carried over from 016-020:

- **Resample by calendar year, not by month.** Twelve months of one year share
  a macro regime the same way the rungs of one Kalshi hour share a BTC path.
  Resampling months gives an interval that is too narrow for anything the
  regime decides.
- **Look at the harvest shape.** A premium delivered by the best 5% of months
  is a different object from one delivered evenly, and only the second is
  something a live book collects.
- **Re-run on a non-overlapping window.** Each window is split in half and both
  halves reported. A sign that flips across halves is noise, not structure.
- **No fill model.** These are published portfolio returns; nothing here
  invents a fill, assumes queue position, or scores a model against itself.

The cost verdict is stated as a **breakeven round-trip cost**: the premium is
the budget, so given a turnover rate the arithmetic returns the cost at which
the premium is exactly consumed. That number is derived entirely from the
measured returns and the turnover axis, so it needs no quoted spread we cannot
verify -- the unverifiable input is moved onto a visible axis instead of into
the conclusion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

from btchour.research.frenchlib import Section, load, section_by_title
from btchour.research.metrics import _mean, _stdev, cluster_ci, t_stat

MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class Anomaly:
    """One factor, with the dates that define its out-of-sample windows."""

    key: str
    label: str
    paper: str
    sample_end: str  # YYYYMM, last month of the original paper's sample
    published: str  # YYYYMM the result became public
    source: str
    is_control: bool = False


# Sample ends and publication dates are the originating papers', not Fama and
# French's later repackaging: the question is when the market could have learned
# the result, and for HML that is Fama-French (1992), not the 2015 five-factor
# paper. CMA is the one judgement call, since the investment effect has two
# plausible origins; `CMA_ALTERNATE` re-runs it on the later pair so the
# conclusion can be checked against that choice.
ANOMALIES = (
    Anomaly(
        key="Mkt-RF",
        label="市场",
        paper="control: equity risk premium, not an anomaly",
        sample_end="199012",
        published="199212",
        source="split borrowed from HML so the control shares its windows",
        is_control=True,
    ),
    Anomaly(
        key="SMB",
        label="规模",
        paper="Banz (1981), JFE",
        sample_end="197512",
        published="198103",
        source="Banz sample 1936-1975; published March 1981",
    ),
    Anomaly(
        key="HML",
        label="价值",
        paper="Fama & French (1992), JF",
        sample_end="199012",
        published="199206",
        source="FF92 sample 1963-1990; published June 1992",
    ),
    Anomaly(
        key="Mom",
        label="动量",
        paper="Jegadeesh & Titman (1993), JF",
        sample_end="198912",
        published="199303",
        source="JT93 sample 1965-1989; published March 1993",
    ),
    Anomaly(
        key="RMW",
        label="盈利",
        paper="Novy-Marx (2013), JFE",
        sample_end="201012",
        published="201306",
        source="Novy-Marx sample 1963-2010; published 2013",
    ),
    Anomaly(
        key="CMA",
        label="投资",
        paper="Titman, Wei & Xie (2004), JFQA",
        sample_end="199612",
        published="200403",
        source="TWX sample 1973-1996; published March 2004",
    ),
)

CMA_ALTERNATE = Anomaly(
    key="CMA",
    label="投资(备选断点)",
    paper="Cooper, Gulen & Schill (2008), JF",
    sample_end="200312",
    published="200808",
    source="asset-growth reading of the same factor; sample to 2003, published 2008",
)


def _year(period: str) -> str:
    return period[:4]


def _window(series: dict[str, float], start: str | None, end: str | None) -> dict[str, float]:
    """Inclusive on both ends; `None` means unbounded."""
    return {
        p: v
        for p, v in series.items()
        if (start is None or p >= start) and (end is None or p <= end)
    }


def _next_month(period: str) -> str:
    year, month = int(period[:4]), int(period[4:])
    return f"{year + 1:04d}01" if month == 12 else f"{year:04d}{month + 1:02d}"


def by_year(series: dict[str, float]) -> list[list[float]]:
    """Group monthly observations into calendar-year blocks for the bootstrap."""
    groups: dict[str, list[float]] = {}
    for period, value in sorted(series.items()):
        groups.setdefault(_year(period), []).append(value)
    return [groups[k] for k in sorted(groups)]


def harvest_share(values: list[float], *, top_fraction: float = 0.05) -> float:
    """Share of the total sum delivered by the best `top_fraction` of months.

    A premium of 0.30%/month that arrives evenly is a book. The same 0.30%
    delivered by three months in forty years is a lottery ticket that happened
    to pay. Returns 0.0 when the total is not positive, because the statistic
    is only meaningful for a premium that exists.
    """
    if not values:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    k = max(1, int(round(len(values) * top_fraction)))
    top = sorted(values, reverse=True)[:k]
    return sum(top) / total


@dataclass(frozen=True)
class WindowStat:
    name: str
    start: str
    end: str
    months: int
    mean_pct: float
    annual_pct: float
    t_stat: float
    ci95: tuple[float, float]
    positive_years: int
    years: int
    harvest_top5_share: float
    half1_mean_pct: float
    half2_mean_pct: float

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def sign_stable(self) -> bool:
        """Both non-overlapping halves agree with the full-window sign."""
        if self.mean_pct == 0:
            return False
        return (
            math.copysign(1, self.half1_mean_pct)
            == math.copysign(1, self.half2_mean_pct)
            == math.copysign(1, self.mean_pct)
        )


def window_stat(name: str, series: dict[str, float]) -> WindowStat:
    periods = sorted(series)
    values = [series[p] for p in periods]
    if not values:
        return WindowStat(name, "", "", 0, 0.0, 0.0, 0.0, (0.0, 0.0), 0, 0, 0.0, 0.0, 0.0)
    groups = by_year(series)
    half = len(values) // 2
    first, second = values[:half], values[half:]
    return WindowStat(
        name=name,
        start=periods[0],
        end=periods[-1],
        months=len(values),
        mean_pct=_mean(values),
        annual_pct=_mean(values) * MONTHS_PER_YEAR,
        t_stat=t_stat(values),
        # Years, not months: the block is the unit inside which the observations
        # are not independent.
        ci95=cluster_ci(groups),
        positive_years=sum(1 for g in groups if sum(g) > 0),
        years=len(groups),
        harvest_top5_share=harvest_share(values),
        half1_mean_pct=_mean(first),
        half2_mean_pct=_mean(second),
    )


@dataclass(frozen=True)
class DecayReport:
    key: str
    label: str
    paper: str
    source: str
    is_control: bool
    in_sample: WindowStat
    unpublished: WindowStat
    post_pub: WindowStat

    def as_dict(self) -> dict:
        out = asdict(self)
        out["decay_ratio"] = self.decay_ratio
        out["survives"] = self.survives
        return out

    @property
    def decay_ratio(self) -> float | None:
        """Post-publication mean over in-sample mean. 0 means it fully died."""
        if self.in_sample.mean_pct <= 0:
            return None
        return self.post_pub.mean_pct / self.in_sample.mean_pct

    @property
    def survives(self) -> bool:
        """Post-publication premium is positive with an interval clear of zero."""
        lo, _ = self.post_pub.ci95
        return self.post_pub.mean_pct > 0 and lo > 0


def decay(series: dict[str, float], anomaly: Anomaly) -> DecayReport:
    """Split one factor into its three windows and score each."""
    return DecayReport(
        key=anomaly.key,
        label=anomaly.label,
        paper=anomaly.paper,
        source=anomaly.source,
        is_control=anomaly.is_control,
        in_sample=window_stat("样本内", _window(series, None, anomaly.sample_end)),
        unpublished=window_stat(
            "样本外未发表",
            _window(series, _next_month(anomaly.sample_end), anomaly.published),
        ),
        post_pub=window_stat("发表后", _window(series, _next_month(anomaly.published), None)),
    )


# ---------------------------------------------------------------- size layer


@dataclass(frozen=True)
class SizeBucket:
    quintile: int
    label: str
    long_leg: str
    short_leg: str
    stat: WindowStat
    firms: float
    mean_cap_musd: float
    cap_share: float

    def as_dict(self) -> dict:
        return asdict(self)


def aggregate_cap(firms: Section, caps: Section, period: str) -> dict[str, float]:
    """Total market cap per portfolio: firm count times average firm size."""
    n = firms.rows.get(period)
    c = caps.rows.get(period)
    if n is None or c is None:
        raise KeyError(f"no firm count / market cap row for {period}")
    out = {}
    for i, column in enumerate(firms.columns):
        count, cap = n[i], c[i]
        out[column] = 0.0 if count is None or cap is None else count * cap
    return out


def size_spread(
    path: str,
    *,
    low: str,
    high: str,
    start: str | None,
    end: str | None,
    weighting: str = "Average Value Weighted Returns -- Monthly",
) -> list[SizeBucket]:
    """The long-short spread measured separately inside each size quintile.

    `low` and `high` are the sort-variable suffixes -- `BM1`/`BM5` for value,
    `PRIOR1`/`PRIOR5` for momentum. The extreme corners of the grid carry the
    library's own names (`SMALL LoBM`, `BIG HiBM`), so both spellings are tried.

    The capacity columns are read at the last month of the window, which is the
    state of the market an entrant would face today rather than an average over
    a century of very different markets.
    """
    sections = load(path)
    returns = section_by_title(sections, weighting).monthly()
    firms = section_by_title(sections, "Number of Firms").monthly()
    caps = section_by_title(sections, "Average Market Cap").monthly()

    periods = sorted(_window(dict.fromkeys(returns.rows, 0.0), start, end))
    if not periods:
        raise ValueError("window selects no months")
    cap_period = max(p for p in periods if p in firms.rows and p in caps.rows)
    cap_by_portfolio = aggregate_cap(firms, caps, cap_period)
    grand_total = sum(cap_by_portfolio.values())

    def column_for(quintile: int, bucket: str) -> str:
        candidates = [f"ME{quintile} {bucket}"]
        tag = bucket.rstrip("12345")
        rank = bucket[len(tag):]
        if quintile == 1:
            candidates.append(f"SMALL {'Lo' if rank == '1' else 'Hi'}{tag}")
        if quintile == 5:
            candidates.append(f"BIG {'Lo' if rank == '1' else 'Hi'}{tag}")
        for candidate in candidates:
            if candidate in returns.columns:
                return candidate
        raise KeyError(f"none of {candidates} in {returns.columns}")

    buckets: list[SizeBucket] = []
    for quintile in range(1, 6):
        long_col = column_for(quintile, high)
        short_col = column_for(quintile, low)
        long_series = _window(returns.series(long_col), start, end)
        short_series = _window(returns.series(short_col), start, end)
        shared = sorted(set(long_series) & set(short_series))
        spread = {p: long_series[p] - short_series[p] for p in shared}

        row_firms = 0.0
        row_cap = 0.0
        for column in returns.columns:
            if column in (f"ME{quintile} ",):
                continue
            if _quintile_of(column) == quintile:
                row_cap += cap_by_portfolio.get(column, 0.0)
                idx = firms.columns.index(column)
                count = firms.rows[cap_period][idx]
                row_firms += 0.0 if count is None else count
        buckets.append(
            SizeBucket(
                quintile=quintile,
                label={1: "最小", 2: "小", 3: "中", 4: "大", 5: "最大"}[quintile],
                long_leg=long_col,
                short_leg=short_col,
                stat=window_stat(f"ME{quintile}", spread),
                firms=row_firms,
                mean_cap_musd=(row_cap / row_firms) if row_firms else 0.0,
                cap_share=(row_cap / grand_total) if grand_total else 0.0,
            )
        )
    return buckets


def _quintile_of(column: str) -> int:
    if column.startswith("SMALL"):
        return 1
    if column.startswith("BIG"):
        return 5
    if column.startswith("ME") and len(column) > 2 and column[2].isdigit():
        return int(column[2])
    return 0


# ------------------------------------------------------------- cost verdict


def breakeven_round_trip_bp(annual_pct: float, turnover_per_year: float) -> float:
    """The round-trip cost at which the premium is exactly eaten, in bp.

    A long-short factor holds one dollar long and one dollar short. Replacing a
    fraction `turnover_per_year` of each leg over a year moves `2 * turnover`
    dollars, and each dollar moved pays one round trip -- a sell and a buy. So
    the annual cost is `2 * turnover * cost`, and the premium is the budget:

        breakeven cost = annual premium / (2 * turnover)

    Everything on the right is either measured here or on the turnover axis, so
    no unverified quoted spread enters the conclusion. Compare the answer to
    what the relevant stocks actually cost to trade; for the smallest quintile
    that is tens of basis points, not the single digits a megacap costs.
    """
    if turnover_per_year <= 0:
        raise ValueError("turnover_per_year must be > 0")
    return annual_pct * 100.0 / (2.0 * turnover_per_year)


TURNOVER_AXIS = (1.0, 3.0, 6.0, 12.0)


def cost_table(annual_pct: float) -> dict[float, float]:
    return {t: breakeven_round_trip_bp(annual_pct, t) for t in TURNOVER_AXIS}


# ------------------------------------------------------------------ reports


def run(data_dir: str) -> dict:
    """The whole H4 screen: decay for every factor, then the size layer."""
    five = load(f"{data_dir}/F-F_Research_Data_5_Factors_2x3_CSV.zip")[0].monthly()
    mom = load(f"{data_dir}/F-F_Momentum_Factor_CSV.zip")[0].monthly()
    three = load(f"{data_dir}/F-F_Research_Data_Factors_CSV.zip")[0].monthly()

    def series_for(key: str) -> dict[str, float]:
        # SMB and HML start in 1926 in the three-factor file and only in 1963 in
        # the five-factor file. Banz's sample ends in 1975, so the long history
        # is the only one that has an in-sample window at all.
        if key == "Mom":
            return mom.series("Mom")
        if key in three.columns:
            return three.series(key)
        return five.series(key)

    reports = [decay(series_for(a.key), a) for a in ANOMALIES]
    reports.append(decay(series_for("CMA"), CMA_ALTERNATE))

    value = size_spread(
        f"{data_dir}/25_Portfolios_5x5_CSV.zip",
        low="BM1",
        high="BM5",
        start=_next_month("199206"),
        end=None,
    )
    momentum = size_spread(
        f"{data_dir}/25_Portfolios_ME_Prior_12_2_CSV.zip",
        low="PRIOR1",
        high="PRIOR5",
        start=_next_month("199303"),
        end=None,
    )
    return {
        "decay": [r.as_dict() for r in reports],
        "decay_reports": reports,
        "size_value": value,
        "size_momentum": momentum,
    }


def render(report: dict) -> str:
    lines: list[str] = []
    lines.append("公开股票异象：发表后还剩多少")
    lines.append("")
    lines.append("窗口按每个异象自己的原始样本终点和发表日切。市场是对照组：")
    lines.append("它是承担不可分散风险的报酬，没有理由因为被写出来而消失。")
    lines.append("")
    head = f"{'因子':<14}{'样本内':>22}{'样本外未发表':>22}{'发表后':>22}{'发表后 95% 区间':>24}{'存活':>6}"
    lines.append(head)
    lines.append("-" * len(head))
    for row in report["decay_reports"]:
        def cell(stat: WindowStat) -> str:
            return f"{stat.annual_pct:>8.2f}%/y t={stat.t_stat:>5.2f}"

        lo, hi = row.post_pub.ci95
        mark = "是" if row.survives else "否"
        if row.is_control:
            mark += "*"
        lines.append(
            f"{row.label:<14}{cell(row.in_sample):>22}{cell(row.unpublished):>22}"
            f"{cell(row.post_pub):>22}"
            f"{f'({lo * MONTHS_PER_YEAR:>6.2f}, {hi * MONTHS_PER_YEAR:>6.2f})':>24}{mark:>6}"
        )
    lines.append("")
    lines.append("* = 对照组。区间按日历年整块重抽，不按月。")
    lines.append("")

    for title, key in (("价值（BM5 − BM1）", "size_value"), ("动量（PRIOR5 − PRIOR1）", "size_momentum")):
        lines.append(f"{title}，发表后，按规模五分位拆开")
        head = (
            f"{'桶':<8}{'年化':>10}{'t':>7}{'95% 区间':>20}"
            f"{'市值份额':>10}{'家数':>8}{'均市值':>12}{'最好5%占比':>12}"
        )
        lines.append(head)
        lines.append("-" * len(head))
        for bucket in report[key]:
            stat = bucket.stat
            lo, hi = stat.ci95
            lines.append(
                f"{bucket.label:<8}{stat.annual_pct:>9.2f}%{stat.t_stat:>7.2f}"
                f"{f'({lo * MONTHS_PER_YEAR:>6.2f}, {hi * MONTHS_PER_YEAR:>6.2f})':>20}"
                f"{bucket.cap_share * 100:>9.2f}%{bucket.firms:>8.0f}"
                f"{f'${bucket.mean_cap_musd:,.0f}M':>12}{stat.harvest_top5_share * 100:>11.1f}%"
            )
        lines.append("")
    return "\n".join(lines)

import io
import zipfile

import pytest

from btchour.research.equityfactors import (
    ANOMALIES,
    CMA_ALTERNATE,
    TURNOVER_AXIS,
    Anomaly,
    _next_month,
    _quintile_of,
    _window,
    aggregate_cap,
    breakeven_round_trip_bp,
    by_year,
    cost_table,
    decay,
    harvest_share,
    window_stat,
)
from btchour.research.frenchlib import Section, parse_sections, read_zip

# ------------------------------------------------------------------- parsing

SAMPLE = """This file was created using the 202607 CRSP database.

Missing data are indicated by -99.99 or -999.

  Average Value Weighted Returns -- Monthly
,SMALL LoBM,BIG HiBM
192607,   3.7806,   0.5623
192608,  -2.2074,   7.7576
192609,  -99.99,    1.0000

  Annual Factors: January-December
,SMALL LoBM,BIG HiBM
1926,   10.00,   2.00

  Number of Firms in Portfolios
,SMALL LoBM,BIG HiBM
192607,      10,       5
"""


def test_parse_splits_stacked_sections_and_keeps_titles():
    sections = parse_sections(SAMPLE)
    titles = [s.title for s in sections]
    assert "Average Value Weighted Returns -- Monthly" in titles
    assert "Number of Firms in Portfolios" in titles


def test_missing_sentinel_becomes_none_and_is_dropped_from_series():
    monthly = parse_sections(SAMPLE)[0]
    series = monthly.series("SMALL LoBM")
    assert "192609" not in series  # -99.99 is the library's missing marker
    assert series["192607"] == pytest.approx(3.7806)


def test_monthly_filter_excludes_the_annual_block():
    annual = [s for s in parse_sections(SAMPLE) if "Annual" in s.title][0]
    assert annual.rows  # the 4-digit row parsed
    assert annual.monthly().rows == {}  # but never counts as a month


def test_read_zip_rejects_an_archive_without_exactly_one_csv(tmp_path):
    path = tmp_path / "two.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.csv", "x")
        zf.writestr("b.csv", "y")
    with pytest.raises(ValueError):
        read_zip(str(path))


# -------------------------------------------------------------- date windows


def test_next_month_rolls_the_year():
    assert _next_month("199212") == "199301"
    assert _next_month("199206") == "199207"


def test_window_is_inclusive_on_both_ends():
    series = {"199201": 1.0, "199202": 2.0, "199203": 3.0}
    assert sorted(_window(series, "199202", "199203")) == ["199202", "199203"]
    assert sorted(_window(series, None, None)) == sorted(series)


def test_decay_windows_do_not_overlap_and_leave_no_gap():
    a = Anomaly("X", "x", "p", sample_end="199012", published="199206", source="s")
    series = {f"{y}{m:02d}": 1.0 for y in range(1985, 2000) for m in range(1, 13)}
    report = decay(series, a)
    assert report.in_sample.end == "199012"
    assert report.unpublished.start == "199101"  # picks up the month after
    assert report.unpublished.end == "199206"
    assert report.post_pub.start == "199207"
    total = report.in_sample.months + report.unpublished.months + report.post_pub.months
    assert total == len(series)  # partition, not a sample with holes


# ------------------------------------------------------------------ the unit


def test_bootstrap_groups_by_calendar_year_not_by_month():
    series = {f"1990{m:02d}": 1.0 for m in range(1, 13)}
    series.update({f"1991{m:02d}": 2.0 for m in range(1, 13)})
    groups = by_year(series)
    assert len(groups) == 2 and all(len(g) == 12 for g in groups)


def test_a_premium_from_one_regime_gets_a_wider_interval_than_month_resampling():
    # Two decades that disagree. Resampling months would call this significant;
    # resampling years has to admit only two independent blocks exist.
    series = {f"1990{m:02d}": 5.0 for m in range(1, 13)}
    series.update({f"2000{m:02d}": -4.0 for m in range(1, 13)})
    stat = window_stat("w", series)
    lo, hi = stat.ci95
    assert lo < 0 < hi


# ---------------------------------------------------------- harvest + halves


def test_harvest_share_flags_a_premium_carried_by_a_few_months():
    lottery = [0.0] * 95 + [100.0] * 5
    assert harvest_share(lottery) == pytest.approx(1.0)
    even = [1.0] * 100
    assert harvest_share(even) == pytest.approx(0.05, abs=0.01)


def test_harvest_share_can_exceed_one_when_the_rest_is_net_negative():
    values = [10.0] * 5 + [-0.4] * 95  # total is +12, the best five are +50
    assert harvest_share(values) > 1.0


def test_harvest_share_is_zero_when_there_is_no_premium_to_explain():
    assert harvest_share([-1.0, -2.0]) == 0.0
    assert harvest_share([]) == 0.0


def test_sign_stability_needs_both_halves_to_agree():
    rising = {f"199{y}{m:02d}": (-1.0 if y < 5 else 1.0) for y in range(10) for m in range(1, 13)}
    assert not window_stat("w", rising).sign_stable
    steady = {f"199{y}{m:02d}": 1.0 for y in range(10) for m in range(1, 13)}
    assert window_stat("w", steady).sign_stable


# -------------------------------------------------------------------- survival


def test_survives_requires_the_interval_to_clear_zero_not_just_a_positive_mean():
    a = Anomaly("X", "x", "p", sample_end="199012", published="199206", source="s")
    # Post-publication years alternate sign: mean is positive, interval is not.
    series = {f"{y}{m:02d}": (6.0 if y % 2 else -5.0) for y in range(1993, 2013) for m in range(1, 13)}
    series.update({f"{y}{m:02d}": 1.0 for y in range(1985, 1991) for m in range(1, 13)})
    report = decay(series, a)
    assert report.post_pub.mean_pct > 0
    assert not report.survives


# ------------------------------------------------------------------- capacity


def test_aggregate_cap_multiplies_firm_count_by_average_size():
    firms = Section("n", ("A", "B"), {"202607": (10.0, 5.0)})
    caps = Section("c", ("A", "B"), {"202607": (100.0, 1000.0)})
    agg = aggregate_cap(firms, caps, "202607")
    assert agg == {"A": 1000.0, "B": 5000.0}


def test_aggregate_cap_treats_a_missing_cell_as_no_market_cap():
    firms = Section("n", ("A",), {"202607": (None,)})
    caps = Section("c", ("A",), {"202607": (100.0,)})
    assert aggregate_cap(firms, caps, "202607") == {"A": 0.0}


def test_quintile_of_reads_the_library_corner_names():
    assert _quintile_of("SMALL LoBM") == 1
    assert _quintile_of("BIG HiBM") == 5
    assert _quintile_of("ME3 BM2") == 3


# ----------------------------------------------------------------- cost floor


def test_breakeven_splits_the_premium_across_both_legs():
    # 12%/yr, one full turnover: two legs move a dollar each, so each round
    # trip may cost half the premium.
    assert breakeven_round_trip_bp(12.0, 1.0) == pytest.approx(600.0)


def test_breakeven_falls_inversely_with_turnover():
    table = cost_table(12.0)
    values = [table[t] for t in TURNOVER_AXIS]
    assert values == sorted(values, reverse=True)
    assert table[12.0] == pytest.approx(table[1.0] / 12.0)


def test_breakeven_rejects_zero_turnover():
    with pytest.raises(ValueError):
        breakeven_round_trip_bp(12.0, 0.0)


# ---------------------------------------------------------------- the roster


def test_every_anomaly_publishes_after_its_own_sample_ends():
    for a in (*ANOMALIES, CMA_ALTERNATE):
        assert a.published > a.sample_end, a.key


def test_exactly_one_control_and_it_is_the_market():
    controls = [a for a in ANOMALIES if a.is_control]
    assert [a.key for a in controls] == ["Mkt-RF"]

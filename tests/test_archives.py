"""Offline tests for the archive fetcher. No network: the parts that can be
wrong without anyone noticing are the month arithmetic, the symbol mapping and
the path layout, and all three are pure functions."""

from __future__ import annotations

from btchour.research import archives as A


def test_months_is_inclusive_and_rolls_the_year():
    assert A.months("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert A.months("2026-03", "2026-03") == ["2026-03"]
    assert A.months("2026-04", "2026-03") == []


def test_paths_cover_every_kind_the_research_modules_load():
    for kind in ("fundingRate", "klines", "markPriceKlines", "spotKlines"):
        assert kind in A.PATHS


def test_funding_path_has_no_interval_but_klines_do():
    f = A.PATHS["fundingRate"].format(sym="BTCUSDT", month="2026-06", iv="1h")
    assert f == "futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-06.zip"
    k = A.PATHS["klines"].format(sym="BTCUSDT", month="2026-06", iv="1d")
    assert k.endswith("1d/BTCUSDT-1d-2026-06.zip")
    s = A.PATHS["spotKlines"].format(sym="BTCUSDT", month="2026-06", iv="1d")
    assert s.startswith("spot/monthly/klines/")


def test_hl_coin_mapping_strips_the_contract_multiplier():
    assert A.hl_coin_for("1000PEPEUSDT") == "PEPE"
    assert A.hl_coin_for("1MBABYDOGEUSDT") == "BABYDOGE"
    assert A.hl_coin_for("1000000MOGUSDT") == "MOG"
    assert A.hl_coin_for("BTCUSDT") == "BTC"
    assert A.hl_coin_for("BTC") == "BTC"


def test_multiplier_strip_does_not_empty_the_name():
    # A coin literally called 1000 would otherwise map to the empty string.
    assert A.hl_coin_for("1000USDT") == "1000"

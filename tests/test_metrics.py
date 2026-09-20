"""The block bootstrap, which is the one new estimator decision 024 needed.

`bootstrap_ci` and `cluster_ci` are exercised through the studies that use
them; what gets tested here is the piece that decides whether a sub-period
verdict has any power behind it, because getting it wrong manufactures
significance rather than losing it.
"""

from __future__ import annotations


def test_block_bootstrap_is_wider_than_iid_on_overlapping_windows():
    """The whole point of `block_ci` is to buy power without buying a lie.

    A 30-day moving average of white noise has exactly the dependence that
    daily-overlapping 30-day windows have. The mean of 1970 such windows is
    about the mean of the 2000 underlying draws, so the honest 95% interval is
    roughly 2 * 1.96 / sqrt(2000) = 0.088 wide. Resampling the windows as if
    they were independent gives a fraction of that, which is how overlapping
    windows manufacture significance; resampling 30-long blocks gets close.
    """
    import random

    from btchour.research.metrics import block_ci, bootstrap_ci

    rng = random.Random(3)
    raw = [rng.gauss(0, 1) for _ in range(2000)]
    series = [sum(raw[i : i + 30]) / 30 for i in range(len(raw) - 30)]

    blo, bhi = block_ci(series, block=30, draws=4000)
    ilo, ihi = bootstrap_ci(series, draws=4000)
    honest = 2 * 1.96 / len(raw) ** 0.5

    assert (bhi - blo) > 3 * (ihi - ilo)
    assert 0.5 * honest < (bhi - blo) < 1.5 * honest
    assert (ihi - ilo) < 0.4 * honest  # far too narrow, as advertised


def test_block_bootstrap_degenerates_gracefully():
    from btchour.research.metrics import block_ci

    assert block_ci([], block=30) == (0.0, 0.0)
    assert block_ci([1.5], block=30) == (1.5, 1.5)
    # a block longer than the series is clamped, not an error
    lo, hi = block_ci([1.0, 2.0, 3.0], block=99)
    assert lo == hi == 2.0

from decimal import Decimal

import pytest

from conftest import make_input
from salaryops.salary_band import BandCategory, analyze_band

TOL = Decimal("0.05")


def band_for(base: int, **band):
    offer = make_input(compensation={"base": base}, salary_band=band)
    return analyze_band(offer.compensation.base, offer.salary_band, TOL)


@pytest.mark.parametrize(
    ("base", "category"),
    [
        (1300000, BandCategory.BELOW_BAND),
        (1400000, BandCategory.LOWER_BAND),
        (1500000, BandCategory.LOWER_BAND),
        (1650000, BandCategory.NEAR_MIDPOINT),
        (1600000, BandCategory.NEAR_MIDPOINT),
        (1800000, BandCategory.UPPER_BAND),
        (1900000, BandCategory.UPPER_BAND),
        (2000000, BandCategory.ABOVE_BAND),
    ],
)
def test_categories(base, category):
    assert band_for(base).category is category


def test_metrics_for_spec_example():
    r = band_for(1500000)
    assert r.compa_ratio == Decimal("0.9091")
    assert r.band_position == Decimal("0.2000")
    assert r.distance_to_midpoint == Decimal(150000)
    assert r.distance_to_high == Decimal(400000)


def test_position_outside_band_is_negative_or_above_one():
    assert band_for(1300000).band_position < 0
    assert band_for(2000000).band_position > 1


def test_midpoint_derived_when_absent():
    offer = make_input()
    band = offer.salary_band.model_copy(update={"midpoint": None})
    r = analyze_band(Decimal(1650000), band, TOL)
    assert r.midpoint == Decimal(1650000)
    assert r.midpoint_derived


def test_no_band_is_unknown():
    r = analyze_band(Decimal(1500000), None, TOL)
    assert r.category is BandCategory.UNKNOWN
    assert r.compa_ratio is None


def test_tolerance_comes_from_settings():
    offer = make_input(compensation={"base": 1550000})
    assert analyze_band(offer.compensation.base, offer.salary_band, TOL).category is BandCategory.LOWER_BAND
    wide = analyze_band(offer.compensation.base, offer.salary_band, Decimal("0.1"))
    assert wide.category is BandCategory.NEAR_MIDPOINT

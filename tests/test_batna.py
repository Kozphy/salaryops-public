from datetime import date

import pytest

from salaryops.batna import LeverageLevel, assess_leverage, days_until
from salaryops.models import Batna, LeveragePoints, PolicySettings

S = PolicySettings()


def lev(days=None, **batna):
    return assess_leverage(Batna(**batna), days, S)


def test_spec_example_is_strong():
    r = lev(competing_offers=1, late_stage_interviews=2, currently_employed=False)
    assert r.level is LeverageLevel.STRONG
    assert r.points == 3
    assert "1 competing offer (+2)" in r.evidence


def test_currently_employed_alone_is_moderate():
    r = lev(currently_employed=True)
    assert (r.level, r.points) == (LeverageLevel.MODERATE, 1)


def test_no_alternatives_is_low_with_penalty():
    r = lev(currently_employed=False)
    assert r.level is LeverageLevel.LOW
    assert r.points == -1
    assert any(e.startswith("no alternatives") for e in r.evidence)


def test_single_interview_counts_as_alternative_but_no_points():
    r = lev(late_stage_interviews=1, currently_employed=False)
    assert r.points == 0
    assert not any(e.startswith("no alternatives") for e in r.evidence)


@pytest.mark.parametrize(("days", "points"), [(3, 0), (0, 0), (-2, 0), (4, 1), (None, 1)])
def test_hard_deadline_penalty(days, points):
    assert lev(days, currently_employed=True).points == points


def test_missing_batna_is_unknown():
    r = assess_leverage(None, 10, S)
    assert r.level is LeverageLevel.UNKNOWN
    assert r.points is None
    assert not r.at_least_moderate


def test_points_are_configurable():
    custom = PolicySettings(leverage_points=LeveragePoints(competing_offer=3))
    assert assess_leverage(Batna(competing_offers=1), None, custom).level is LeverageLevel.STRONG


def test_days_until():
    assert days_until(date(2026, 10, 5), date(2026, 10, 2)) == 3
    assert days_until(None, date(2026, 10, 2)) is None

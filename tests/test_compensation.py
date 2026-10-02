from decimal import Decimal

from conftest import DELETE, make_input
from salaryops.compensation import compute_compensation
from salaryops.models import AmountStatus


def comp_for(**compensation):
    return compute_compensation(make_input(compensation=compensation).compensation)


def test_base_only_with_known_zeros():
    r = comp_for(base=1000000, bonus=0, annualized_equity=0, sign_on=0)
    assert r.recurring_floor == r.year1_floor == Decimal(1000000)
    assert r.recurring_complete and r.year1_complete


def test_bonus_and_equity_are_recurring():
    r = comp_for(base=1000000, bonus=100000, annualized_equity=50000, sign_on=0)
    assert r.recurring_floor == Decimal(1150000)


def test_sign_on_is_year1_only():
    r = comp_for(base=1000000, bonus=0, annualized_equity=0, sign_on=200000)
    assert r.recurring_floor == Decimal(1000000)
    assert r.year1_floor == Decimal(1200000)


def test_equity_grant_is_annualized_over_vesting():
    r = comp_for(annualized_equity=DELETE, equity_grant=400000, vesting_years=4)
    assert r.equity.value == Decimal(100000)
    assert r.equity_basis == "grant / 4 years"


def test_grant_without_vesting_schedule_is_unknown():
    r = comp_for(annualized_equity=DELETE, equity_grant=400000)
    assert r.equity.status is AmountStatus.UNKNOWN
    assert r.vesting_schedule_missing
    assert "equity" in r.unknown_recurring


def test_unknown_components_are_not_zero():
    r = comp_for(base=1000000, bonus="unknown", annualized_equity=DELETE, sign_on=DELETE)
    assert r.recurring_floor == Decimal(1000000)
    assert r.unknown_recurring == ("bonus", "equity")
    assert r.unknown_one_time == ("sign_on",)
    assert not r.recurring_complete and not r.year1_complete


def test_benefits_never_added_to_tc():
    r = comp_for(base=1000000, bonus=0, annualized_equity=0, sign_on=0, benefits_value=80000)
    assert r.benefits.value == Decimal(80000)
    assert r.year1_floor == Decimal(1000000)


def test_to_dict_serializes_decimals_as_strings():
    d = comp_for(base=1000000, bonus=DELETE).to_dict()
    assert d["base"] == "1000000"
    assert d["bonus"] == {"status": "UNKNOWN", "value": None}

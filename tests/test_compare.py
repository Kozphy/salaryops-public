from decimal import Decimal

import pytest

from conftest import AS_OF, make_input
from salaryops.compare import compare, parse_fx
from salaryops.decision import analyze
from salaryops.models import FailureClass, PolicySettings, SalaryOpsError


def a(**override):
    return analyze(make_input(**override), PolicySettings(), AS_OF)


USD_OFFER = {
    "compensation": {"currency": "USD", "base": 60000, "bonus": 0, "annualized_equity": 0, "sign_on": 0},
    "salary_band": {"low": 50000, "midpoint": 60000, "high": 70000},
}


def test_dimensions_reported_independently():
    cmp = compare([("A", a()), ("B", a(requirements={"travel_percent": 30}))])
    col_a, col_b = cmp.columns
    assert col_a.travel == "ok (5%)"
    assert col_b.travel == "CONFLICT (30%)"
    assert col_b.constraint_violations[0].startswith("POLICY-005")
    assert cmp.to_dict()["combined_score"] is None


def test_mixed_currency_requires_explicit_fx():
    with pytest.raises(SalaryOpsError) as info:
        compare([("A", a()), ("B", a(**USD_OFFER))])
    assert info.value.failure.failure_class is FailureClass.CURRENCY_MISMATCH


def test_explicit_fx_converts_money():
    cmp = compare([("A", a()), ("B", a(**USD_OFFER))], fx={"usd": Decimal("31.5")})
    usd = cmp.columns[1]
    assert usd.base == Decimal("1890000.0")
    assert usd.source_currency == "USD" and usd.rate == Decimal("31.5")


def test_unknown_amounts_stay_incomplete():
    cmp = compare([("A", a()), ("B", a(compensation={"bonus": "unknown"}))])
    assert not cmp.columns[1].recurring_complete
    assert "bonus" in cmp.columns[1].critical_missing


@pytest.mark.parametrize("n", [1, 4])
def test_offer_count_limits(n):
    with pytest.raises(SalaryOpsError) as info:
        compare([(str(i), a()) for i in range(n)])
    assert info.value.failure.failure_class is FailureClass.INPUT_INVALID


@pytest.mark.parametrize("bad", ["USD", "USD=abc", "USD=-1", "DOLLAR=3"])
def test_parse_fx_rejects_bad_pairs(bad):
    with pytest.raises(SalaryOpsError):
        parse_fx([bad])


def test_parse_fx():
    assert parse_fx(["usd=31.5", "JPY=0.21"]) == {"USD": Decimal("31.5"), "JPY": Decimal("0.21")}

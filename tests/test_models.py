from decimal import Decimal
from pathlib import Path

import pytest

from conftest import BASE_OFFER, DELETE, make_input, merge
from salaryops.models import (
    Amount,
    AmountStatus,
    FailureClass,
    PolicySettings,
    SalaryOpsError,
    load_offer,
    load_settings,
    parse_offer,
)


@pytest.mark.parametrize(
    ("raw", "status", "value"),
    [
        (None, AmountStatus.UNKNOWN, None),
        ("unknown", AmountStatus.UNKNOWN, None),
        (" Unknown ", AmountStatus.UNKNOWN, None),
        (0, AmountStatus.KNOWN_ZERO, Decimal(0)),
        (150000, AmountStatus.KNOWN_VALUE, Decimal(150000)),
        ("1,500", AmountStatus.KNOWN_VALUE, Decimal(1500)),
    ],
)
def test_amount_tri_state(raw, status, value):
    a = Amount.model_validate(raw)
    assert a.status is status
    assert a.value == value


@pytest.mark.parametrize("raw", [-1, True, "lots", float("inf")])
def test_amount_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        Amount.model_validate(raw)


def test_omitted_amount_is_unknown_not_zero():
    offer = make_input(compensation={"bonus": DELETE})
    assert offer.compensation.bonus.status is AmountStatus.UNKNOWN


def _failure(data) -> SalaryOpsError:
    with pytest.raises(SalaryOpsError) as info:
        parse_offer(data)
    return info.value


def test_invalid_input_maps_to_input_invalid():
    err = _failure(merge(BASE_OFFER, {"compensation": {"base": 0}}))
    assert err.failure.failure_class is FailureClass.INPUT_INVALID
    assert err.failure.field == "compensation.base"


def test_unknown_keys_are_rejected():
    err = _failure(merge(BASE_OFFER, {"compensaton": {}}))
    assert err.failure.failure_class is FailureClass.INPUT_INVALID


@pytest.mark.parametrize(
    ("band", "message"),
    [
        ({"low": 2000000}, "low must be below high"),
        ({"midpoint": 2000000}, "midpoint must be between low and high"),
    ],
)
def test_invalid_band_maps_to_band_failure(band, message):
    err = _failure(merge(BASE_OFFER, {"salary_band": band}))
    assert err.failure.failure_class is FailureClass.SALARY_BAND_INVALID
    assert message in err.failure.message


def test_band_in_other_currency_is_rejected():
    err = _failure(merge(BASE_OFFER, {"salary_band": {"currency": "usd"}}))
    assert err.failure.failure_class is FailureClass.CURRENCY_MISMATCH


def test_both_equity_forms_rejected():
    err = _failure(merge(BASE_OFFER, {"compensation": {"annualized_equity": 100, "equity_grant": 400}}))
    assert "not both" in err.failure.message


@pytest.mark.parametrize(
    ("compensation", "message"),
    [
        ({"vesting_years": 1, "vesting_cliff_months": 12}, "shorter than the vesting period"),
        ({"equity_discount": 1}, "less than 1"),
        ({"equity_kind": "maybe"}, "equity_kind"),
    ],
)
def test_equity_detail_validation(compensation, message):
    err = _failure(merge(BASE_OFFER, {"compensation": compensation}))
    assert err.failure.failure_class is FailureClass.INPUT_INVALID
    assert message in err.failure.message


def test_failure_is_machine_readable():
    err = _failure(merge(BASE_OFFER, {"salary_band": {"midpoint": 1}}))
    assert err.to_dict() == {
        "failure_class": "SALARY_BAND_INVALID",
        "message": "salary_band: midpoint must be between low and high",
        "field": "salary_band",
    }


def test_load_offer_reports_bad_yaml(tmp_path: Path):
    path = tmp_path / "offer.yaml"
    path.write_text("role: [unclosed", encoding="utf-8")
    with pytest.raises(SalaryOpsError) as info:
        load_offer(path)
    assert info.value.failure.failure_class is FailureClass.INPUT_INVALID


def test_load_offer_missing_file(tmp_path: Path):
    with pytest.raises(SalaryOpsError) as info:
        load_offer(tmp_path / "nope.yaml")
    assert "cannot read" in info.value.failure.message


def test_settings_overlay_changes_only_given_thresholds(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    path.write_text("near_midpoint_tolerance: 0.1\nleverage_points:\n  competing_offer: 3\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.near_midpoint_tolerance == Decimal("0.1")
    assert settings.leverage_points.competing_offer == 3
    assert settings.hard_deadline_days == PolicySettings().hard_deadline_days
    assert settings.sha256() != PolicySettings().sha256()


def test_settings_overlay_rejects_unknown_keys(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    path.write_text("allow_everything: true\n", encoding="utf-8")
    with pytest.raises(SalaryOpsError) as info:
        load_settings(path)
    assert info.value.failure.message.startswith("policy config:")


def test_default_settings_without_file():
    assert load_settings(None) == PolicySettings()

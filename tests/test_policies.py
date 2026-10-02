"""Every policy: triggered, passed, and (where it can be) not evaluable."""

import pytest

from conftest import AS_OF, DELETE, make_input
from salaryops.decision import analyze
from salaryops.models import PolicySettings
from salaryops.policies import Outcome, PolicyResult

T, P, N = Outcome.TRIGGERED, Outcome.PASSED, Outcome.NOT_EVALUABLE

WEAK = {"competing_offers": 0, "late_stage_interviews": 0, "currently_employed": False}
STRONG = {"competing_offers": 1, "late_stage_interviews": 2, "currently_employed": True}
BONUS_UNKNOWN_BELOW_TC = {"base": 1500000, "bonus": DELETE}  # floor 1.5M < min TC 1.6M


def result(policy_id: str, **override) -> PolicyResult:
    analysis = analyze(make_input(**override), PolicySettings(), AS_OF)
    return next(r for r in analysis.policies if r.policy_id == policy_id)


def outcome(policy_id: str, **override) -> Outcome:
    return result(policy_id, **override).outcome


def test_base_offer_passes_every_gating_policy():
    analysis = analyze(make_input(), PolicySettings(), AS_OF)
    assert {r.policy_id: r.outcome for r in analysis.policies if r.outcome is not P} == {"POLICY-012": T}


def test_p001_band_unknown():
    r = result("POLICY-001", salary_band=DELETE)
    assert r.outcome is T
    assert r.evidence == {"recruiter_type": "in_house"}
    assert outcome("POLICY-001") is P


def test_p002_critical_missing_excludes_band():
    r = result("POLICY-002", employment_type=None)
    assert r.outcome is T
    assert r.evidence["missing_fields"] == ["employment_type"]
    assert outcome("POLICY-002", salary_band=DELETE) is P


@pytest.mark.parametrize(
    "override",
    [
        {"location": {"remote": True, "remote_eligible_countries": ["United States"]}},
        {"location": {"country": "Japan"}},
        {"constraints": {"remote_required": True}},
        {"constraints": {"needs_sponsorship": True}, "requirements": {"sponsorship_offered": False}},
    ],
    ids=["remote-geography", "on-site-location", "remote-required", "sponsorship"],
)
def test_p003_location_conflicts(override):
    r = result("POLICY-003", **override)
    assert r.outcome is T
    assert r.evidence["conflicts"]


@pytest.mark.parametrize(
    ("override", "missing"),
    [
        ({"location": {"remote": True}}, ("remote_geography",)),
        ({"location": {"remote": True, "remote_eligible_countries": ["Taiwan"]},
          "constraints": {"candidate_country": None}}, ("candidate_country",)),
        ({"constraints": {"needs_sponsorship": True}}, ("sponsorship",)),
        ({"constraints": {"remote_required": True}, "location": {"remote": None}}, ("remote_policy",)),
    ],
)
def test_p003_not_evaluable(override, missing):
    r = result("POLICY-003", **override)
    assert (r.outcome, r.missing) == (N, missing)


def test_p003_country_match_is_case_insensitive():
    assert outcome("POLICY-003", location={"remote": True, "remote_eligible_countries": ["taiwan "]}) is P


def test_p004_on_call():
    assert outcome("POLICY-004", requirements={"on_call": True}) is T
    assert outcome("POLICY-004", requirements={"on_call": None}) is N
    assert outcome("POLICY-004", requirements={"on_call": True}, constraints={"on_call_allowed": None}) is P


def test_p005_travel():
    r = result("POLICY-005", requirements={"travel_percent": 20})
    assert r.outcome is T
    assert r.evidence == {"max_travel_percent": "10", "travel_percent": "20"}
    assert outcome("POLICY-005", requirements={"travel_percent": None}) is N
    assert outcome("POLICY-005", constraints={"max_travel_percent": None}) is P


def test_p006_final_offer_below_minimum():
    assert outcome("POLICY-006", employer_final_offer=True, compensation={"base": 1300000}) is T
    assert outcome("POLICY-006", employer_final_offer=True, compensation=BONUS_UNKNOWN_BELOW_TC) is N
    assert outcome("POLICY-006", compensation={"base": 1300000}) is P
    assert outcome("POLICY-006", employer_final_offer=True) is P


def test_p007_below_minimum_weak_leverage():
    assert outcome("POLICY-007", compensation={"base": 1300000}, batna=WEAK) is T
    assert outcome("POLICY-007", compensation={"base": 1300000}, batna=DELETE) is T
    assert outcome("POLICY-007", compensation=BONUS_UNKNOWN_BELOW_TC, batna=WEAK) is N
    assert outcome("POLICY-007", compensation={"base": 1300000}) is P
    assert outcome("POLICY-007", compensation={"base": 1300000}, batna=WEAK, employer_final_offer=True) is P


def test_p008_sign_on_masks_recurring_gap():
    masked = {"base": 1450000, "bonus": 0, "sign_on": 200000}
    r = result("POLICY-008", compensation=masked)
    assert r.outcome is T
    assert r.evidence["recurring_gap"] == "150000"
    assert outcome("POLICY-008", compensation=masked | {"sign_on": 50000}) is P
    assert outcome("POLICY-008", compensation=masked | {"bonus": DELETE}) is P
    assert outcome("POLICY-008", compensation=masked | {"base": 1350000, "sign_on": 300000}) is P


def test_p009_counter_to_minimum():
    r = result("POLICY-009", compensation={"base": 1300000})
    assert r.outcome is T
    assert r.evidence["base"] == "1300000" and r.evidence["minimum_base"] == "1400000"
    assert outcome("POLICY-009", compensation=BONUS_UNKNOWN_BELOW_TC) is N
    assert outcome("POLICY-009", compensation={"base": 1300000}, batna=WEAK) is P


def test_p009_uses_recurring_tc_not_year1():
    assert outcome("POLICY-009", compensation={"base": 1450000, "bonus": 0, "sign_on": 0}) is T


def test_p010_negotiate_toward_midpoint():
    lower = {"compensation": {"base": 1500000, "bonus": 150000}}
    assert outcome("POLICY-010", **lower) is T
    assert outcome("POLICY-010", **lower, salary_band={"confidence": "low"}) is P
    assert outcome("POLICY-010", **lower, batna=WEAK) is P
    assert outcome("POLICY-010") is P
    assert outcome("POLICY-010", salary_band=DELETE) is N
    assert outcome("POLICY-010", compensation={"base": 1300000}) is P


def test_p011_deadline_pressure():
    soon = {"recruiter": {"offer_deadline": "2026-10-04"}}
    assert outcome("POLICY-011", **soon, employment_type=None) is T
    assert outcome("POLICY-011", **soon) is P
    assert outcome("POLICY-011", employment_type=None) is P
    assert outcome("POLICY-011", employment_type=None, recruiter={"offer_deadline": None}) is P


def test_p012_acceptable_only_when_all_gating_pass():
    assert outcome("POLICY-012") is T
    r = result("POLICY-012", requirements={"travel_percent": None})
    assert r.outcome is P
    assert r.evidence["not_passed"] == {"POLICY-002": "TRIGGERED", "POLICY-005": "NOT_EVALUABLE"}


def test_p013_low_confidence_flag():
    r = result("POLICY-013", salary_band={"confidence": "low"})
    assert r.outcome is T and r.state is None
    assert outcome("POLICY-013") is P


def test_p014_above_band_flag():
    assert outcome("POLICY-014", compensation={"base": 2000000}) is T
    assert outcome("POLICY-014") is P


def test_settings_overlay_changes_policy_outcome():
    offer = make_input(recruiter={"offer_deadline": "2026-10-06"}, employment_type=None)
    default = analyze(offer, PolicySettings(), AS_OF)
    wider = analyze(offer, PolicySettings(deadline_pressure_days=5), AS_OF)
    by_id = lambda a: {r.policy_id: r.outcome for r in a.policies}  # noqa: E731
    assert by_id(default)["POLICY-011"] is P
    assert by_id(wider)["POLICY-011"] is T

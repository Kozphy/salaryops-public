"""End-to-end scenarios from the spec plus regression on the shipped examples."""

from pathlib import Path

import pytest

from conftest import AS_OF, DELETE, make_input
from salaryops.decision import analyze
from salaryops.models import PolicySettings, load_offer
from salaryops.policies import Action, DecisionState

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
S = DecisionState


def decision(**override):
    return analyze(make_input(**override), PolicySettings(), AS_OF).decision


def test_taiwan_candidate_us_only_remote_role_is_conflict():
    d = decision(
        location={"country": "United States", "remote": True, "remote_eligible_countries": ["United States"]},
        constraints={"candidate_country": "Taiwan"},
    )
    assert (d.state, d.deciding_policies) == (S.CONSTRAINT_CONFLICT, ("POLICY-003",))


def test_unknown_band_with_internal_recruiter_asks_for_band():
    d = decision(salary_band=DELETE, recruiter={"type": "in_house"})
    assert (d.state, d.action) == (S.NEED_MORE_INFORMATION, Action.ASK_FOR_BAND)


def test_below_minimum_with_strong_batna_counters():
    d = decision(
        compensation={"base": 1300000},
        batna={"competing_offers": 1, "late_stage_interviews": 2, "currently_employed": True},
    )
    assert (d.state, d.action) == (S.COUNTER, Action.COUNTER_TO_MINIMUM)


def test_below_minimum_with_no_alternatives_goes_to_human():
    d = decision(compensation={"base": 1300000},
                 batna={"competing_offers": 0, "late_stage_interviews": 0, "currently_employed": False})
    assert (d.state, d.action) == (S.HUMAN_REVIEW, Action.REVIEW_COUNTER_RISK)


def test_final_offer_below_minimum_never_auto_walks_away():
    d = decision(compensation={"base": 1300000}, employer_final_offer=True)
    assert (d.state, d.action) == (S.HUMAN_REVIEW, Action.REVIEW_WALK_AWAY)


def test_missing_info_beats_counter():
    d = decision(compensation={"base": 1300000}, employment_type=None)
    assert d.state is S.NEED_MORE_INFORMATION


def test_conflict_beats_everything():
    d = decision(compensation={"base": 1300000}, employment_type=None, requirements={"on_call": True},
                 employer_final_offer=True)
    assert d.state is S.CONSTRAINT_CONFLICT


@pytest.mark.parametrize(
    ("example", "state", "action"),
    [
        ("offer_basic.yaml", S.NEED_MORE_INFORMATION, Action.ASK_FOR_INFORMATION),
        ("offer_remote_conflict.yaml", S.CONSTRAINT_CONFLICT, Action.RESOLVE_CONSTRAINT),
        ("offer_counter.yaml", S.COUNTER, Action.COUNTER_TO_MINIMUM),
        ("offer_revised.yaml", S.ACCEPTABLE, Action.CONFIRM_IN_WRITING),
    ],
)
def test_examples(example, state, action):
    offer = load_offer(EXAMPLES / example)
    d = analyze(offer, PolicySettings(), offer.as_of).decision
    assert (d.state, d.action) == (state, action)


def test_spec_example_numbers():
    offer = load_offer(EXAMPLES / "offer_basic.yaml")
    a = analyze(offer, PolicySettings(), offer.as_of)
    assert str(a.compensation.recurring_floor) == "1850000"
    assert str(a.compensation.year1_floor) == "1950000"
    assert str(a.band.compa_ratio) == "0.9091"
    assert a.leverage.level.value == "STRONG"
    assert [m.field for m in a.critical_missing] == [
        "employment_type", "remote_geography", "on_call_requirement", "travel_requirement",
    ]

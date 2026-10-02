import pytest

from conftest import AS_OF, DELETE, make_input
from salaryops.decision import (
    ACTION_SUMMARY,
    HUMAN_TRANSITIONS,
    PRECEDENCE,
    analyze,
    apply_human_decision,
    decide,
)
from salaryops.models import FailureClass, PolicySettings, SalaryOpsError
from salaryops.policies import (
    FLAG_POLICIES,
    GATING_POLICIES,
    Action,
    DecisionState,
    Outcome,
    PolicyResult,
)

S = DecisionState


def pr(pid: str, outcome: Outcome, state: DecisionState | None, action: Action | None = None) -> PolicyResult:
    return PolicyResult(pid, pid, outcome, state, action or Action.ASK_FOR_INFORMATION, f"reason {pid}")


def test_strictest_state_wins():
    results = (
        pr("A", Outcome.TRIGGERED, S.COUNTER, Action.COUNTER_TO_MINIMUM),
        pr("B", Outcome.TRIGGERED, S.NEED_MORE_INFORMATION),
        pr("C", Outcome.TRIGGERED, S.CONSTRAINT_CONFLICT, Action.RESOLVE_CONSTRAINT),
        pr("D", Outcome.TRIGGERED, S.HUMAN_REVIEW, Action.REVIEW_COUNTER_RISK),
    )
    d = decide(results)
    assert (d.state, d.action, d.deciding_policies) == (S.CONSTRAINT_CONFLICT, Action.RESOLVE_CONSTRAINT, ("C",))


def test_first_policy_in_list_order_chooses_action():
    results = (
        pr("A", Outcome.TRIGGERED, S.COUNTER, Action.ASK_TO_CONVERT_SIGN_ON),
        pr("B", Outcome.TRIGGERED, S.COUNTER, Action.COUNTER_TO_MINIMUM),
    )
    d = decide(results)
    assert d.action is Action.ASK_TO_CONVERT_SIGN_ON
    assert d.deciding_policies == ("A", "B")


def test_not_evaluable_means_need_more_information():
    d = decide((pr("A", Outcome.NOT_EVALUABLE, S.CONSTRAINT_CONFLICT), pr("B", Outcome.PASSED, S.COUNTER)))
    assert (d.state, d.action) == (S.NEED_MORE_INFORMATION, Action.ASK_FOR_INFORMATION)


def test_flags_never_set_state():
    d = decide((pr("F", Outcome.TRIGGERED, None, Action.NEGOTIATE_NON_BASE),
                pr("A", Outcome.TRIGGERED, S.ACCEPTABLE, Action.CONFIRM_IN_WRITING)))
    assert d.state is S.ACCEPTABLE


def test_no_state_fails_closed_to_human_review():
    d = decide((pr("A", Outcome.PASSED, S.COUNTER),))
    assert d.state is S.HUMAN_REVIEW
    assert "failing closed" in d.reason


def test_precedence_covers_every_automatic_state_and_never_walk_away():
    assert set(PRECEDENCE) == set(DecisionState) - {S.WALK_AWAY}
    assert all(p.state is not S.WALK_AWAY for p in GATING_POLICIES + FLAG_POLICIES)


def test_every_state_has_transitions_and_walk_away_is_terminal():
    assert set(HUMAN_TRANSITIONS) == set(DecisionState)
    assert HUMAN_TRANSITIONS[S.WALK_AWAY] == frozenset()
    assert all(S.WALK_AWAY in allowed for s, allowed in HUMAN_TRANSITIONS.items() if s is not S.WALK_AWAY)


def test_human_decision_valid_and_invalid():
    assert apply_human_decision(S.HUMAN_REVIEW, S.COUNTER) is S.COUNTER
    with pytest.raises(SalaryOpsError) as info:
        apply_human_decision(S.CONSTRAINT_CONFLICT, S.ACCEPTABLE)
    assert info.value.failure.failure_class is FailureClass.INVALID_TRANSITION
    with pytest.raises(SalaryOpsError):
        apply_human_decision(S.WALK_AWAY, S.HUMAN_REVIEW)


def test_every_action_has_a_summary():
    assert set(ACTION_SUMMARY) == set(Action)


def test_analysis_is_deterministic():
    offer = make_input()
    assert analyze(offer, PolicySettings(), AS_OF).to_dict() == analyze(offer, PolicySettings(), AS_OF).to_dict()


def test_degraded_failures_listed():
    a = analyze(make_input(salary_band=DELETE, compensation={"bonus": DELETE}), PolicySettings(), AS_OF)
    assert [f.failure_class for f in a.failures] == [
        FailureClass.COMPENSATION_INCOMPLETE,
        FailureClass.MARKET_DATA_MISSING,
    ]

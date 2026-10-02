"""Analysis pipeline and decision state.

``analyze`` is a pure function of (offer, settings, as_of): the same inputs always give
the same state. Automatic states are re-derived on every analysis; only a human can move
an offer to WALK_AWAY or resolve HUMAN_REVIEW, through HUMAN_TRANSITIONS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .batna import LeverageResult, assess_leverage, days_until
from .compensation import CompResult, compute_compensation
from .missing_info import MissingItem, detect_missing, next_question
from .models import Failure, FailureClass, OfferInput, PolicySettings, SalaryOpsError
from .policies import Action, DecisionState, Facts, Outcome, PolicyResult, evaluate_policies
from .salary_band import BandResult, analyze_band

S = DecisionState

# Strictest first. When several policies fire, the earliest state in this list wins.
PRECEDENCE: tuple[DecisionState, ...] = (
    S.CONSTRAINT_CONFLICT,
    S.HUMAN_REVIEW,
    S.NEED_MORE_INFORMATION,
    S.COUNTER,
    S.READY_TO_NEGOTIATE,
    S.ACCEPTABLE,
)

# States a reviewer may move an offer to from its current state. WALK_AWAY is terminal and
# is never produced automatically.
HUMAN_TRANSITIONS: dict[DecisionState, frozenset[DecisionState]] = {
    S.NEED_MORE_INFORMATION: frozenset({S.WALK_AWAY}),
    S.READY_TO_NEGOTIATE: frozenset({S.WALK_AWAY}),
    S.COUNTER: frozenset({S.WALK_AWAY}),
    S.ACCEPTABLE: frozenset({S.WALK_AWAY}),
    S.HUMAN_REVIEW: frozenset({S.COUNTER, S.ACCEPTABLE, S.NEED_MORE_INFORMATION, S.WALK_AWAY}),
    S.CONSTRAINT_CONFLICT: frozenset({S.HUMAN_REVIEW, S.WALK_AWAY}),
    S.WALK_AWAY: frozenset(),
}

ACTION_SUMMARY: dict[Action, str] = {
    Action.ASK_FOR_BAND: "Ask the recruiter for the salary range before naming a number.",
    Action.ASK_FOR_INFORMATION: "Collect the missing critical information before negotiating.",
    Action.RESOLVE_CONSTRAINT: "The offer conflicts with one of your hard constraints; resolve it or walk away.",
    Action.REVIEW_WALK_AWAY: "Final offer is below your minimum. Decide yourself whether to walk away.",
    Action.REVIEW_COUNTER_RISK: "Below your minimum with weak leverage. Decide yourself whether to counter.",
    Action.ASK_TO_CONVERT_SIGN_ON: "Ask to move part of the sign-on bonus into base or recurring pay.",
    Action.COUNTER_TO_MINIMUM: "Counter with at least your minimum, citing your alternatives.",
    Action.ASK_TOWARD_MIDPOINT: "Your minimums are met; you have room to ask toward the band midpoint.",
    Action.ASK_FOR_EXTENSION: "Ask for a deadline extension to collect the missing information.",
    Action.CONFIRM_IN_WRITING: "The offer meets your requirements. Get the final terms in writing.",
    Action.VERIFY_BAND: "Verify the band with a second source.",
    Action.NEGOTIATE_NON_BASE: "Negotiate non-base terms (sign-on, equity, title, start date).",
}


@dataclass(frozen=True)
class Decision:
    state: DecisionState
    action: Action
    deciding_policies: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_state": self.state.value,
            "action": self.action.value,
            "deciding_policies": list(self.deciding_policies),
            "reason": self.reason,
        }


def requested_state(r: PolicyResult) -> DecisionState | None:
    if r.outcome is Outcome.TRIGGERED:
        return r.state
    if r.outcome is Outcome.NOT_EVALUABLE:
        return S.NEED_MORE_INFORMATION
    return None


def requested_action(r: PolicyResult) -> Action | None:
    if r.outcome is Outcome.TRIGGERED:
        return r.action
    if r.outcome is Outcome.NOT_EVALUABLE:
        return Action.ASK_FOR_INFORMATION
    return None


def decide(results: tuple[PolicyResult, ...]) -> Decision:
    requested = {r.policy_id: requested_state(r) for r in results}
    for state in PRECEDENCE:
        deciding = [r for r in results if requested[r.policy_id] is state]
        if deciding:
            first = deciding[0]
            action = requested_action(first)
            assert action is not None
            return Decision(state, action, tuple(r.policy_id for r in deciding), first.reason)
    return Decision(S.HUMAN_REVIEW, Action.REVIEW_COUNTER_RISK, (),
                    "no policy produced a state; failing closed to human review")


def apply_human_decision(current: DecisionState, requested: DecisionState) -> DecisionState:
    allowed = HUMAN_TRANSITIONS[current]
    if requested not in allowed:
        options = ", ".join(sorted(s.value for s in allowed)) or "none (terminal state)"
        raise SalaryOpsError(Failure(
            FailureClass.INVALID_TRANSITION,
            f"cannot move from {current.value} to {requested.value}; allowed: {options}",
        ))
    return requested


@dataclass(frozen=True)
class Analysis:
    offer: OfferInput
    as_of: date
    settings: PolicySettings
    compensation: CompResult
    band: BandResult
    missing: tuple[MissingItem, ...]
    leverage: LeverageResult
    policies: tuple[PolicyResult, ...]
    decision: Decision
    failures: tuple[Failure, ...]

    @property
    def critical_missing(self) -> tuple[MissingItem, ...]:
        return tuple(m for m in self.missing if m.critical)

    @property
    def next_question(self) -> str | None:
        return next_question(self.missing)

    @property
    def action_summary(self) -> str:
        return ACTION_SUMMARY[self.decision.action]

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.offer.company,
            "role": self.offer.role,
            "as_of": self.as_of.isoformat(),
            **self.decision.to_dict(),
            "action_summary": self.action_summary,
            "next_question": self.next_question,
            "missing_fields": [m.field for m in self.missing if m.critical],
            "compensation": self.compensation.to_dict(),
            "salary_band": self.band.to_dict(),
            "leverage": self.leverage.to_dict(),
            "missing": [m.to_dict() for m in self.missing],
            "policies": [r.to_dict() for r in self.policies],
            "failures": [f.to_dict() for f in self.failures],
        }


def degraded_failures(comp: CompResult, offer: OfferInput) -> tuple[Failure, ...]:
    out: list[Failure] = []
    unknown = comp.unknown_recurring + comp.unknown_one_time
    if unknown:
        out.append(Failure(
            FailureClass.COMPENSATION_INCOMPLETE,
            f"total compensation is a floor; unknown components: {', '.join(unknown)}",
            "compensation",
        ))
    if offer.salary_band is None:
        out.append(Failure(
            FailureClass.MARKET_DATA_MISSING,
            "no salary band; band position and compa-ratio cannot be computed",
            "salary_band",
        ))
    return tuple(out)


def analyze(offer: OfferInput, settings: PolicySettings, as_of: date) -> Analysis:
    comp = compute_compensation(offer.compensation)
    band = analyze_band(comp.base, offer.salary_band, settings.near_midpoint_tolerance)
    missing = detect_missing(offer, comp)
    leverage = assess_leverage(offer.batna, days_until(offer.recruiter.offer_deadline, as_of), settings)
    results = evaluate_policies(Facts(offer, settings, comp, band, missing, leverage))
    return Analysis(
        offer=offer,
        as_of=as_of,
        settings=settings,
        compensation=comp,
        band=band,
        missing=missing,
        leverage=leverage,
        policies=results,
        decision=decide(results),
        failures=degraded_failures(comp, offer),
    )

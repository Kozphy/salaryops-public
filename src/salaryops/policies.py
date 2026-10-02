"""Negotiation policies: plain functions with stable IDs, evaluated in a fixed order.

Each policy returns TRIGGERED, PASSED, or NOT_EVALUABLE (needed facts are unknown) and
carries the observed values it used as evidence. Thresholds come from PolicySettings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .batna import LeverageResult
from .compensation import CompResult
from .countries import country_code, same_place
from .missing_info import MissingItem
from .models import Confidence, OfferInput, PolicySettings
from .salary_band import BandCategory, BandResult


class Outcome(StrEnum):
    TRIGGERED = "TRIGGERED"
    PASSED = "PASSED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class DecisionState(StrEnum):
    NEED_MORE_INFORMATION = "NEED_MORE_INFORMATION"
    READY_TO_NEGOTIATE = "READY_TO_NEGOTIATE"
    COUNTER = "COUNTER"
    ACCEPTABLE = "ACCEPTABLE"
    CONSTRAINT_CONFLICT = "CONSTRAINT_CONFLICT"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    WALK_AWAY = "WALK_AWAY"


class Action(StrEnum):
    ASK_FOR_BAND = "ASK_FOR_BAND"
    ASK_FOR_INFORMATION = "ASK_FOR_INFORMATION"
    RESOLVE_CONSTRAINT = "RESOLVE_CONSTRAINT"
    REVIEW_WALK_AWAY = "REVIEW_WALK_AWAY"
    REVIEW_COUNTER_RISK = "REVIEW_COUNTER_RISK"
    ASK_TO_CONVERT_SIGN_ON = "ASK_TO_CONVERT_SIGN_ON"
    COUNTER_TO_MINIMUM = "COUNTER_TO_MINIMUM"
    ASK_TOWARD_MIDPOINT = "ASK_TOWARD_MIDPOINT"
    ASK_FOR_EXTENSION = "ASK_FOR_EXTENSION"
    CONFIRM_IN_WRITING = "CONFIRM_IN_WRITING"
    VERIFY_BAND = "VERIFY_BAND"
    NEGOTIATE_NON_BASE = "NEGOTIATE_NON_BASE"


@dataclass(frozen=True)
class Facts:
    offer: OfferInput
    settings: PolicySettings
    comp: CompResult
    band: BandResult
    missing: tuple[MissingItem, ...]
    leverage: LeverageResult


@dataclass(frozen=True)
class PolicyResult:
    policy_id: str
    title: str
    outcome: Outcome
    state: DecisionState | None
    action: Action | None
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    @property
    def triggered(self) -> bool:
        return self.outcome is Outcome.TRIGGERED

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "title": self.title,
            "result": self.outcome.value,
            "state": self.state.value if self.state and self.triggered else None,
            "action": self.action.value if self.action and self.triggered else None,
            "reason": self.reason,
            "evidence": self.evidence,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class Policy:
    policy_id: str
    title: str
    state: DecisionState | None
    action: Action | None
    check: Callable[[Facts], tuple[Outcome, str, dict[str, Any], tuple[str, ...]]]

    def evaluate(self, facts: Facts) -> PolicyResult:
        outcome, reason, evidence, missing = self.check(facts)
        return PolicyResult(self.policy_id, self.title, outcome, self.state, self.action, reason, evidence, missing)


Check = tuple[Outcome, str, dict[str, Any], tuple[str, ...]]
T, P, N = Outcome.TRIGGERED, Outcome.PASSED, Outcome.NOT_EVALUABLE


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


# --- shared fact: minimum status -------------------------------------------------------

@dataclass(frozen=True)
class MinimumStatus:
    status: str  # "below" | "ok" | "unknown" | "not_set"
    base_below: bool
    tc_below: bool
    evidence: dict[str, Any]
    missing: tuple[str, ...]


def minimum_status(f: Facts) -> MinimumStatus:
    c, comp = f.offer.constraints, f.comp
    ev: dict[str, Any] = {}
    base_below = tc_below = tc_unknown = False
    if c.minimum_base is not None:
        base_below = comp.base < c.minimum_base
        ev |= {"base": _s(comp.base), "minimum_base": _s(c.minimum_base)}
    if c.minimum_total_comp is not None:
        ev |= {
            "recurring_tc_floor": _s(comp.recurring_floor),
            "recurring_tc_complete": comp.recurring_complete,
            "minimum_total_comp": _s(c.minimum_total_comp),
        }
        if comp.recurring_floor < c.minimum_total_comp:
            tc_below = comp.recurring_complete
            tc_unknown = not comp.recurring_complete
    if base_below or tc_below:
        status = "below"
    elif tc_unknown:
        status = "unknown"
    elif c.minimum_base is None and c.minimum_total_comp is None:
        status = "not_set"
    else:
        status = "ok"
    missing = comp.unknown_recurring if status == "unknown" else ()
    return MinimumStatus(status, base_below, tc_below, ev, missing)


def _minimum_check(f: Facts, reason_below: str) -> Check:
    m = minimum_status(f)
    if m.status == "below":
        return T, reason_below, m.evidence, ()
    if m.status == "unknown":
        return N, "recurring TC floor is below the minimum but unknown components could close the gap", m.evidence, m.missing
    return P, "offer meets the stated minimums" if m.status == "ok" else "no minimums set", m.evidence, ()


# --- policies ------------------------------------------------------------------------

def p001_band_unknown(f: Facts) -> Check:
    ev = {"recruiter_type": f.offer.recruiter.type.value}
    if f.offer.salary_band is None:
        return T, "salary band unknown; ask for it before naming a number", ev, ()
    return P, "salary band provided", ev, ()


def p002_critical_missing(f: Facts) -> Check:
    fields = [m.field for m in f.missing if m.critical and m.field != "salary_band"]
    if fields:
        return T, f"critical information missing: {', '.join(fields)}", {"missing_fields": fields}, ()
    return P, "no critical information missing besides the band", {}, ()


def p003_location(f: Facts) -> Check:
    loc, c, req = f.offer.location, f.offer.constraints, f.offer.requirements
    conflicts: list[str] = []
    unknown: list[str] = []
    ev: dict[str, Any] = {
        "role_remote": loc.remote,
        "role_country": loc.country,
        "remote_eligible_countries": loc.remote_eligible_countries,
        "candidate_country": c.candidate_country,
    }
    places = [loc.country, loc.city, c.candidate_country, *(loc.remote_eligible_countries or []), *c.acceptable_locations]
    if named := [p for p in places if p]:
        ev["country_codes"] = {p: country_code(p) for p in named}
    if loc.remote is True and loc.remote_eligible_countries is None:
        unknown.append("remote_geography")
    elif loc.remote is True and loc.remote_eligible_countries:
        if c.candidate_country is None:
            unknown.append("candidate_country")
        elif not any(same_place(c.candidate_country, x) for x in loc.remote_eligible_countries):
            conflicts.append(
                f"remote work limited to {', '.join(loc.remote_eligible_countries)}; candidate is in {c.candidate_country}"
            )
    if c.remote_required:
        ev["remote_required"] = True
        if loc.remote is False:
            conflicts.append("role is not remote but remote work is required")
        elif loc.remote is None:
            unknown.append("remote_policy")
    if loc.remote is False and c.acceptable_locations:
        ev["acceptable_locations"] = c.acceptable_locations
        onsite = [p for p in (loc.country, loc.city) if p]
        if not onsite:
            unknown.append("location")
        elif not any(same_place(p, a) for p in onsite for a in c.acceptable_locations):
            conflicts.append(f"on-site location {', '.join(onsite)} is not in acceptable locations")
    if c.needs_sponsorship:
        ev["sponsorship_offered"] = req.sponsorship_offered
        if req.sponsorship_offered is False:
            conflicts.append("work-authorization sponsorship needed but not offered")
        elif req.sponsorship_offered is None:
            unknown.append("sponsorship")
    if conflicts:
        return T, "; ".join(conflicts), ev | {"conflicts": conflicts}, ()
    if unknown:
        return N, f"cannot check location fit: {', '.join(unknown)} unknown", ev, tuple(unknown)
    return P, "no location or work-authorization conflict", ev, ()


def p004_on_call(f: Facts) -> Check:
    allowed, required = f.offer.constraints.on_call_allowed, f.offer.requirements.on_call
    ev = {"on_call_allowed": allowed, "on_call_required": required}
    if allowed is not False:
        return P, "no on-call constraint", ev, ()
    if required is None:
        return N, "on-call not allowed but the requirement is unknown", ev, ("on_call_requirement",)
    if required:
        return T, "role requires on-call; candidate does not accept on-call", ev, ()
    return P, "role has no on-call requirement", ev, ()


def p005_travel(f: Facts) -> Check:
    limit, travel = f.offer.constraints.max_travel_percent, f.offer.requirements.travel_percent
    ev = {"max_travel_percent": _s(limit), "travel_percent": _s(travel)}
    if limit is None:
        return P, "no travel limit set", ev, ()
    if travel is None:
        return N, "travel limit set but travel requirement unknown", ev, ("travel_requirement",)
    if travel > limit:
        return T, f"travel {travel}% exceeds limit of {limit}%", ev, ()
    return P, f"travel {travel}% within limit of {limit}%", ev, ()


def p006_final_below_minimum(f: Facts) -> Check:
    if not f.offer.employer_final_offer:
        return P, "offer not marked final", {"employer_final_offer": False}, ()
    outcome, reason, ev, missing = _minimum_check(
        f, "final offer is below your minimum; decide whether to walk away"
    )
    return outcome, reason, ev | {"employer_final_offer": True}, missing


def p007_below_minimum_weak_leverage(f: Facts) -> Check:
    lev = f.leverage
    ev = {"leverage": lev.level.value, "employer_final_offer": f.offer.employer_final_offer}
    if f.offer.employer_final_offer:
        return P, "final offer handled by POLICY-006", ev, ()
    if lev.at_least_moderate:
        return P, f"leverage is {lev.level.value}", ev, ()
    outcome, reason, mev, missing = _minimum_check(
        f, f"below minimum with {lev.level.value} leverage; countering risks the offer"
    )
    return outcome, reason, ev | mev, missing


def p008_sign_on_masks_gap(f: Facts) -> Check:
    c, comp = f.offer.constraints, f.comp
    if c.minimum_total_comp is None or not comp.recurring_complete or not comp.sign_on.is_known:
        return P, "not applicable (no TC minimum, or recurring TC / sign-on unknown)", {}, ()
    ev = {
        "recurring_tc": _s(comp.recurring_floor),
        "year1_tc": _s(comp.year1_floor),
        "minimum_total_comp": _s(c.minimum_total_comp),
    }
    base_ok = c.minimum_base is None or comp.base >= c.minimum_base
    if base_ok and comp.recurring_floor < c.minimum_total_comp <= comp.year1_floor:
        gap = c.minimum_total_comp - comp.recurring_floor
        return T, f"year-1 TC meets the minimum only because of sign-on; recurring gap is {gap}", ev | {"recurring_gap": str(gap)}, ()
    return P, "sign-on does not hide a recurring gap", ev, ()


def p009_counter_to_minimum(f: Facts) -> Check:
    lev = f.leverage
    ev = {"leverage": lev.level.value, "employer_final_offer": f.offer.employer_final_offer}
    if f.offer.employer_final_offer:
        return P, "final offer handled by POLICY-006", ev, ()
    if not lev.at_least_moderate:
        return P, f"leverage is {lev.level.value}; handled by POLICY-007", ev, ()
    outcome, reason, mev, missing = _minimum_check(
        f, f"below minimum with {lev.level.value} leverage; counter to at least your minimum"
    )
    return outcome, reason, ev | mev, missing


def p010_negotiate_toward_midpoint(f: Facts) -> Check:
    band, lev = f.band, f.leverage
    ev = {
        "band_category": band.category.value,
        "compa_ratio": _s(band.compa_ratio),
        "band_confidence": band.confidence,
        "leverage": lev.level.value,
    }
    if band.category is BandCategory.UNKNOWN:
        return N, "band unknown", ev, ("salary_band",)
    m = minimum_status(f)
    if m.status == "below":
        return P, "below minimum; handled by POLICIES 006-009", ev, ()
    if m.status == "unknown":
        return N, "minimum status unknown", ev | m.evidence, m.missing
    if band.category not in (BandCategory.BELOW_BAND, BandCategory.LOWER_BAND):
        return P, f"offer is {band.category.value}", ev, ()
    if not lev.at_least_moderate:
        return P, f"leverage is {lev.level.value}", ev, ()
    if band.confidence == Confidence.LOW.value:
        return P, "band confidence is low; verify the band before anchoring on it", ev, ()
    return T, f"minimums met, offer below midpoint (compa {band.compa_ratio}), leverage {lev.level.value}", ev, ()


def p011_deadline_pressure(f: Facts) -> Check:
    days = f.leverage.days_to_deadline
    critical = [m.field for m in f.missing if m.critical]
    ev = {"days_to_deadline": days, "critical_missing": critical,
          "deadline_pressure_days": f.settings.deadline_pressure_days}
    if days is None:
        return P, "offer deadline unknown", ev, ()
    if days <= f.settings.deadline_pressure_days and critical:
        return T, f"deadline in {days} days with critical information missing", ev, ()
    return P, "no deadline pressure on missing information", ev, ()


def p013_low_confidence_band(f: Facts) -> Check:
    ev = {"band_source": f.band.source, "band_confidence": f.band.confidence}
    if f.band.confidence == Confidence.LOW.value:
        return T, "band confidence is low; treat band metrics as rough", ev, ()
    return P, "band confidence is not low" if f.band.confidence else "no band", ev, ()


def p014_above_band(f: Facts) -> Check:
    ev = {"band_category": f.band.category.value, "distance_to_high": _s(f.band.distance_to_high)}
    if f.band.category is BandCategory.ABOVE_BAND:
        return T, "base is above the band; further base increases are unlikely, negotiate other terms", ev, ()
    return P, "base is not above the band", ev, ()


S = DecisionState
GATING_POLICIES: tuple[Policy, ...] = (
    Policy("POLICY-001", "Salary band unknown", S.NEED_MORE_INFORMATION, Action.ASK_FOR_BAND, p001_band_unknown),
    Policy("POLICY-002", "Critical information missing", S.NEED_MORE_INFORMATION, Action.ASK_FOR_INFORMATION, p002_critical_missing),
    Policy("POLICY-003", "Location / work-authorization conflict", S.CONSTRAINT_CONFLICT, Action.RESOLVE_CONSTRAINT, p003_location),
    Policy("POLICY-004", "On-call conflict", S.CONSTRAINT_CONFLICT, Action.RESOLVE_CONSTRAINT, p004_on_call),
    Policy("POLICY-005", "Travel conflict", S.CONSTRAINT_CONFLICT, Action.RESOLVE_CONSTRAINT, p005_travel),
    Policy("POLICY-006", "Final offer below minimum", S.HUMAN_REVIEW, Action.REVIEW_WALK_AWAY, p006_final_below_minimum),
    Policy("POLICY-007", "Below minimum with weak leverage", S.HUMAN_REVIEW, Action.REVIEW_COUNTER_RISK, p007_below_minimum_weak_leverage),
    Policy("POLICY-008", "Sign-on masks recurring gap", S.COUNTER, Action.ASK_TO_CONVERT_SIGN_ON, p008_sign_on_masks_gap),
    Policy("POLICY-009", "Below minimum with leverage", S.COUNTER, Action.COUNTER_TO_MINIMUM, p009_counter_to_minimum),
    Policy("POLICY-010", "Below midpoint with leverage", S.READY_TO_NEGOTIATE, Action.ASK_TOWARD_MIDPOINT, p010_negotiate_toward_midpoint),
    Policy("POLICY-011", "Deadline pressure with missing info", S.HUMAN_REVIEW, Action.ASK_FOR_EXTENSION, p011_deadline_pressure),
)

FLAG_POLICIES: tuple[Policy, ...] = (
    Policy("POLICY-013", "Low-confidence band (flag)", None, Action.VERIFY_BAND, p013_low_confidence_band),
    Policy("POLICY-014", "Above band (flag)", None, Action.NEGOTIATE_NON_BASE, p014_above_band),
)

ACCEPTABLE_ID = "POLICY-012"
ACCEPTABLE_TITLE = "All gating policies passed"


def p012_acceptable(gating: list[PolicyResult]) -> PolicyResult:
    not_passed = {r.policy_id: r.outcome.value for r in gating if r.outcome is not Outcome.PASSED}
    if not_passed:
        outcome, reason = P, f"{len(not_passed)} gating policies did not pass"
    else:
        outcome, reason = T, "all gating policies passed; get the final terms in writing"
    return PolicyResult(ACCEPTABLE_ID, ACCEPTABLE_TITLE, outcome, S.ACCEPTABLE, Action.CONFIRM_IN_WRITING,
                        reason, {"not_passed": not_passed})


def evaluate_policies(facts: Facts) -> tuple[PolicyResult, ...]:
    gating = [p.evaluate(facts) for p in GATING_POLICIES]
    flags = [p.evaluate(facts) for p in FLAG_POLICIES]
    return (*gating, p012_acceptable(gating), *flags)


POLICY_CATALOG: tuple[tuple[str, str, DecisionState | None, Action | None], ...] = (
    *((p.policy_id, p.title, p.state, p.action) for p in GATING_POLICIES),
    (ACCEPTABLE_ID, ACCEPTABLE_TITLE, S.ACCEPTABLE, Action.CONFIRM_IN_WRITING),
    *((p.policy_id, p.title, p.state, p.action) for p in FLAG_POLICIES),
)

"""Detect information that should be collected before negotiating.

An item is critical when the decision could change once it is known. Critical items come
first; within each group the order below is fixed so output is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .compensation import CompResult
from .models import OfferInput, RecruiterType

BAND_QUESTIONS = {
    RecruiterType.AGENCY: "Could you share the salary range your client has approved for this role?",
    RecruiterType.IN_HOUSE: "Could you share the salary range budgeted for this role, so I can make sure we're aligned?",
    RecruiterType.UNKNOWN: "Could you share the salary range budgeted for this role?",
}


@dataclass(frozen=True)
class MissingItem:
    field: str
    label: str
    critical: bool
    question: str
    ask_recruiter: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "label": self.label,
            "critical": self.critical,
            "question": self.question,
            "ask_recruiter": self.ask_recruiter,
        }


def detect_missing(offer: OfferInput, comp: CompResult) -> tuple[MissingItem, ...]:
    c = offer.constraints
    loc = offer.location
    req = offer.requirements
    items: list[MissingItem] = []

    def item(field: str, label: str, critical: bool, question: str, ask_recruiter: bool = True) -> None:
        items.append(MissingItem(field, label, critical, question, ask_recruiter))

    if offer.salary_band is None:
        item("salary_band", "salary band", True, BAND_QUESTIONS[offer.recruiter.type])
    if not offer.compensation.bonus.is_known:
        item("bonus", "annual bonus", True,
             "Is there an annual bonus, and what is the target amount or percentage?")
    if comp.vesting_schedule_missing:
        item("equity_vesting_schedule", "equity vesting schedule", True,
             "What is the vesting schedule for the equity grant (total years and cliff)?")
    elif not comp.equity.is_known:
        item("equity", "equity", True,
             "Does the offer include equity? If so, what is the grant and vesting schedule?")
    if offer.employment_type is None:
        item("employment_type", "employment type", True,
             "Is this a full-time employee position or a contract role?")
    if loc.remote is True and loc.remote_eligible_countries is None:
        item("remote_geography", "remote geography", True,
             "Which countries can this remote role be performed from?")
    if loc.remote is None:
        item("remote_policy", "remote / on-site policy", c.remote_required,
             "Is this role fully remote, hybrid, or on-site?")
    if c.needs_sponsorship and req.sponsorship_offered is None:
        item("sponsorship", "visa / work-authorization sponsorship", True,
             "Does the company sponsor work authorization for this role?")
    if req.on_call is None and c.on_call_allowed is not True:
        item("on_call_requirement", "on-call requirement", c.on_call_allowed is False,
             "Does this role include an on-call rotation?")
    if req.travel_percent is None:
        item("travel_requirement", "travel requirement", c.max_travel_percent is not None,
             "Roughly what percentage of time does this role require travel?")
    if offer.recruiter.type is RecruiterType.AGENCY and offer.company is None:
        item("hiring_company", "hiring company", True, "Which company is the hiring client?")
    if loc.remote is True and loc.remote_eligible_countries and c.candidate_country is None:
        item("candidate_country", "your work country", True,
             "Set constraints.candidate_country so remote eligibility can be checked.",
             ask_recruiter=False)
    if offer.level is None:
        item("level", "role level", False, "What level or grade is this offer at?")
    if not offer.compensation.sign_on.is_known:
        item("sign_on", "sign-on bonus", False, "Is there a sign-on bonus?")
    if offer.recruiter.offer_deadline is None:
        item("offer_deadline", "offer deadline", False, "When do you need a decision by?")
    if offer.batna is None:
        item("batna", "your alternatives (batna)", False,
             "Add a batna section (competing offers, interviews, employment) to assess leverage.",
             ask_recruiter=False)

    return tuple(sorted(items, key=lambda m: not m.critical))


def next_question(items: tuple[MissingItem, ...]) -> str | None:
    """The first critical question for the recruiter, else the first critical item at all."""
    critical = [m for m in items if m.critical]
    for m in critical:
        if m.ask_recruiter:
            return m.question
    return critical[0].question if critical else None

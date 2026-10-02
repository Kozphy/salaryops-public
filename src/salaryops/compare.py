"""Side-by-side comparison of up to three analyzed offers.

Dimensions are reported independently; there is deliberately no combined score.
Money is converted only with exchange rates the user supplies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .decision import Analysis
from .models import Amount, Failure, FailureClass, SalaryOpsError
from .policies import DecisionState, Outcome, PolicyResult

MAX_OFFERS = 3
MINIMUM_POLICIES = ("POLICY-006", "POLICY-007", "POLICY-009")


@dataclass(frozen=True)
class OfferColumn:
    label: str
    source_currency: str
    rate: Decimal
    base: Decimal
    recurring_floor: Decimal
    recurring_complete: bool
    year1_floor: Decimal
    year1_complete: bool
    equity: Decimal | None
    remote: str
    travel: str
    on_call: str
    location: str
    constraint_violations: tuple[str, ...]
    critical_missing: tuple[str, ...]
    band_category: str
    compa_ratio: Decimal | None
    decision_state: DecisionState

    def to_dict(self) -> dict[str, Any]:
        def s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)

        return {
            "label": self.label,
            "source_currency": self.source_currency,
            "fx_rate": str(self.rate),
            "base": s(self.base),
            "recurring_tc_floor": s(self.recurring_floor),
            "recurring_tc_complete": self.recurring_complete,
            "year1_tc_floor": s(self.year1_floor),
            "year1_tc_complete": self.year1_complete,
            "annualized_equity": s(self.equity),
            "remote": self.remote,
            "travel": self.travel,
            "on_call": self.on_call,
            "location": self.location,
            "constraint_violations": list(self.constraint_violations),
            "critical_missing": list(self.critical_missing),
            "band_category": self.band_category,
            "compa_ratio": s(self.compa_ratio),
            "decision_state": self.decision_state.value,
        }


@dataclass(frozen=True)
class Comparison:
    currency: str
    columns: tuple[OfferColumn, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"currency": self.currency, "offers": [c.to_dict() for c in self.columns], "combined_score": None}


def _fit(r: PolicyResult, raw: str) -> str:
    verdict = {Outcome.TRIGGERED: "CONFLICT", Outcome.NOT_EVALUABLE: "unknown", Outcome.PASSED: "ok"}[r.outcome]
    return f"{verdict} ({raw})"


def _rate(currency: str, target: str, fx: dict[str, Decimal]) -> Decimal:
    if currency == target:
        return Decimal(1)
    if currency not in fx:
        raise SalaryOpsError(Failure(
            FailureClass.CURRENCY_MISMATCH,
            f"offers use {currency} and {target}; pass --fx {currency}=<{target} per 1 {currency}> to compare",
        ))
    return fx[currency]


def _column(label: str, a: Analysis, target: str, fx: dict[str, Decimal]) -> OfferColumn:
    comp, offer = a.compensation, a.offer
    rate = _rate(comp.currency, target, fx)
    by_id = {r.policy_id: r for r in a.policies}
    loc = offer.location
    remote_raw = {True: "remote", False: "on-site", None: "remote?"}[loc.remote]
    place = ", ".join(p for p in (loc.city, loc.country) if p) or "location unknown"
    travel = offer.requirements.travel_percent
    on_call = offer.requirements.on_call
    violations = tuple(
        f"{r.policy_id}: {r.reason}"
        for r in a.policies
        if r.triggered and (r.state is DecisionState.CONSTRAINT_CONFLICT or r.policy_id in MINIMUM_POLICIES)
    )

    def conv(v: Decimal) -> Decimal:
        return v * rate

    def conv_amount(x: Amount) -> Decimal | None:
        return None if x.value is None else conv(x.value)

    return OfferColumn(
        label=label,
        source_currency=comp.currency,
        rate=rate,
        base=conv(comp.base),
        recurring_floor=conv(comp.recurring_floor),
        recurring_complete=comp.recurring_complete,
        year1_floor=conv(comp.year1_floor),
        year1_complete=comp.year1_complete,
        equity=conv_amount(comp.equity),
        remote=_fit(by_id["POLICY-003"], remote_raw),
        travel=_fit(by_id["POLICY-005"], "unknown" if travel is None else f"{travel}%"),
        on_call=_fit(by_id["POLICY-004"], {True: "required", False: "none", None: "unknown"}[on_call]),
        location=place,
        constraint_violations=violations,
        critical_missing=tuple(m.field for m in a.critical_missing),
        band_category=a.band.category.value,
        compa_ratio=a.band.compa_ratio,
        decision_state=a.decision.state,
    )


def compare(
    analyses: list[tuple[str, Analysis]],
    fx: dict[str, Decimal] | None = None,
    currency: str | None = None,
) -> Comparison:
    if not 2 <= len(analyses) <= MAX_OFFERS:
        raise SalaryOpsError(Failure(FailureClass.INPUT_INVALID, f"compare takes 2 to {MAX_OFFERS} offers"))
    target = (currency or analyses[0][1].compensation.currency).upper()
    fx = {k.upper(): v for k, v in (fx or {}).items()}
    return Comparison(target, tuple(_column(label, a, target, fx) for label, a in analyses))


def parse_fx(pairs: list[str]) -> dict[str, Decimal]:
    """Parse ["USD=31.5"] into {"USD": Decimal("31.5")}."""
    out: dict[str, Decimal] = {}
    for pair in pairs:
        code, sep, value = pair.partition("=")
        try:
            rate = Decimal(value)
        except InvalidOperation:
            rate = Decimal(0)
        if not sep or len(code.strip()) != 3 or rate <= 0:
            raise SalaryOpsError(Failure(FailureClass.INPUT_INVALID, f"--fx must look like USD=31.5, got {pair!r}"))
        out[code.strip().upper()] = rate
    return out

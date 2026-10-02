"""Total-compensation normalization.

Unknown components are never treated as zero. Totals are reported as a known floor plus
the names of components that could raise it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .models import Amount, AmountStatus, Compensation

CENT = Decimal("0.01")


@dataclass(frozen=True)
class CompResult:
    currency: str
    base: Decimal
    bonus: Amount
    equity: Amount
    equity_basis: str
    sign_on: Amount
    benefits: Amount
    recurring_floor: Decimal
    year1_floor: Decimal
    unknown_recurring: tuple[str, ...]
    unknown_one_time: tuple[str, ...]

    @property
    def recurring_complete(self) -> bool:
        return not self.unknown_recurring

    @property
    def year1_complete(self) -> bool:
        return not self.unknown_recurring and not self.unknown_one_time

    @property
    def vesting_schedule_missing(self) -> bool:
        return self.equity_basis == "grant without vesting schedule"

    def to_dict(self) -> dict[str, Any]:
        def amt(a: Amount) -> dict[str, Any]:
            return {"status": a.status.value, "value": None if a.value is None else str(a.value)}

        return {
            "currency": self.currency,
            "base": str(self.base),
            "bonus": amt(self.bonus),
            "equity": amt(self.equity),
            "equity_basis": self.equity_basis,
            "sign_on": amt(self.sign_on),
            "benefits_value_not_in_tc": amt(self.benefits),
            "recurring_tc_floor": str(self.recurring_floor),
            "recurring_tc_complete": self.recurring_complete,
            "year1_tc_floor": str(self.year1_floor),
            "year1_tc_complete": self.year1_complete,
            "unknown_recurring": list(self.unknown_recurring),
            "unknown_one_time": list(self.unknown_one_time),
        }


def annualize_equity(comp: Compensation) -> tuple[Amount, str]:
    if comp.annualized_equity.is_known:
        return comp.annualized_equity, "annualized"
    grant = comp.equity_grant
    if grant.status is AmountStatus.KNOWN_ZERO:
        return grant, "no equity grant"
    if grant.status is AmountStatus.KNOWN_VALUE:
        if comp.vesting_years is None:
            return Amount.unknown(), "grant without vesting schedule"
        assert grant.value is not None
        per_year = (grant.value / comp.vesting_years).quantize(CENT, rounding=ROUND_HALF_UP)
        return Amount.of(per_year), f"grant / {comp.vesting_years} years"
    return Amount.unknown(), "unknown"


def compute_compensation(comp: Compensation) -> CompResult:
    equity, basis = annualize_equity(comp)
    recurring = {"bonus": comp.bonus, "equity": equity}
    one_time = {"sign_on": comp.sign_on}

    recurring_floor = comp.base + sum(
        (a.value for a in recurring.values() if a.value is not None), Decimal(0)
    )
    year1_floor = recurring_floor + sum(
        (a.value for a in one_time.values() if a.value is not None), Decimal(0)
    )
    return CompResult(
        currency=comp.currency,
        base=comp.base,
        bonus=comp.bonus,
        equity=equity,
        equity_basis=basis,
        sign_on=comp.sign_on,
        benefits=comp.benefits_value,
        recurring_floor=recurring_floor,
        year1_floor=year1_floor,
        unknown_recurring=tuple(k for k, a in recurring.items() if not a.is_known),
        unknown_one_time=tuple(k for k, a in one_time.items() if not a.is_known),
    )

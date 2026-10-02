"""Total-compensation normalization.

Unknown components are never treated as zero. Totals are reported as a known floor plus
the names of components that could raise it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .models import Amount, AmountStatus, Compensation, EquityKind

CENT = Decimal("0.01")
BASIS_NO_VESTING = "grant without vesting schedule"
BASIS_NO_DISCOUNT = "private equity without valuation discount"


@dataclass(frozen=True)
class CompResult:
    currency: str
    base: Decimal
    bonus: Amount
    equity: Amount
    equity_basis: str
    equity_gross: Amount
    year1_equity: Amount
    vesting_cliff_months: int | None
    equity_refresh: Amount
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
        return self.equity_basis == BASIS_NO_VESTING

    @property
    def valuation_discount_missing(self) -> bool:
        return self.equity_basis == BASIS_NO_DISCOUNT

    @property
    def cliff_blocks_year1(self) -> bool:
        return self.vesting_cliff_months is not None and self.vesting_cliff_months > 12

    def to_dict(self) -> dict[str, Any]:
        def amt(a: Amount) -> dict[str, Any]:
            return {"status": a.status.value, "value": None if a.value is None else str(a.value)}

        return {
            "currency": self.currency,
            "base": str(self.base),
            "bonus": amt(self.bonus),
            "equity": amt(self.equity),
            "equity_basis": self.equity_basis,
            "equity_gross": amt(self.equity_gross),
            "year1_equity": amt(self.year1_equity),
            "vesting_cliff_months": self.vesting_cliff_months,
            "equity_refresh_not_in_tc": amt(self.equity_refresh),
            "sign_on": amt(self.sign_on),
            "benefits_value_not_in_tc": amt(self.benefits),
            "recurring_tc_floor": str(self.recurring_floor),
            "recurring_tc_complete": self.recurring_complete,
            "year1_tc_floor": str(self.year1_floor),
            "year1_tc_complete": self.year1_complete,
            "unknown_recurring": list(self.unknown_recurring),
            "unknown_one_time": list(self.unknown_one_time),
        }


def gross_equity(comp: Compensation) -> tuple[Amount, str]:
    """Annual equity at the stated (paper) value."""
    if comp.annualized_equity.is_known:
        return comp.annualized_equity, "annualized"
    grant = comp.equity_grant
    if grant.status is AmountStatus.KNOWN_ZERO:
        return grant, "no equity grant"
    if grant.status is AmountStatus.KNOWN_VALUE:
        if comp.vesting_years is None:
            return Amount.unknown(), BASIS_NO_VESTING
        assert grant.value is not None
        per_year = (grant.value / comp.vesting_years).quantize(CENT, rounding=ROUND_HALF_UP)
        return Amount.of(per_year), f"grant / {comp.vesting_years} years"
    return Amount.unknown(), "unknown"


def annualize_equity(comp: Compensation) -> tuple[Amount, str]:
    """Annual equity after the valuation discount.

    Private-company equity without a stated discount is UNKNOWN rather than paper value, so
    the TC floor is never overstated. ``equity_discount: 0`` accepts paper value explicitly.
    """
    gross, basis = gross_equity(comp)
    if gross.status is not AmountStatus.KNOWN_VALUE:
        return gross, basis
    assert gross.value is not None
    if comp.equity_discount is not None:
        net = (gross.value * (1 - comp.equity_discount)).quantize(CENT, rounding=ROUND_HALF_UP)
        pct = f"{(comp.equity_discount * 100).normalize():f}"
        return Amount.of(net), f"{basis}, {pct}% valuation discount"
    if comp.equity_kind is EquityKind.PRIVATE:
        return Amount.unknown(), BASIS_NO_DISCOUNT
    return gross, basis


def year1_equity(equity: Amount, cliff_months: int | None) -> Amount:
    """Equity vesting in the first 12 months: nothing if the cliff is longer than a year."""
    if equity.is_known and cliff_months is not None and cliff_months > 12:
        return Amount.of(0)
    return equity


def compute_compensation(comp: Compensation) -> CompResult:
    equity, basis = annualize_equity(comp)
    gross, _ = gross_equity(comp)
    first_year_equity = year1_equity(equity, comp.vesting_cliff_months)
    recurring = {"bonus": comp.bonus, "equity": equity}
    one_time = {"sign_on": comp.sign_on}

    def known_sum(amounts: list[Amount]) -> Decimal:
        return sum((a.value for a in amounts if a.value is not None), Decimal(0))

    return CompResult(
        currency=comp.currency,
        base=comp.base,
        bonus=comp.bonus,
        equity=equity,
        equity_basis=basis,
        equity_gross=gross,
        year1_equity=first_year_equity,
        vesting_cliff_months=comp.vesting_cliff_months,
        equity_refresh=comp.equity_refresh_annual,
        sign_on=comp.sign_on,
        benefits=comp.benefits_value,
        recurring_floor=comp.base + known_sum(list(recurring.values())),
        year1_floor=comp.base + known_sum([comp.bonus, first_year_equity, comp.sign_on]),
        unknown_recurring=tuple(k for k, a in recurring.items() if not a.is_known),
        unknown_one_time=tuple(k for k, a in one_time.items() if not a.is_known),
    )

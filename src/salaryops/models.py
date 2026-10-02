"""Input schema, failure taxonomy, and policy-threshold settings.

Everything a user can type lives here. Analysis modules consume these models and
never re-parse YAML themselves.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class FailureClass(StrEnum):
    """Reasons the system could not (fully) analyze an input.

    These are system failures, not negotiation findings: a constraint conflict is a
    finding and is reported as a decision state, never as a failure.
    """

    INPUT_INVALID = "INPUT_INVALID"
    SALARY_BAND_INVALID = "SALARY_BAND_INVALID"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    COMPENSATION_INCOMPLETE = "COMPENSATION_INCOMPLETE"
    MARKET_DATA_MISSING = "MARKET_DATA_MISSING"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    INVALID_TRANSITION = "INVALID_TRANSITION"


FATAL_FAILURES = frozenset(
    {FailureClass.INPUT_INVALID, FailureClass.SALARY_BAND_INVALID, FailureClass.CURRENCY_MISMATCH}
)


@dataclass(frozen=True)
class Failure:
    failure_class: FailureClass
    message: str
    field: str | None = None

    @property
    def fatal(self) -> bool:
        return self.failure_class in FATAL_FAILURES

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"failure_class": self.failure_class.value, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


class SalaryOpsError(Exception):
    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.message)
        self.failure = failure

    def to_dict(self) -> dict[str, Any]:
        return self.failure.to_dict()


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"


class RecruiterType(StrEnum):
    IN_HOUSE = "in_house"
    AGENCY = "agency"
    UNKNOWN = "unknown"


class BandSource(StrEnum):
    RECRUITER = "recruiter"
    PUBLISHED = "published"
    MARKET_SURVEY = "market_survey"
    SELF_ESTIMATE = "self_estimate"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AmountStatus(StrEnum):
    KNOWN_VALUE = "KNOWN_VALUE"
    KNOWN_ZERO = "KNOWN_ZERO"
    UNKNOWN = "UNKNOWN"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Amount(_Model):
    """A money amount that distinguishes "zero" from "nobody told me".

    YAML input is a plain number, ``0``, ``unknown``, ``null``, or the field is omitted.
    Omitted, ``null`` and ``unknown`` all mean UNKNOWN; only an explicit 0 is KNOWN_ZERO.
    """

    status: AmountStatus
    value: Decimal | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, raw: Any) -> Any:
        if isinstance(raw, Amount):
            return raw.model_dump()
        if isinstance(raw, dict):
            return raw
        if raw is None or (isinstance(raw, str) and raw.strip().lower() == "unknown"):
            return {"status": AmountStatus.UNKNOWN}
        if isinstance(raw, bool):
            raise ValueError("amount must be a number or 'unknown'")
        try:
            value = Decimal(str(raw).replace(",", "").strip())
        except InvalidOperation:
            raise ValueError(f"amount must be a number or 'unknown', got {raw!r}") from None
        if not value.is_finite():
            raise ValueError("amount must be finite")
        if value < 0:
            raise ValueError("amount cannot be negative")
        if value == 0:
            return {"status": AmountStatus.KNOWN_ZERO, "value": Decimal(0)}
        return {"status": AmountStatus.KNOWN_VALUE, "value": value}

    @model_validator(mode="after")
    def _consistent(self) -> Amount:
        if self.status is AmountStatus.UNKNOWN and self.value is not None:
            raise ValueError("an UNKNOWN amount cannot carry a value")
        if self.status is not AmountStatus.UNKNOWN and self.value is None:
            raise ValueError("a known amount needs a value")
        return self

    @classmethod
    def unknown(cls) -> Amount:
        return cls(status=AmountStatus.UNKNOWN)

    @classmethod
    def of(cls, value: Decimal | int | str) -> Amount:
        return cls.model_validate(value)

    @property
    def is_known(self) -> bool:
        return self.status is not AmountStatus.UNKNOWN


def _upper_currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"currency must be a 3-letter ISO code, got {value!r}")
    return code


class Location(_Model):
    country: str | None = None
    city: str | None = None
    remote: bool | None = None
    remote_eligible_countries: list[str] | None = None


class Compensation(_Model):
    currency: str
    base: Decimal = Field(gt=0)
    bonus: Amount = Field(default_factory=Amount.unknown)
    annualized_equity: Amount = Field(default_factory=Amount.unknown)
    equity_grant: Amount = Field(default_factory=Amount.unknown)
    vesting_years: Decimal | None = Field(default=None, gt=0)
    sign_on: Amount = Field(default_factory=Amount.unknown)
    benefits_value: Amount = Field(default_factory=Amount.unknown)

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return _upper_currency(v)

    @model_validator(mode="after")
    def _one_equity_form(self) -> Compensation:
        if self.annualized_equity.is_known and self.equity_grant.is_known:
            raise ValueError(
                "give either annualized_equity or equity_grant + vesting_years, not both"
            )
        return self


class RoleRequirements(_Model):
    on_call: bool | None = None
    travel_percent: Decimal | None = Field(default=None, ge=0, le=100)
    sponsorship_offered: bool | None = None


class SalaryBand(_Model):
    low: Decimal = Field(gt=0)
    midpoint: Decimal | None = None
    high: Decimal = Field(gt=0)
    currency: str | None = None
    source: BandSource
    confidence: Confidence

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else _upper_currency(v)

    @model_validator(mode="after")
    def _ordered(self) -> SalaryBand:
        if self.low >= self.high:
            raise ValueError("low must be below high")
        if self.midpoint is not None and not self.low <= self.midpoint <= self.high:
            raise ValueError("midpoint must be between low and high")
        return self

    @property
    def midpoint_derived(self) -> bool:
        return self.midpoint is None

    @property
    def effective_midpoint(self) -> Decimal:
        return self.midpoint if self.midpoint is not None else (self.low + self.high) / 2


class RecruiterContext(_Model):
    type: RecruiterType = RecruiterType.UNKNOWN
    offer_deadline: date | None = None


class Batna(_Model):
    competing_offers: int = Field(default=0, ge=0)
    late_stage_interviews: int = Field(default=0, ge=0)
    currently_employed: bool | None = None
    freelance_income: bool = False


class Constraints(_Model):
    candidate_country: str | None = None
    minimum_base: Decimal | None = Field(default=None, ge=0)
    minimum_total_comp: Decimal | None = Field(default=None, ge=0)
    remote_required: bool = False
    acceptable_locations: list[str] = Field(default_factory=list)
    max_travel_percent: Decimal | None = Field(default=None, ge=0, le=100)
    on_call_allowed: bool | None = None
    needs_sponsorship: bool = False


class OfferInput(_Model):
    session: str | None = None
    as_of: date | None = None
    company: str | None = None
    role: str = Field(min_length=1)
    level: str | None = None
    employment_type: EmploymentType | None = None
    location: Location = Field(default_factory=Location)
    compensation: Compensation
    requirements: RoleRequirements = Field(default_factory=RoleRequirements)
    employer_final_offer: bool = False
    salary_band: SalaryBand | None = None
    recruiter: RecruiterContext = Field(default_factory=RecruiterContext)
    batna: Batna | None = None
    constraints: Constraints = Field(default_factory=Constraints)


class LeveragePoints(_Model):
    competing_offer: int = 2
    multiple_late_stage_interviews: int = 1
    currently_employed: int = 1
    freelance_income: int = 1
    hard_deadline: int = -1
    no_alternatives: int = -1


class PolicySettings(_Model):
    """Thresholds the policy functions read. Loaded from an optional YAML overlay.

    The policies themselves stay in Python; this file only tunes numbers.
    """

    near_midpoint_tolerance: Decimal = Field(default=Decimal("0.05"), ge=0, le=Decimal("0.5"))
    late_stage_interviews_threshold: int = Field(default=2, ge=1)
    hard_deadline_days: int = Field(default=3, ge=0)
    deadline_pressure_days: int = Field(default=2, ge=0)
    moderate_leverage_min_points: int = 1
    strong_leverage_min_points: int = 3
    leverage_points: LeveragePoints = Field(default_factory=LeveragePoints)

    @model_validator(mode="after")
    def _levels_ordered(self) -> PolicySettings:
        if self.moderate_leverage_min_points >= self.strong_leverage_min_points:
            raise ValueError("moderate_leverage_min_points must be below strong_leverage_min_points")
        return self

    def sha256(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _format_errors(exc: ValidationError) -> tuple[str, str | None]:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    first = exc.errors()[0]["loc"]
    return "; ".join(parts), (".".join(str(p) for p in first) or None)


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SalaryOpsError(Failure(FailureClass.INPUT_INVALID, f"cannot read {path}: {exc.strerror}")) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SalaryOpsError(Failure(FailureClass.INPUT_INVALID, f"{path} is not valid YAML: {exc}")) from exc


def parse_offer(data: Any) -> OfferInput:
    """Validate a raw mapping into an OfferInput, mapping errors to the failure taxonomy."""
    if not isinstance(data, dict):
        raise SalaryOpsError(Failure(FailureClass.INPUT_INVALID, "offer file must be a YAML mapping"))
    try:
        offer = OfferInput.model_validate(data)
    except ValidationError as exc:
        message, field = _format_errors(exc)
        cls = (
            FailureClass.SALARY_BAND_INVALID
            if field and field.startswith("salary_band")
            else FailureClass.INPUT_INVALID
        )
        raise SalaryOpsError(Failure(cls, message, field)) from exc

    band = offer.salary_band
    if band is not None and band.currency is not None and band.currency != offer.compensation.currency:
        raise SalaryOpsError(
            Failure(
                FailureClass.CURRENCY_MISMATCH,
                f"salary band is in {band.currency} but the offer is in {offer.compensation.currency}; "
                "convert one of them before analysis",
                "salary_band.currency",
            )
        )
    return offer


def load_offer(path: Path) -> OfferInput:
    return parse_offer(_read_yaml(path))


def load_settings(path: Path | None) -> PolicySettings:
    if path is None:
        return PolicySettings()
    data = _read_yaml(path)
    if data is None:
        return PolicySettings()
    if not isinstance(data, dict):
        raise SalaryOpsError(Failure(FailureClass.INPUT_INVALID, "policy config must be a YAML mapping"))
    try:
        return PolicySettings.model_validate(data)
    except ValidationError as exc:
        message, field = _format_errors(exc)
        raise SalaryOpsError(
            Failure(FailureClass.INPUT_INVALID, f"policy config: {message}", field)
        ) from exc

"""Rule-based leverage from the candidate's alternatives.

Points are additive and every point is listed as evidence. No probabilities: there is no
historical outcome data to calibrate them against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from .models import Batna, PolicySettings


class LeverageLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LeverageResult:
    level: LeverageLevel
    points: int | None
    evidence: tuple[str, ...]
    days_to_deadline: int | None

    @property
    def at_least_moderate(self) -> bool:
        return self.level in (LeverageLevel.MODERATE, LeverageLevel.STRONG)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "points": self.points,
            "evidence": list(self.evidence),
            "days_to_deadline": self.days_to_deadline,
        }


def days_until(deadline: date | None, as_of: date) -> int | None:
    return None if deadline is None else (deadline - as_of).days


def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def assess_leverage(batna: Batna | None, days_to_deadline: int | None, settings: PolicySettings) -> LeverageResult:
    if batna is None:
        return LeverageResult(
            LeverageLevel.UNKNOWN,
            None,
            ("no batna section provided; policies treat unknown leverage as LOW",),
            days_to_deadline,
        )

    pts = settings.leverage_points
    total = 0
    evidence: list[str] = []

    def add(points: int, text: str) -> None:
        nonlocal total
        total += points
        evidence.append(f"{text} ({_signed(points)})")

    if batna.competing_offers:
        plural = "s" if batna.competing_offers > 1 else ""
        add(pts.competing_offer, f"{batna.competing_offers} competing offer{plural}")
    if batna.late_stage_interviews >= settings.late_stage_interviews_threshold:
        add(pts.multiple_late_stage_interviews, f"{batna.late_stage_interviews} late-stage interviews")
    elif batna.late_stage_interviews:
        add(0, f"{batna.late_stage_interviews} late-stage interview, below threshold of "
               f"{settings.late_stage_interviews_threshold}")
    if batna.currently_employed:
        add(pts.currently_employed, "currently employed")
    elif batna.currently_employed is False:
        add(0, "not currently employed")
    else:
        add(0, "current employment not stated")
    if batna.freelance_income:
        add(pts.freelance_income, "freelance income available")

    if days_to_deadline is None:
        add(0, "offer deadline unknown")
    elif days_to_deadline <= settings.hard_deadline_days:
        when = f"in {days_to_deadline} days" if days_to_deadline >= 0 else f"passed {-days_to_deadline} days ago"
        add(pts.hard_deadline, f"hard offer deadline {when}")
    else:
        add(0, f"no urgent deadline ({days_to_deadline} days)")

    has_alternative = (
        batna.competing_offers > 0
        or batna.late_stage_interviews > 0
        or bool(batna.currently_employed)
        or batna.freelance_income
    )
    if not has_alternative:
        add(pts.no_alternatives, "no alternatives: no offers, interviews, current job or freelance income")

    if total >= settings.strong_leverage_min_points:
        level = LeverageLevel.STRONG
    elif total >= settings.moderate_leverage_min_points:
        level = LeverageLevel.MODERATE
    else:
        level = LeverageLevel.LOW
    return LeverageResult(level, total, tuple(evidence), days_to_deadline)

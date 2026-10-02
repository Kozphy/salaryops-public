"""Salary-band position metrics.

The categories are internal decision-support buckets, not an HR standard.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from .models import SalaryBand

RATIO = Decimal("0.0001")


class BandCategory(StrEnum):
    BELOW_BAND = "BELOW_BAND"
    LOWER_BAND = "LOWER_BAND"
    NEAR_MIDPOINT = "NEAR_MIDPOINT"
    UPPER_BAND = "UPPER_BAND"
    ABOVE_BAND = "ABOVE_BAND"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BandResult:
    category: BandCategory
    low: Decimal | None = None
    midpoint: Decimal | None = None
    high: Decimal | None = None
    midpoint_derived: bool = False
    band_position: Decimal | None = None
    compa_ratio: Decimal | None = None
    distance_to_midpoint: Decimal | None = None
    distance_to_high: Decimal | None = None
    source: str | None = None
    confidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        def s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)

        return {
            "category": self.category.value,
            "low": s(self.low),
            "midpoint": s(self.midpoint),
            "high": s(self.high),
            "midpoint_derived": self.midpoint_derived,
            "band_position": s(self.band_position),
            "compa_ratio": s(self.compa_ratio),
            "distance_to_midpoint": s(self.distance_to_midpoint),
            "distance_to_high": s(self.distance_to_high),
            "source": self.source,
            "confidence": self.confidence,
        }


def classify(base: Decimal, low: Decimal, midpoint: Decimal, high: Decimal, tolerance: Decimal) -> BandCategory:
    if base < low:
        return BandCategory.BELOW_BAND
    if base > high:
        return BandCategory.ABOVE_BAND
    if abs(base / midpoint - 1) <= tolerance:
        return BandCategory.NEAR_MIDPOINT
    return BandCategory.LOWER_BAND if base < midpoint else BandCategory.UPPER_BAND


def analyze_band(base: Decimal, band: SalaryBand | None, tolerance: Decimal) -> BandResult:
    """Position of base salary within the band.

    band_position is 0 at low and 1 at high (negative or >1 outside the band).
    Distances are band value minus base, so positive means the offer is below that point.
    """
    if band is None:
        return BandResult(category=BandCategory.UNKNOWN)
    mid = band.effective_midpoint
    return BandResult(
        category=classify(base, band.low, mid, band.high, tolerance),
        low=band.low,
        midpoint=mid,
        high=band.high,
        midpoint_derived=band.midpoint_derived,
        band_position=((base - band.low) / (band.high - band.low)).quantize(RATIO, rounding=ROUND_HALF_UP),
        compa_ratio=(base / mid).quantize(RATIO, rounding=ROUND_HALF_UP),
        distance_to_midpoint=mid - base,
        distance_to_high=band.high - base,
        source=band.source.value,
        confidence=band.confidence.value,
    )

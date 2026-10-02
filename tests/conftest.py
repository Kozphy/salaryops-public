from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from salaryops.models import OfferInput, parse_offer

AS_OF = date(2026, 10, 2)

# A complete offer with nothing missing and nothing in conflict. Tests override fields.
BASE_OFFER: dict[str, Any] = {
    "as_of": AS_OF.isoformat(),
    "company": "Example Corp",
    "role": "Data Analyst",
    "level": "L3",
    "employment_type": "full_time",
    "location": {"country": "Taiwan", "remote": False},
    "compensation": {
        "currency": "TWD",
        "base": 1650000,
        "bonus": 165000,
        "annualized_equity": 0,
        "sign_on": 0,
    },
    "requirements": {"on_call": False, "travel_percent": 5},
    "salary_band": {
        "low": 1400000,
        "midpoint": 1650000,
        "high": 1900000,
        "source": "recruiter",
        "confidence": "high",
    },
    "recruiter": {"type": "in_house", "offer_deadline": "2026-10-16"},
    "batna": {"competing_offers": 0, "late_stage_interviews": 0, "currently_employed": True},
    "constraints": {
        "candidate_country": "Taiwan",
        "minimum_base": 1400000,
        "minimum_total_comp": 1600000,
        "acceptable_locations": ["Taiwan"],
        "on_call_allowed": False,
        "max_travel_percent": 10,
    },
}

DELETE = object()


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge override into a copy of base. A value of DELETE removes the key."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if value is DELETE:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def make_input(**override: Any) -> OfferInput:
    return parse_offer(merge(BASE_OFFER, override))


@pytest.fixture
def base_offer() -> OfferInput:
    return make_input()

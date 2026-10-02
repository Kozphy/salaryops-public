"""Country-name normalization to ISO 3166-1 alpha-2 codes.

Places are compared by code, so "USA", "US" and "United States" are the same country.
Names that are not recognized as countries (cities, regions, typos) fall back to
case-insensitive text comparison. Two-letter inputs are read as ISO codes ("CA" is Canada).
"""

from __future__ import annotations

from functools import cache

import pycountry

# Everyday names that pycountry's lookup does not resolve.
ALIASES: dict[str, str] = {
    "america": "US",
    "uk": "GB",
    "britain": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "korea": "KR",
    "russia": "RU",
    "macau": "MO",
    "holland": "NL",
    "uae": "AE",
    "turkey": "TR",
    "turkiye": "TR",
    "ivory coast": "CI",
    "republic of china": "TW",
}


def _normalize(name: str) -> str:
    return " ".join(name.split()).casefold().removeprefix("the ")


@cache
def country_code(name: str) -> str | None:
    """ISO alpha-2 code for a country name or code, or None if it is not a known country."""
    key = _normalize(name)
    if key in ALIASES:
        return ALIASES[key]
    try:
        return str(pycountry.countries.lookup(key).alpha_2)
    except LookupError:
        return None


def place_key(name: str) -> str:
    """Comparison key: the ISO code for a recognized country, else the normalized text."""
    return country_code(name) or _normalize(name)


def same_place(a: str, b: str) -> bool:
    return place_key(a) == place_key(b)

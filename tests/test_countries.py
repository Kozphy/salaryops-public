import pytest

from salaryops.countries import country_code, place_key, same_place


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("USA", "US"),
        ("us", "US"),
        ("United States of America", "US"),
        ("  united   states ", "US"),
        ("UK", "GB"),
        ("England", "GB"),
        ("United Kingdom", "GB"),
        ("Taiwan", "TW"),
        ("South Korea", "KR"),
        ("The Netherlands", "NL"),
        ("Holland", "NL"),
        ("UAE", "AE"),
    ],
)
def test_country_code(name, code):
    assert country_code(name) == code


@pytest.mark.parametrize("name", ["Taipei", "Remote", "Atlantis"])
def test_non_countries_have_no_code(name):
    assert country_code(name) is None


def test_place_key_falls_back_to_normalized_text():
    assert place_key("  Taipei ") == "taipei"
    assert same_place("TAIPEI", "taipei")
    assert same_place("USA", "United States")
    assert not same_place("Taipei", "Taiwan")

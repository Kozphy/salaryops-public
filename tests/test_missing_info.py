from conftest import DELETE, make_input
from salaryops.compensation import compute_compensation
from salaryops.missing_info import detect_missing, next_question


def missing(offer):
    return detect_missing(offer, compute_compensation(offer.compensation))


def fields(offer, critical=None):
    return [m.field for m in missing(offer) if critical is None or m.critical is critical]


def test_complete_offer_has_nothing_missing(base_offer):
    assert fields(base_offer) == []


def test_unknown_band_question_depends_on_recruiter_type():
    in_house = missing(make_input(salary_band=DELETE))
    agency = missing(make_input(salary_band=DELETE, recruiter={"type": "agency"}))
    assert in_house[0].field == agency[0].field == "salary_band"
    assert "your client" in agency[0].question
    assert "your client" not in in_house[0].question


def test_unknown_comp_components_are_critical():
    offer = make_input(compensation={"bonus": DELETE, "annualized_equity": DELETE})
    assert fields(offer, critical=True) == ["bonus", "equity"]


def test_grant_without_vesting_asks_for_schedule_not_equity():
    offer = make_input(compensation={"annualized_equity": DELETE, "equity_grant": 400000})
    assert fields(offer, critical=True) == ["equity_vesting_schedule"]


def test_constraint_makes_requirement_critical():
    offer = make_input(requirements={"on_call": None, "travel_percent": None})
    assert fields(offer, critical=True) == ["on_call_requirement", "travel_requirement"]
    relaxed = make_input(
        requirements={"on_call": None, "travel_percent": None},
        constraints={"on_call_allowed": None, "max_travel_percent": None},
    )
    assert fields(relaxed, critical=False) == ["on_call_requirement", "travel_requirement"]


def test_on_call_irrelevant_when_allowed():
    offer = make_input(requirements={"on_call": None}, constraints={"on_call_allowed": True})
    assert "on_call_requirement" not in fields(offer)


def test_remote_role_without_geography():
    offer = make_input(location={"remote": True})
    assert fields(offer, critical=True) == ["remote_geography"]


def test_candidate_country_needed_for_remote_eligibility_check():
    offer = make_input(
        location={"remote": True, "remote_eligible_countries": ["Taiwan"]},
        constraints={"candidate_country": None},
    )
    items = missing(offer)
    assert [m.field for m in items] == ["candidate_country"]
    assert not items[0].ask_recruiter


def test_sponsorship_and_agency_company():
    offer = make_input(
        company=None,
        recruiter={"type": "agency"},
        constraints={"needs_sponsorship": True},
    )
    assert fields(offer, critical=True) == ["sponsorship", "hiring_company"]


def test_optional_items_listed_after_critical():
    offer = make_input(level=None, employment_type=None, batna=DELETE, recruiter={"offer_deadline": None})
    assert fields(offer) == ["employment_type", "level", "offer_deadline", "batna"]


def test_next_question_prefers_recruiter_questions():
    offer = make_input(
        employment_type=None,
        location={"remote": True, "remote_eligible_countries": ["Taiwan"]},
        constraints={"candidate_country": None},
    )
    items = missing(offer)
    assert items[0].field == "employment_type"
    assert next_question(items) == items[0].question


def test_next_question_none_when_only_optional():
    assert next_question(missing(make_input(level=None))) is None

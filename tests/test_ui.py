from pathlib import Path

import pytest

from salaryops import audit

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

UI = Path(__file__).resolve().parent.parent / "src" / "salaryops" / "ui.py"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def example(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


def upload(name: str) -> tuple[str, bytes, str]:
    return name, (EXAMPLES / name).read_bytes(), "application/x-yaml"


@pytest.fixture
def app() -> AppTest:
    at = AppTest.from_file(str(UI), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def headline(at: AppTest) -> str:
    return next(m.value for m in at.markdown if m.value.startswith("## "))


def test_default_template_needs_more_information(app):
    assert "NEED_MORE_INFORMATION" in headline(app)
    assert app.info[0].value.startswith("Next question:")
    assert {m.label for m in app.metric} >= {"Base", "Recurring TC (floor)", "Year-1 TC (floor)"}
    assert any("MARKET_DATA_MISSING" in w.value for w in app.warning)


def test_editing_yaml_reanalyzes(app):
    app.text_area(key="offer_text").set_value(example("offer_counter.yaml")).run()
    assert "COUNTER" in headline(app)
    assert any(m.label == "Compa ratio" for m in app.metric)
    assert not app.error


def test_uploaded_offer_replaces_editor(app):
    app.file_uploader(key="upload").set_value(upload("offer_startup.yaml")).run()
    assert "READY_TO_NEGOTIATE" in headline(app)
    assert app.text_area(key="offer_text").value == (EXAMPLES / "offer_startup.yaml").read_bytes().decode("utf-8")


def test_invalid_yaml_shows_typed_failure(app):
    app.text_area(key="offer_text").set_value("role: [unclosed").run()
    assert "INPUT_INVALID" in app.error[0].value
    assert not any(m.value.startswith("## ") for m in app.markdown)


def test_ui_date_used_when_file_as_of_is_ignored(app):
    app.text_area(key="offer_text").set_value(example("offer_counter.yaml"))
    app.sidebar.checkbox[0].uncheck()
    app.sidebar.date_input[0].set_value("2030-01-01").run()
    assert any("as of 2030-01-01 (ui)" in c.value for c in app.caption)


def test_bad_policy_config_stops_analysis(app):
    app.sidebar.text_area[0].set_value("near_midpoint_tolerance: lots").run()
    assert "policy config" in app.sidebar.error[0].value
    assert not any(m.value.startswith("## ") for m in app.markdown)


def test_record_analysis_in_audit_log(app, tmp_path):
    log = tmp_path / "audit.jsonl"
    app.text_area(key="offer_text").set_value(example("offer_counter.yaml"))
    app.text_input[0].set_value(str(log))
    app.text_input[1].set_value("ui-test")
    app.button[0].click().run()
    assert "Recorded analysis" in app.success[0].value
    assert audit.verify(log).ok
    assert [s.session_id for s in audit.summarize_sessions(audit.read_records(log))] == ["ui-test"]


def test_record_into_corrupt_log_shows_failure(app, tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text("not json\n", encoding="utf-8")
    app.text_input[0].set_value(str(log))
    app.button[0].click().run()
    assert "AUDIT_CORRUPT" in app.error[0].value


def compare_uploader(at: AppTest):
    return next(u for u in at.file_uploader if u.accept_multiple_files)


def test_compare_same_currency(app):
    assert any("no combined score" in c.value for c in app.caption)
    compare_uploader(app).set_value([upload("offer_basic.yaml"), upload("offer_counter.yaml")]).run()
    table = app.dataframe[-1].value
    assert list(table.columns) == ["Dimension", "offer_basic", "offer_counter"]
    assert "Decision" in table["Dimension"].tolist()


def test_compare_needs_fx_across_currencies(app):
    compare_uploader(app).set_value([upload("offer_basic.yaml"), upload("offer_remote_conflict.yaml")]).run()
    assert any("CURRENCY_MISMATCH" in e.value for e in app.error)
    fx = next(t for t in app.text_input if t.label == "Exchange rates")
    fx.set_value("USD=31.5").run()
    assert not app.error
    assert any("offer_remote_conflict: converted from USD at 31.5 TWD per 1 USD" in c.value for c in app.caption)

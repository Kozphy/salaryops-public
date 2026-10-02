import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from salaryops.cli import app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
runner = CliRunner()


@pytest.fixture
def log(tmp_path: Path) -> str:
    return str(tmp_path / "audit.jsonl")


def invoke(*args: str):
    return runner.invoke(app, [str(a) for a in args])


def test_analyze_human_output_and_audit(log):
    r = invoke("analyze", EXAMPLES / "offer_basic.yaml", "--audit-log", log)
    assert r.exit_code == 0, r.output
    assert "NEED_MORE_INFORMATION" in r.output
    assert "Compa ratio" in r.output
    assert invoke("audit", "verify", "--audit-log", log).exit_code == 0


def test_analyze_json(log):
    r = invoke("analyze", EXAMPLES / "offer_counter.yaml", "--json", "--audit-log", log)
    data = json.loads(r.output)
    assert data["decision_state"] == "COUNTER"
    assert data["session_id"] == "offer_counter"
    assert data["analysis_id"]


def test_analyze_no_audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    r = invoke("analyze", EXAMPLES / "offer_basic.yaml", "--no-audit", "--audit-log", path)
    assert r.exit_code == 0
    assert not path.exists()


def test_fatal_input_exits_2(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("role: X\ncompensation: {currency: TWD, base: 1}\n"
                   "salary_band: {low: 5, high: 3, source: recruiter, confidence: high}\n", encoding="utf-8")
    r = invoke("analyze", bad, "--json", "--no-audit")
    assert r.exit_code == 2
    assert json.loads(r.output)["failure"]["failure_class"] == "SALARY_BAND_INVALID"


def test_as_of_flag_overrides_file(log):
    r = invoke("analyze", EXAMPLES / "offer_counter.yaml", "--json", "--as-of", "2026-10-11", "--audit-log", log)
    assert json.loads(r.output)["leverage"]["days_to_deadline"] == 1
    assert invoke("analyze", EXAMPLES / "offer_counter.yaml", "--as-of", "10/11", "--no-audit").exit_code == 2


def test_analyze_private_equity_detail():
    r = invoke("analyze", EXAMPLES / "offer_startup.yaml", "--no-audit")
    assert r.exit_code == 0, r.output
    assert "50% valuation discount" in r.output
    assert "before discount" in r.output and "Cliff" in r.output and "Refreshers" in r.output
    data = json.loads(invoke("analyze", EXAMPLES / "offer_startup.yaml", "--no-audit", "--json").output)
    assert data["compensation"]["recurring_tc_floor"] == "1950000.00"
    assert data["decision_state"] == "READY_TO_NEGOTIATE"


def test_missing_command():
    r = invoke("missing", EXAMPLES / "offer_basic.yaml", "--json")
    data = json.loads(r.output)
    assert data["missing_fields"] == ["employment_type", "remote_geography", "on_call_requirement", "travel_requirement"]
    assert "full-time" in invoke("missing", EXAMPLES / "offer_basic.yaml").output


def test_policies_command():
    r = invoke("policies", EXAMPLES / "offer_remote_conflict.yaml")
    assert r.exit_code == 0
    assert "POLICY-003" in r.output and "CONSTRAINT_CONFLICT" in r.output
    data = json.loads(invoke("policies", EXAMPLES / "offer_counter.yaml", "--json").output)
    assert len(data["policies"]) == 14


def test_policy_config_overlay(tmp_path):
    cfg = tmp_path / "policy.yaml"
    cfg.write_text("near_midpoint_tolerance: 0.2\n", encoding="utf-8")
    data = json.loads(invoke("policies", EXAMPLES / "offer_basic.yaml", "--json", "--policy-config", cfg).output)
    p010 = next(p for p in data["policies"] if p["policy_id"] == "POLICY-010")
    assert p010["result"] == "PASSED"


def test_compare_command():
    r = invoke("compare", EXAMPLES / "offer_counter.yaml", EXAMPLES / "offer_revised.yaml")
    assert r.exit_code == 0 and "no combined score" in r.output
    mixed = [EXAMPLES / "offer_counter.yaml", EXAMPLES / "offer_remote_conflict.yaml"]
    assert invoke("compare", *mixed).exit_code == 2
    assert invoke("compare", *mixed, "--fx", "USD=31.5").exit_code == 0


def test_review_flow(log):
    path = EXAMPLES / "offer_remote_conflict.yaml"
    bad = invoke("review", path, "--decision", "ACCEPTABLE", "--reviewer", "me", "--audit-log", log)
    assert bad.exit_code == 2 and "INVALID_TRANSITION" in bad.output
    ok = invoke("review", path, "--decision", "HUMAN_REVIEW", "--reviewer", "me", "--audit-log", log, "--json")
    assert json.loads(ok.output)["from_state"] == "CONSTRAINT_CONFLICT"
    nxt = json.loads(invoke("review", path, "--decision", "COUNTER", "--reviewer", "me", "--audit-log", log,
                            "--json").output)
    assert (nxt["from_state"], nxt["decision_state"]) == ("HUMAN_REVIEW", "COUNTER")
    shown = json.loads(invoke("analyze", path, "--json", "--audit-log", log).output)
    assert shown["decision_state"] == "CONSTRAINT_CONFLICT"
    assert shown["human_decision"] == "COUNTER"


def test_audit_show(log):
    assert "no sessions" in invoke("audit", "show", "--audit-log", log).output
    invoke("analyze", EXAMPLES / "offer_counter.yaml", "--audit-log", log)
    invoke("review", EXAMPLES / "offer_counter.yaml", "--decision", "WALK_AWAY", "--reviewer", "me",
           "--comment", "found better", "--audit-log", log)

    listing = invoke("audit", "show", "--audit-log", log)
    assert listing.exit_code == 0
    assert "offer_counter" in listing.output and "hash chain intact" in listing.output
    sessions = json.loads(invoke("audit", "show", "--json", "--audit-log", log).output)["sessions"]
    assert sessions[0]["last_human_decision"] == "WALK_AWAY"

    timeline = invoke("audit", "show", "offer_counter", "--audit-log", log)
    assert timeline.exit_code == 0
    assert "POLICY-009" in timeline.output and "found better" in timeline.output
    entries = json.loads(invoke("audit", "show", "offer_counter", "--json", "--audit-log", log).output)["timeline"]
    assert [e["kind"] for e in entries] == ["analysis", "human_decision"]

    analysis_id = entries[0]["analysis_id"]
    events = invoke("audit", "show", "offer_counter", "--analysis", analysis_id, "--audit-log", log)
    assert events.exit_code == 0 and "DECISION_MADE" in events.output
    raw = json.loads(invoke("audit", "show", "offer_counter", "--analysis", analysis_id, "--json",
                            "--audit-log", log).output)["records"]
    assert raw[-1]["event_type"] == "DECISION_MADE"


def test_audit_show_errors(log):
    invoke("analyze", EXAMPLES / "offer_counter.yaml", "--audit-log", log)
    assert invoke("audit", "show", "nope", "--audit-log", log).exit_code == 2
    assert invoke("audit", "show", "offer_counter", "--analysis", "nope", "--audit-log", log).exit_code == 2
    assert invoke("audit", "show", "--analysis", "x", "--audit-log", log).exit_code == 2
    text = Path(log).read_text(encoding="utf-8").replace("1300000", "1200000", 1)
    Path(log).write_text(text, encoding="utf-8")
    assert "verification failed" in invoke("audit", "show", "offer_counter", "--audit-log", log).output


def test_corrupt_audit_log_fails_cleanly(log):
    Path(log).write_text("not json\n", encoding="utf-8")
    r = invoke("analyze", EXAMPLES / "offer_counter.yaml", "--json", "--audit-log", log)
    assert r.exit_code == 1
    assert json.loads(r.output)["failure"]["failure_class"] == "AUDIT_CORRUPT"
    review = invoke("review", EXAMPLES / "offer_counter.yaml", "--decision", "WALK_AWAY", "--reviewer", "me",
                    "--audit-log", log)
    assert review.exit_code == 1 and "AUDIT_CORRUPT" in review.output


def test_audit_verify_detects_tampering(log):
    invoke("analyze", EXAMPLES / "offer_basic.yaml", "--audit-log", log)
    text = Path(log).read_text(encoding="utf-8").replace("1500000", "1600000", 1)
    Path(log).write_text(text, encoding="utf-8")
    r = invoke("audit", "verify", "--audit-log", log)
    assert r.exit_code == 1

import json
from pathlib import Path

import pytest

from conftest import AS_OF, make_input
from salaryops import audit
from salaryops.decision import analyze
from salaryops.models import FailureClass, PolicySettings, SalaryOpsError, parse_offer
from salaryops.policies import DecisionState


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


def write(log_path: Path, **override):
    a = analyze(make_input(**override), PolicySettings(), AS_OF)
    audit.write_analysis(audit.AuditLog(log_path), a, "s1", "file")
    return a


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_analysis_writes_full_event_sequence(log_path):
    a = write(log_path)
    records = audit.read_records(log_path)
    types = [r["event_type"] for r in records]
    assert types[:5] == ["ANALYSIS_STARTED", "COMPENSATION_CALCULATED", "BAND_ANALYZED",
                         "MISSING_INFO_DETECTED", "LEVERAGE_ASSESSED"]
    assert types.count("POLICY_EVALUATED") == len(a.policies)
    assert types[-1] == "DECISION_MADE"
    assert records[0]["prev_hash"] == audit.GENESIS
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    assert len({r["analysis_id"] for r in records}) == 1


def test_audit_reconstructs_inputs_and_decision(log_path):
    a = write(log_path, compensation={"base": 1300000})
    records = audit.read_records(log_path)
    started = records[0]
    assert parse_offer(started["inputs"]) == a.offer
    assert started["evidence"]["settings_sha256"] == PolicySettings().sha256()
    fired = [r for r in records if r["event_type"] == "POLICY_EVALUATED" and r["result"]["outcome"] == "TRIGGERED"]
    assert [r["policy_id"] for r in fired] == ["POLICY-009"]
    assert fired[0]["evidence"]["observed"]["base"] == "1300000"
    assert records[-1]["result"]["decision_state"] == "COUNTER"


def test_chain_continues_across_analyses(log_path):
    write(log_path)
    first = audit.read_records(log_path)
    write(log_path)
    records = audit.read_records(log_path)
    assert records[len(first)]["prev_hash"] == first[-1]["hash"]
    assert audit.verify(log_path).ok


def test_verify_detects_modified_record(log_path):
    write(log_path)
    content = lines(log_path)
    record = json.loads(content[1])
    record["result"]["base"] = "9999999"
    content[1] = json.dumps(record)
    log_path.write_text("\n".join(content) + "\n", encoding="utf-8")
    result = audit.verify(log_path)
    assert not result.ok and result.line == 2 and "hash mismatch" in result.error


def test_verify_detects_deleted_record(log_path):
    write(log_path)
    content = lines(log_path)
    del content[3]
    log_path.write_text("\n".join(content) + "\n", encoding="utf-8")
    result = audit.verify(log_path)
    assert not result.ok and result.line == 4


def test_verify_missing_file(tmp_path):
    assert not audit.verify(tmp_path / "none.jsonl").ok


@pytest.mark.parametrize(
    ("content", "error"),
    [("not json\n", "not valid JSON"), ("[1, 2]\n", "not a JSON object")],
)
def test_verify_reports_unparseable_lines(log_path, content, error):
    log_path.write_text(content, encoding="utf-8")
    result = audit.verify(log_path)
    assert not result.ok and result.line == 1 and error in result.error


@pytest.mark.parametrize("content", ["not json\n", "[1, 2]\n", '{"event_type": "X"}\n'])
def test_corrupt_log_raises_typed_failure_instead_of_appending(log_path, content):
    log_path.write_text(content, encoding="utf-8")
    with pytest.raises(SalaryOpsError) as exc:
        write(log_path)
    assert exc.value.failure.failure_class is FailureClass.AUDIT_CORRUPT
    assert log_path.read_text(encoding="utf-8") == content


def test_session_summary_and_timeline(log_path):
    a = write(log_path, compensation={"base": 1300000})
    sha = audit.offer_sha256(a)
    audit.write_human_decision(audit.AuditLog(log_path), "s1", sha, DecisionState.COUNTER, DecisionState.WALK_AWAY,
                               "me", "too low")
    other = analyze(make_input(), PolicySettings(), AS_OF)
    audit.write_analysis(audit.AuditLog(log_path), other, "s2", "cli")
    records = audit.read_records(log_path)

    summaries = {s.session_id: s for s in audit.summarize_sessions(records)}
    assert list(summaries) == ["s1", "s2"]
    assert (summaries["s1"].analyses, summaries["s1"].human_decisions) == (1, 1)
    assert (summaries["s1"].last_decision, summaries["s1"].last_human_decision) == ("COUNTER", "WALK_AWAY")
    assert summaries["s2"].last_human_decision is None

    analysis_entry, human_entry = audit.session_timeline(records, "s1")
    assert analysis_entry.kind is audit.EntryKind.ANALYSIS
    assert analysis_entry.input_sha256 == human_entry.input_sha256 == sha
    assert analysis_entry.detail["triggered_policies"] == ["POLICY-009"]
    assert analysis_entry.detail["as_of"] == AS_OF.isoformat()
    assert human_entry.to_dict()["kind"] == "human_decision"
    assert (human_entry.detail["from_state"], human_entry.decision_state) == ("COUNTER", "WALK_AWAY")
    assert human_entry.detail["comment"] == "too low"


def test_event_summary_covers_every_event_type(log_path):
    a = write(log_path)
    audit.write_human_decision(audit.AuditLog(log_path), "s1", audit.offer_sha256(a), DecisionState.ACCEPTABLE,
                               DecisionState.WALK_AWAY, "me", None)
    records = audit.read_records(log_path)
    summaries = {r["event_type"]: audit.event_summary(r) for r in records}
    assert set(summaries) == set(audit.EventType)
    assert summaries["DECISION_MADE"] == "ACCEPTABLE / CONFIRM_IN_WRITING"
    assert summaries["HUMAN_DECISION_RECORDED"] == "ACCEPTABLE -> WALK_AWAY by me"
    assert "unrecognized" in audit.event_summary({"event_type": "SOMETHING_ELSE"})


def test_latest_human_decision_is_scoped_to_input(log_path):
    a = write(log_path)
    sha = audit.offer_sha256(a)
    log = audit.AuditLog(log_path)
    assert audit.latest_state(log_path, "s1", sha) is None
    audit.write_human_decision(log, "s1", sha, DecisionState.ACCEPTABLE, DecisionState.WALK_AWAY, "me", "no")
    assert audit.latest_state(log_path, "s1", sha) is DecisionState.WALK_AWAY
    assert audit.latest_state(log_path, "s1", "other-input") is None
    assert audit.latest_state(log_path, "s2", sha) is None
    assert audit.verify(log_path).ok

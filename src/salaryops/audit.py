"""Append-only, hash-chained JSONL audit trail.

Each record stores the hash of the previous record, so editing or deleting any earlier
line breaks verification. This detects tampering; it does not prevent it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .decision import Analysis
from .models import Failure, FailureClass, SalaryOpsError
from .policies import DecisionState

SCHEMA_VERSION = 1
GENESIS = "GENESIS"
DEFAULT_PATH = Path(".salaryops") / "audit.jsonl"


class EventType(StrEnum):
    ANALYSIS_STARTED = "ANALYSIS_STARTED"
    COMPENSATION_CALCULATED = "COMPENSATION_CALCULATED"
    BAND_ANALYZED = "BAND_ANALYZED"
    MISSING_INFO_DETECTED = "MISSING_INFO_DETECTED"
    LEVERAGE_ASSESSED = "LEVERAGE_ASSESSED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    DECISION_MADE = "DECISION_MADE"
    HUMAN_DECISION_RECORDED = "HUMAN_DECISION_RECORDED"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(record: dict[str, Any]) -> str:
    return sha256_text(canonical({k: v for k, v in record.items() if k != "hash"}))


def offer_sha256(analysis: Analysis) -> str:
    return sha256_text(canonical(analysis.offer.model_dump(mode="json")))


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _corrupt(path: Path, where: str, why: str) -> SalaryOpsError:
    return SalaryOpsError(Failure(
        FailureClass.AUDIT_CORRUPT,
        f"{path} {where}: {why}; run `salaryops audit verify` and repair or move the file",
    ))


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                raise _corrupt(path, f"line {line_no}", "not valid JSON") from None
            if not isinstance(record, dict):
                raise _corrupt(path, f"line {line_no}", "not a JSON object")
            records.append(record)
    return records


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _tail(self) -> tuple[int, str]:
        records = read_records(self.path)
        if not records:
            return 0, GENESIS
        sequence, last_hash = records[-1].get("sequence"), records[-1].get("hash")
        if not isinstance(sequence, int) or not isinstance(last_hash, str):
            raise _corrupt(self.path, "last record", "missing sequence or hash")
        return sequence, last_hash

    def append_many(self, events: list[dict[str, Any]], session_id: str, analysis_id: str) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sequence, prev = self._tail()
        written = []
        timestamp = now_utc()
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            for event in events:
                sequence += 1
                record = {
                    "schema_version": SCHEMA_VERSION,
                    "sequence": sequence,
                    "timestamp": timestamp,
                    "session_id": session_id,
                    "analysis_id": analysis_id,
                    "event_type": event["event_type"],
                    "policy_id": event.get("policy_id"),
                    "inputs": event.get("inputs"),
                    "result": event.get("result"),
                    "evidence": event.get("evidence"),
                    "prev_hash": prev,
                }
                record["hash"] = record_hash(record)
                fh.write(canonical(record) + "\n")
                prev = record["hash"]
                written.append(record)
        return written


def analysis_events(analysis: Analysis, as_of_source: str) -> list[dict[str, Any]]:
    offer, comp, band, lev = analysis.offer, analysis.compensation, analysis.band, analysis.leverage
    events: list[dict[str, Any]] = [
        {
            "event_type": EventType.ANALYSIS_STARTED,
            "inputs": offer.model_dump(mode="json"),
            "evidence": {
                "input_sha256": offer_sha256(analysis),
                "as_of": analysis.as_of.isoformat(),
                "as_of_source": as_of_source,
                "settings": analysis.settings.model_dump(mode="json"),
                "settings_sha256": analysis.settings.sha256(),
            },
        },
        {
            "event_type": EventType.COMPENSATION_CALCULATED,
            "inputs": offer.compensation.model_dump(mode="json"),
            "result": comp.to_dict(),
            "evidence": {"formula": "recurring = base + bonus + annualized equity; year1 = recurring + sign_on"},
        },
        {
            "event_type": EventType.BAND_ANALYZED,
            "inputs": {
                "base": str(comp.base),
                "salary_band": None if offer.salary_band is None else offer.salary_band.model_dump(mode="json"),
                "near_midpoint_tolerance": str(analysis.settings.near_midpoint_tolerance),
            },
            "result": band.to_dict(),
        },
        {
            "event_type": EventType.MISSING_INFO_DETECTED,
            "result": [m.to_dict() for m in analysis.missing],
            "evidence": {"critical": [m.field for m in analysis.critical_missing]},
        },
        {
            "event_type": EventType.LEVERAGE_ASSESSED,
            "inputs": {
                "batna": None if offer.batna is None else offer.batna.model_dump(mode="json"),
                "days_to_deadline": lev.days_to_deadline,
            },
            "result": {"level": lev.level.value, "points": lev.points},
            "evidence": list(lev.evidence),
        },
    ]
    for r in analysis.policies:
        d = r.to_dict()
        events.append({
            "event_type": EventType.POLICY_EVALUATED,
            "policy_id": r.policy_id,
            "result": {"outcome": d["result"], "state": d["state"], "action": d["action"]},
            "evidence": {"reason": r.reason, "observed": r.evidence, "missing": list(r.missing)},
        })
    events.append({
        "event_type": EventType.DECISION_MADE,
        "result": {"decision_state": analysis.decision.state.value, "action": analysis.decision.action.value},
        "evidence": {
            "deciding_policies": list(analysis.decision.deciding_policies),
            "reason": analysis.decision.reason,
            "next_question": analysis.next_question,
            "failures": [f.to_dict() for f in analysis.failures],
        },
    })
    return events


def write_analysis(log: AuditLog, analysis: Analysis, session_id: str, as_of_source: str) -> str:
    analysis_id = uuid.uuid4().hex[:12]
    log.append_many(analysis_events(analysis, as_of_source), session_id, analysis_id)
    return analysis_id


def latest_state(path: Path, session_id: str, input_sha256: str) -> DecisionState | None:
    """Most recent state for this exact input: a human decision if one exists, else None."""
    for record in reversed(read_records(path)):
        if (
            record.get("session_id") == session_id
            and record.get("event_type") == EventType.HUMAN_DECISION_RECORDED
            and (record.get("inputs") or {}).get("input_sha256") == input_sha256
        ):
            return DecisionState(record["result"]["decision_state"])
    return None


def write_human_decision(
    log: AuditLog,
    session_id: str,
    input_sha256: str,
    from_state: DecisionState,
    to_state: DecisionState,
    reviewer: str,
    comment: str | None,
) -> dict[str, Any]:
    event = {
        "event_type": EventType.HUMAN_DECISION_RECORDED,
        "inputs": {"input_sha256": input_sha256, "from_state": from_state.value, "requested": to_state.value},
        "result": {"decision_state": to_state.value},
        "evidence": {"reviewer": reviewer, "comment": comment},
    }
    return log.append_many([event], session_id, uuid.uuid4().hex[:12])[0]


class EntryKind(StrEnum):
    ANALYSIS = "analysis"
    HUMAN_DECISION = "human_decision"


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    analyses: int
    human_decisions: int
    last_timestamp: str
    last_decision: str | None
    last_human_decision: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "analyses": self.analyses,
            "human_decisions": self.human_decisions,
            "last_timestamp": self.last_timestamp,
            "last_decision": self.last_decision,
            "last_human_decision": self.last_human_decision,
        }


@dataclass(frozen=True)
class TimelineEntry:
    """One analysis run or one human decision within a session."""

    kind: EntryKind
    analysis_id: str
    timestamp: str
    input_sha256: str | None
    decision_state: str | None
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "analysis_id": self.analysis_id,
            "timestamp": self.timestamp,
            "input_sha256": self.input_sha256,
            "decision_state": self.decision_state,
            **self.detail,
        }


def summarize_sessions(records: list[dict[str, Any]]) -> list[SessionSummary]:
    """One summary per session, in order of first appearance."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("session_id")), []).append(record)
    out = []
    for session_id, recs in grouped.items():
        decisions = [r for r in recs if r.get("event_type") == EventType.DECISION_MADE]
        humans = [r for r in recs if r.get("event_type") == EventType.HUMAN_DECISION_RECORDED]
        out.append(SessionSummary(
            session_id=session_id,
            analyses=len(decisions),
            human_decisions=len(humans),
            last_timestamp=str(recs[-1].get("timestamp")),
            last_decision=(decisions[-1].get("result") or {}).get("decision_state") if decisions else None,
            last_human_decision=(humans[-1].get("result") or {}).get("decision_state") if humans else None,
        ))
    return out


def session_records(records: list[dict[str, Any]], session_id: str, analysis_id: str | None = None) -> list[dict[str, Any]]:
    return [
        r for r in records
        if r.get("session_id") == session_id and (analysis_id is None or r.get("analysis_id") == analysis_id)
    ]


def session_timeline(records: list[dict[str, Any]], session_id: str) -> list[TimelineEntry]:
    by_run: dict[str, list[dict[str, Any]]] = {}
    for record in session_records(records, session_id):
        by_run.setdefault(str(record.get("analysis_id")), []).append(record)
    return [_timeline_entry(run_id, recs) for run_id, recs in by_run.items()]


def _timeline_entry(analysis_id: str, recs: list[dict[str, Any]]) -> TimelineEntry:
    by_type = {r.get("event_type"): r for r in recs}
    human = by_type.get(EventType.HUMAN_DECISION_RECORDED)
    if human is not None:
        inputs, evidence = human.get("inputs") or {}, human.get("evidence") or {}
        return TimelineEntry(
            kind=EntryKind.HUMAN_DECISION,
            analysis_id=analysis_id,
            timestamp=str(human.get("timestamp")),
            input_sha256=inputs.get("input_sha256"),
            decision_state=(human.get("result") or {}).get("decision_state"),
            detail={"from_state": inputs.get("from_state"), "reviewer": evidence.get("reviewer"),
                    "comment": evidence.get("comment")},
        )
    started = by_type.get(EventType.ANALYSIS_STARTED) or {}
    decision = by_type.get(EventType.DECISION_MADE) or {}
    started_ev, decision_ev = started.get("evidence") or {}, decision.get("evidence") or {}
    triggered = [
        r.get("policy_id") for r in recs
        if r.get("event_type") == EventType.POLICY_EVALUATED and (r.get("result") or {}).get("outcome") == "TRIGGERED"
    ]
    missing = by_type.get(EventType.MISSING_INFO_DETECTED) or {}
    return TimelineEntry(
        kind=EntryKind.ANALYSIS,
        analysis_id=analysis_id,
        timestamp=str(recs[0].get("timestamp")),
        input_sha256=started_ev.get("input_sha256"),
        decision_state=(decision.get("result") or {}).get("decision_state"),
        detail={
            "as_of": started_ev.get("as_of"),
            "action": (decision.get("result") or {}).get("action"),
            "deciding_policies": decision_ev.get("deciding_policies", []),
            "triggered_policies": triggered,
            "critical_missing": (missing.get("evidence") or {}).get("critical", []),
            "reason": decision_ev.get("reason"),
        },
    )


def event_summary(record: dict[str, Any]) -> str:
    """Short one-line description of an audit record's result."""
    result = record.get("result") or {}
    evidence = record.get("evidence") or {}
    match record.get("event_type"):
        case EventType.ANALYSIS_STARTED:
            return f"as_of {evidence.get('as_of')} ({evidence.get('as_of_source')}), input {str(evidence.get('input_sha256'))[:12]}"
        case EventType.COMPENSATION_CALCULATED:
            return f"recurring TC floor {result.get('recurring_tc_floor')} {result.get('currency')}"
        case EventType.BAND_ANALYZED:
            return f"band {result.get('category')}, compa {result.get('compa_ratio')}"
        case EventType.MISSING_INFO_DETECTED:
            return f"critical missing: {', '.join(evidence.get('critical', [])) or 'none'}"
        case EventType.LEVERAGE_ASSESSED:
            return f"leverage {result.get('level')} ({result.get('points')} points)"
        case EventType.POLICY_EVALUATED:
            effect = " -> ".join(x for x in (result.get("state"), result.get("action")) if x)
            return f"{result.get('outcome')}{f' {effect}' if effect else ''}: {evidence.get('reason')}"
        case EventType.DECISION_MADE:
            return f"{result.get('decision_state')} / {result.get('action')}"
        case EventType.HUMAN_DECISION_RECORDED:
            inputs = record.get("inputs") or {}
            return f"{inputs.get('from_state')} -> {result.get('decision_state')} by {evidence.get('reviewer')}"
        case other:
            return f"unrecognized event {other!r}"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records: int
    error: str | None = None
    line: int | None = None


def verify(path: Path) -> VerifyResult:
    if not path.exists():
        return VerifyResult(False, 0, f"{path} does not exist")
    prev = GENESIS
    count = 0
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return VerifyResult(False, count, "line is not valid JSON", line_no)
            if not isinstance(record, dict):
                return VerifyResult(False, count, "line is not a JSON object", line_no)
            count += 1
            if record.get("schema_version") != SCHEMA_VERSION:
                return VerifyResult(False, count, f"unsupported schema_version {record.get('schema_version')}", line_no)
            if record.get("sequence") != count:
                return VerifyResult(False, count, f"sequence {record.get('sequence')} != expected {count}", line_no)
            if record.get("prev_hash") != prev:
                return VerifyResult(False, count, "prev_hash does not match previous record (deleted or reordered line)", line_no)
            if record_hash(record) != record.get("hash"):
                return VerifyResult(False, count, "hash mismatch (record was modified)", line_no)
            prev = record["hash"]
    return VerifyResult(True, count)

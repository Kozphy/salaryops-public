"""salaryops command-line interface.

Exit codes: 0 analysis completed (any decision state); 2 input could not be analyzed or a
review transition was invalid; 1 audit verification failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from . import audit
from .compare import compare, parse_fx
from .decision import Analysis, analyze, apply_human_decision
from .models import OfferInput, SalaryOpsError, load_offer, load_settings
from .policies import DecisionState
from .report import (
    render_analysis,
    render_comparison,
    render_decision,
    render_missing_detail,
    render_policy_table,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Negotiation control plane for job offers.")
audit_app = typer.Typer(no_args_is_help=True, help="Inspect the audit trail.")
app.add_typer(audit_app, name="audit")
console = Console()
err_console = Console(stderr=True)

OfferPath = Annotated[Path, typer.Argument(help="Offer YAML file.")]
AsOf = Annotated[str | None, typer.Option("--as-of", help="Analysis date YYYY-MM-DD (default: file's as_of, else today).")]
PolicyConfig = Annotated[Path | None, typer.Option("--policy-config", help="YAML file overriding policy thresholds.")]
JsonOut = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
AuditPath = Annotated[Path, typer.Option("--audit-log", help="Audit JSONL file.")]


@dataclass(frozen=True)
class Run:
    analysis: Analysis
    session_id: str
    as_of_source: str


def _fail(exc: SalaryOpsError, as_json: bool) -> typer.Exit:
    if as_json:
        _emit_json({"failure": exc.to_dict()})
    else:
        err_console.print(f"[bold red]{exc.failure.failure_class.value}[/bold red]: {exc.failure.message}")
    return typer.Exit(code=2)


def _emit_json(payload: dict[str, Any]) -> None:
    console.print_json(json.dumps(payload, ensure_ascii=False))


def resolve_as_of(flag: str | None, offer: OfferInput) -> tuple[date, str]:
    if flag:
        try:
            return date.fromisoformat(flag), "cli"
        except ValueError:
            raise typer.BadParameter(f"--as-of must be YYYY-MM-DD, got {flag!r}") from None
    if offer.as_of:
        return offer.as_of, "file"
    return date.today(), "today"


def run(path: Path, as_of: str | None, policy_config: Path | None) -> Run:
    offer = load_offer(path)
    settings = load_settings(policy_config)
    day, source = resolve_as_of(as_of, offer)
    return Run(analyze(offer, settings, day), offer.session or path.stem, source)


@app.command("analyze")
def analyze_cmd(
    path: OfferPath,
    as_of: AsOf = None,
    policy_config: PolicyConfig = None,
    as_json: JsonOut = False,
    audit_log: AuditPath = audit.DEFAULT_PATH,
    no_audit: Annotated[bool, typer.Option("--no-audit", help="Do not write audit events.")] = False,
) -> None:
    """Full analysis: compensation, band, leverage, missing info, policies, decision."""
    try:
        r = run(path, as_of, policy_config)
    except SalaryOpsError as exc:
        raise _fail(exc, as_json) from exc
    analysis_id = None
    human = None
    if not no_audit:
        analysis_id = audit.write_analysis(audit.AuditLog(audit_log), r.analysis, r.session_id, r.as_of_source)
        human = audit.latest_state(audit_log, r.session_id, audit.offer_sha256(r.analysis))
    if as_json:
        payload = r.analysis.to_dict() | {"session_id": r.session_id, "analysis_id": analysis_id}
        payload["human_decision"] = None if human is None else human.value
        _emit_json(payload)
        return
    render_analysis(console, r.analysis, human)
    if analysis_id:
        console.print(f"\n[dim]audit: {audit_log} (session {r.session_id}, analysis {analysis_id})[/dim]")


@app.command("missing")
def missing_cmd(path: OfferPath, as_of: AsOf = None, as_json: JsonOut = False) -> None:
    """List missing information and the questions to ask."""
    try:
        a = run(path, as_of, None).analysis
    except SalaryOpsError as exc:
        raise _fail(exc, as_json) from exc
    if as_json:
        _emit_json({
            "decision_state": a.decision.state.value,
            "missing_fields": [m.field for m in a.critical_missing],
            "missing": [m.to_dict() for m in a.missing],
            "next_question": a.next_question,
        })
        return
    render_missing_detail(console, a)


@app.command("policies")
def policies_cmd(path: OfferPath, as_of: AsOf = None, policy_config: PolicyConfig = None, as_json: JsonOut = False) -> None:
    """Show every policy result with its evidence and the resulting decision."""
    try:
        a = run(path, as_of, policy_config).analysis
    except SalaryOpsError as exc:
        raise _fail(exc, as_json) from exc
    if as_json:
        _emit_json({"policies": [p.to_dict() for p in a.policies], **a.decision.to_dict()})
        return
    render_policy_table(console, a)


@app.command("compare")
def compare_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Two or three offer YAML files.")],
    fx: Annotated[list[str] | None, typer.Option("--fx", help="Exchange rate, e.g. USD=31.5 (target units per 1 USD).")] = None,
    currency: Annotated[str | None, typer.Option("--currency", help="Currency to show (default: first offer's).")] = None,
    as_of: AsOf = None,
    policy_config: PolicyConfig = None,
    as_json: JsonOut = False,
) -> None:
    """Compare up to three offers dimension by dimension (no combined score)."""
    try:
        runs = [(p.stem, run(p, as_of, policy_config).analysis) for p in paths]
        result = compare(runs, parse_fx(fx or []), currency)
    except SalaryOpsError as exc:
        raise _fail(exc, as_json) from exc
    if as_json:
        _emit_json(result.to_dict())
        return
    render_comparison(console, result)


@app.command("review")
def review_cmd(
    path: OfferPath,
    decision: Annotated[DecisionState, typer.Option("--decision", help="State chosen by the reviewer.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="Who made the decision.")],
    comment: Annotated[str | None, typer.Option("--comment", help="Why.")] = None,
    as_of: AsOf = None,
    policy_config: PolicyConfig = None,
    audit_log: AuditPath = audit.DEFAULT_PATH,
    as_json: JsonOut = False,
) -> None:
    """Record a human decision (e.g. WALK_AWAY) against the current state."""
    try:
        r = run(path, as_of, policy_config)
        sha = audit.offer_sha256(r.analysis)
        current = audit.latest_state(audit_log, r.session_id, sha) or r.analysis.decision.state
        new_state = apply_human_decision(current, decision)
    except SalaryOpsError as exc:
        raise _fail(exc, as_json) from exc
    record = audit.write_human_decision(
        audit.AuditLog(audit_log), r.session_id, sha, current, new_state, reviewer, comment
    )
    if as_json:
        _emit_json({"from_state": current.value, "decision_state": new_state.value, "sequence": record["sequence"]})
        return
    render_decision(console, r.analysis, new_state)
    console.print(f"\n  recorded {current.value} -> {new_state.value} by {reviewer} (audit sequence {record['sequence']})")


@audit_app.command("verify")
def audit_verify_cmd(audit_log: AuditPath = audit.DEFAULT_PATH) -> None:
    """Check the hash chain of the audit log."""
    result = audit.verify(audit_log)
    if result.ok:
        console.print(f"[green]OK[/green] {result.records} records, hash chain intact ({audit_log})")
        return
    where = f" at line {result.line}" if result.line else ""
    err_console.print(f"[bold red]FAILED[/bold red]{where}: {result.error}")
    raise typer.Exit(code=1)


@app.callback()
def _main() -> None:
    """SalaryOps: deterministic offer analysis with explicit unknowns."""

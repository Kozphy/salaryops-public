"""Human-readable terminal rendering. No calculations happen here."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .audit import EntryKind, SessionSummary, TimelineEntry, VerifyResult, event_summary
from .compare import Comparison
from .compensation import CompResult
from .decision import Analysis
from .models import Amount, AmountStatus
from .policies import DecisionState, Outcome, PolicyResult
from .salary_band import BandCategory

STATE_STYLES = {
    DecisionState.NEED_MORE_INFORMATION: "yellow",
    DecisionState.READY_TO_NEGOTIATE: "cyan",
    DecisionState.COUNTER: "cyan",
    DecisionState.ACCEPTABLE: "green",
    DecisionState.CONSTRAINT_CONFLICT: "red",
    DecisionState.HUMAN_REVIEW: "magenta",
    DecisionState.WALK_AWAY: "red",
}

SYMBOLS = {"TWD": "NT$", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "SGD": "S$", "HKD": "HK$"}

BAND_LABELS = {
    BandCategory.BELOW_BAND: "Below band",
    BandCategory.LOWER_BAND: "Between low and midpoint",
    BandCategory.NEAR_MIDPOINT: "Near midpoint",
    BandCategory.UPPER_BAND: "Between midpoint and high",
    BandCategory.ABOVE_BAND: "Above band",
    BandCategory.UNKNOWN: "Unknown (no salary band)",
}

LABEL_WIDTH = 14


def money(value: Decimal, currency: str) -> str:
    symbol = SYMBOLS.get(currency, f"{currency} ")
    text = f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"
    return f"{symbol}{text}"


def amount_text(a: Amount, currency: str) -> Text:
    if a.status is AmountStatus.UNKNOWN:
        return Text("unknown", style="yellow")
    assert a.value is not None
    return Text(money(a.value, currency))


def floor_text(value: Decimal, unknown: tuple[str, ...], currency: str) -> Text:
    if not unknown:
        return Text(money(value, currency), style="bold")
    return Text.assemble(
        (f">= {money(value, currency)}", "bold"),
        (f"  (floor; unknown: {', '.join(unknown)})", "yellow"),
    )


def heading(console: Console, title: str) -> None:
    console.print()
    console.print(Text(title, style="bold cyan"))


def row(console: Console, label: str, value: Text | str) -> None:
    console.print(Text.assemble(f"  {label:<{LABEL_WIDTH}}", value))


def render_compensation(console: Console, comp: CompResult) -> None:
    cur = comp.currency
    heading(console, f"COMPENSATION ({cur})")
    row(console, "Base", money(comp.base, cur))
    row(console, "Bonus", amount_text(comp.bonus, cur))
    equity = amount_text(comp.equity, cur)
    equity.append(f"  ({comp.equity_basis})", style="dim")
    row(console, "Equity / yr", equity)
    if comp.equity_gross.value is not None and comp.equity_gross != comp.equity:
        row(console, "  paper value", Text(f"{money(comp.equity_gross.value, cur)} / yr before discount", style="dim"))
    if comp.vesting_cliff_months is not None:
        note = "no equity vests in year 1" if comp.cliff_blocks_year1 else "unvested equity is forfeited if you leave first"
        row(console, "Cliff", Text.assemble(f"{comp.vesting_cliff_months} months", (f"  ({note})", "dim")))
    if comp.equity_refresh.is_known:
        refresh = amount_text(comp.equity_refresh, cur)
        refresh.append("  / yr (shown only; discretionary, never added to TC)", style="dim")
        row(console, "Refreshers", refresh)
    row(console, "Sign-on", amount_text(comp.sign_on, cur))
    row(console, "Recurring TC", floor_text(comp.recurring_floor, comp.unknown_recurring, cur))
    row(console, "Year-1 TC", floor_text(comp.year1_floor, comp.unknown_recurring + comp.unknown_one_time, cur))
    benefits = amount_text(comp.benefits, cur)
    benefits.append("  (shown only; never added to TC)", style="dim")
    row(console, "Benefits", benefits)


def render_band(console: Console, analysis: Analysis) -> None:
    band, cur = analysis.band, analysis.compensation.currency
    heading(console, "SALARY BAND")
    if band.category is BandCategory.UNKNOWN:
        row(console, "Band", Text("unknown", style="yellow"))
        return
    assert band.low is not None and band.high is not None and band.midpoint is not None
    mid_note = "derived" if band.midpoint_derived else "stated"
    row(console, "Band", f"{money(band.low, cur)} - {money(band.high, cur)}")
    row(console, "Midpoint", f"{money(band.midpoint, cur)} ({mid_note})")
    row(console, "Source", f"{band.source}, {band.confidence} confidence")
    row(console, "Position", f"{BAND_LABELS[band.category]} ({band.category.value})")
    assert band.band_position is not None and band.compa_ratio is not None
    row(console, "Band position", f"{band.band_position * 100:.0f}% of the way from low to high")
    row(console, "Compa ratio", f"{band.compa_ratio:.3f}  (base / midpoint)")
    assert band.distance_to_midpoint is not None
    d = band.distance_to_midpoint
    row(console, "To midpoint", f"{money(abs(d), cur)} {'below' if d > 0 else 'above' if d < 0 else 'at'} midpoint")


def render_leverage(console: Console, analysis: Analysis) -> None:
    lev = analysis.leverage
    heading(console, "LEVERAGE (BATNA)")
    pts = "" if lev.points is None else f" ({lev.points} points)"
    row(console, "Level", Text(f"{lev.level.value}{pts}", style="bold"))
    for line in lev.evidence:
        console.print(f"    - {line}")


def render_missing(console: Console, analysis: Analysis) -> None:
    heading(console, "MISSING INFORMATION")
    if not analysis.missing:
        console.print("  none")
        return
    for m in analysis.missing:
        tag = Text("critical", style="bold red") if m.critical else Text("optional", style="dim")
        console.print(Text.assemble("  - ", (f"{m.label:<34}", ""), tag))


def render_missing_detail(console: Console, analysis: Analysis) -> None:
    render_missing(console, analysis)
    if analysis.missing:
        heading(console, "QUESTIONS")
        for m in analysis.missing:
            who = "recruiter" if m.ask_recruiter else "you"
            console.print(Text.assemble(f"  [{who:<9}] ", (m.question, "bold" if m.critical else "")))


def outcome_text(result: PolicyResult) -> Text:
    if result.outcome is Outcome.PASSED:
        return Text("PASS", style="green")
    if result.outcome is Outcome.NOT_EVALUABLE:
        return Text("N/A ", style="yellow")
    return Text("FIRE", style="bold red" if result.state else "bold magenta")


def render_policy_lines(console: Console, analysis: Analysis, verbose: bool) -> None:
    for r in analysis.policies:
        if not verbose and r.outcome is Outcome.PASSED:
            continue
        console.print(Text.assemble("  ", outcome_text(r), f"  {r.policy_id}  ", (r.title, "bold")))
        console.print(Text(f"        {r.reason}", style="dim"))
    if not verbose:
        passed = sum(1 for r in analysis.policies if r.outcome is Outcome.PASSED)
        console.print(Text(f"  ({passed} policies passed; see `salaryops policies` for all)", style="dim"))


def render_policy_table(console: Console, analysis: Analysis) -> None:
    table = Table(title=f"POLICY RESULTS - {analysis.offer.role}", show_lines=True, expand=False)
    table.add_column("Policy", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("Check", ratio=3, overflow="fold")
    table.add_column("Effect", ratio=2, overflow="fold")
    for r in analysis.policies:
        if r.triggered:
            effect = f"{r.state.value if r.state else 'flag only'}\n{r.action.value if r.action else ''}".strip()
        elif r.missing:
            effect = f"needs: {', '.join(r.missing)}"
        else:
            effect = ""
        check = Text.assemble((r.title, "bold"), "\n", (r.reason, "dim"))
        table.add_row(r.policy_id, outcome_text(r), check, effect)
    console.print(table)
    render_decision(console, analysis)


def render_decision(console: Console, analysis: Analysis, human_state: DecisionState | None = None) -> None:
    d = analysis.decision
    heading(console, "DECISION")
    row(console, "State", Text(d.state.value, style=f"bold {STATE_STYLES[d.state]}"))
    row(console, "Action", d.action.value)
    row(console, "Why", f"{', '.join(d.deciding_policies) or 'fallback'}: {d.reason}")
    row(console, "Next step", analysis.action_summary)
    if human_state is not None:
        row(console, "Human review", Text(f"recorded decision for this input: {human_state.value}", style="bold"))


def render_analysis(console: Console, analysis: Analysis, human_state: DecisionState | None = None) -> None:
    offer = analysis.offer
    console.print(Text("SALARYOPS ANALYSIS", style="bold"))
    console.print("=" * 40)
    row(console, "Company", offer.company or Text("unknown", style="yellow"))
    row(console, "Role", offer.role + (f" ({offer.level})" if offer.level else ""))
    row(console, "As of", analysis.as_of.isoformat())
    render_compensation(console, analysis.compensation)
    render_band(console, analysis)
    render_leverage(console, analysis)
    render_missing(console, analysis)
    heading(console, "POLICY RESULTS")
    render_policy_lines(console, analysis, verbose=False)
    render_decision(console, analysis, human_state)
    if analysis.next_question:
        heading(console, "RECOMMENDED NEXT QUESTION")
        console.print(f'  "{analysis.next_question}"')
    if analysis.failures:
        heading(console, "DATA QUALITY")
        for f in analysis.failures:
            console.print(f"  [yellow]{f.failure_class.value}[/yellow]: {f.message}")


def render_comparison(console: Console, cmp: Comparison) -> None:
    cur = cmp.currency
    table = Table(title=f"OFFER COMPARISON ({cur}; no combined score)", expand=False)
    table.add_column("Dimension", style="bold", no_wrap=True)
    for col in cmp.columns:
        table.add_column(col.label)

    def tc(value: Decimal, complete: bool) -> str:
        return money(value, cur) if complete else f">= {money(value, cur)} (incomplete)"

    rows: list[tuple[str, list[str]]] = [
        ("Base", [money(c.base, cur) for c in cmp.columns]),
        ("Recurring TC", [tc(c.recurring_floor, c.recurring_complete) for c in cmp.columns]),
        ("Year-1 TC", [tc(c.year1_floor, c.year1_complete) for c in cmp.columns]),
        ("Equity / yr", ["unknown" if c.equity is None else money(c.equity, cur) for c in cmp.columns]),
        ("Band position", [
            c.band_category + ("" if c.compa_ratio is None else f" (compa {c.compa_ratio:.3f})") for c in cmp.columns
        ]),
        ("Remote fit", [c.remote for c in cmp.columns]),
        ("Travel", [c.travel for c in cmp.columns]),
        ("On-call", [c.on_call for c in cmp.columns]),
        ("Location", [c.location for c in cmp.columns]),
        ("Violations", ["\n".join(c.constraint_violations) or "none" for c in cmp.columns]),
        ("Critical missing", [", ".join(c.critical_missing) or "none" for c in cmp.columns]),
        ("Decision", [c.decision_state.value for c in cmp.columns]),
    ]
    for name, values in rows:
        table.add_row(name, *values)
    console.print(table)
    converted = [c for c in cmp.columns if c.source_currency != cur]
    for c in converted:
        console.print(f"  {c.label}: converted from {c.source_currency} at {c.rate} {cur} per 1 {c.source_currency} (user-supplied)")


def render_chain_status(console: Console, result: VerifyResult) -> None:
    if result.ok:
        console.print(Text(f"hash chain intact ({result.records} records)", style="dim"))
    else:
        where = f" at line {result.line}" if result.line else ""
        console.print(Text(f"WARNING: hash chain verification failed{where}: {result.error}", style="bold red"))


def _state_text(state: str | None) -> Text:
    if state is None:
        return Text("-", style="dim")
    try:
        return Text(state, style=STATE_STYLES[DecisionState(state)])
    except ValueError:
        return Text(state)


def render_sessions(console: Console, sessions: list[SessionSummary]) -> None:
    if not sessions:
        console.print("no sessions in the audit log")
        return
    console.print(Text("AUDIT SESSIONS", style="bold"))
    for s in sessions:
        runs = f"{s.analyses} analys{'is' if s.analyses == 1 else 'es'}"
        reviews = f"{s.human_decisions} human decision{'' if s.human_decisions == 1 else 's'}"
        heading(console, s.session_id)
        row(console, "Activity", f"{runs}, {reviews}; last {s.last_timestamp[:16].replace('T', ' ')} UTC")
        row(console, "Last state", _state_text(s.last_decision))
        if s.last_human_decision:
            row(console, "Human", _state_text(s.last_human_decision))


def render_timeline(console: Console, session_id: str, entries: list[TimelineEntry]) -> None:
    console.print(Text(f"SESSION {session_id}", style="bold"))
    for e in entries:
        sha = (e.input_sha256 or "?")[:12]
        heading(console, f"{e.timestamp}  {e.kind.value}  {e.analysis_id}")
        row(console, "Input", sha)
        if e.kind is EntryKind.HUMAN_DECISION:
            row(console, "Decision", Text.assemble(f"{e.detail['from_state']} -> ", _state_text(e.decision_state)))
            row(console, "Reviewer", str(e.detail["reviewer"]))
            if e.detail.get("comment"):
                row(console, "Comment", str(e.detail["comment"]))
            continue
        row(console, "As of", str(e.detail["as_of"]))
        row(console, "State", Text.assemble(_state_text(e.decision_state), f"  ({e.detail['action']})"))
        row(console, "Fired", ", ".join(e.detail["triggered_policies"]) or "none")
        row(console, "Missing", ", ".join(e.detail["critical_missing"]) or "none")
        row(console, "Why", str(e.detail["reason"]))


def render_events(console: Console, records: list[dict[str, Any]]) -> None:
    table = Table(title=f"AUDIT EVENTS - analysis {records[0].get('analysis_id')}", expand=False)
    table.add_column("Seq", justify="right", no_wrap=True)
    table.add_column("Event", no_wrap=True)
    table.add_column("Policy", no_wrap=True)
    table.add_column("Summary", overflow="fold")
    for r in records:
        table.add_row(str(r.get("sequence")), str(r.get("event_type")), r.get("policy_id") or "", event_summary(r))
    console.print(table)

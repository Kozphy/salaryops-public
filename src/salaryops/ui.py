"""Streamlit view over the same analyze() pipeline the CLI uses. Nothing is calculated here.

Start it with `salaryops ui` (needs the `ui` extra). Streamlit runs this file as a script,
so imports are absolute.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import streamlit as st

from salaryops import audit
from salaryops.compare import Comparison, compare, parse_fx
from salaryops.decision import Analysis, analyze
from salaryops.models import Amount, OfferInput, PolicySettings, SalaryOpsError, offer_template, parse_offer_yaml, parse_settings_yaml
from salaryops.policies import DecisionState, Outcome
from salaryops.report import BAND_LABELS, money
from salaryops.salary_band import BandCategory

STATE_COLORS = {
    DecisionState.NEED_MORE_INFORMATION: "orange",
    DecisionState.READY_TO_NEGOTIATE: "blue",
    DecisionState.COUNTER: "blue",
    DecisionState.ACCEPTABLE: "green",
    DecisionState.CONSTRAINT_CONFLICT: "red",
    DecisionState.HUMAN_REVIEW: "violet",
    DecisionState.WALK_AWAY: "red",
}
OUTCOME_LABELS = {Outcome.TRIGGERED: "FIRE", Outcome.PASSED: "PASS", Outcome.NOT_EVALUABLE: "N/A"}


def show_failure(exc: SalaryOpsError) -> None:
    st.error(f"**{exc.failure.failure_class.value}**: {exc.failure.message}")


def amount(a: Amount, currency: str) -> str:
    return "unknown" if a.value is None else money(a.value, currency)


def compensation_rows(a: Analysis) -> list[dict[str, str]]:
    comp, cur = a.compensation, a.compensation.currency
    rows = [
        {"Component": "Base", "Value": money(comp.base, cur), "Note": ""},
        {"Component": "Bonus", "Value": amount(comp.bonus, cur), "Note": "recurring"},
        {"Component": "Equity / yr", "Value": amount(comp.equity, cur), "Note": comp.equity_basis},
    ]
    if comp.equity_gross.value is not None and comp.equity_gross != comp.equity:
        rows.append({"Component": "Equity paper value / yr", "Value": amount(comp.equity_gross, cur), "Note": "before discount"})
    if comp.vesting_cliff_months is not None:
        note = "no equity vests in year 1" if comp.cliff_blocks_year1 else "unvested equity is forfeited if you leave first"
        rows.append({"Component": "Cliff", "Value": f"{comp.vesting_cliff_months} months", "Note": note})
    rows += [
        {"Component": "Refreshers / yr", "Value": amount(comp.equity_refresh, cur), "Note": "shown only; never added to TC"},
        {"Component": "Sign-on", "Value": amount(comp.sign_on, cur), "Note": "year 1 only"},
        {"Component": "Benefits", "Value": amount(comp.benefits, cur), "Note": "shown only; never added to TC"},
    ]
    return rows


def policy_rows(a: Analysis) -> list[dict[str, str]]:
    rows = []
    for r in a.policies:
        if r.triggered:
            effect = " / ".join(x for x in (r.state.value if r.state else "flag only", r.action.value if r.action else "") if x)
        elif r.missing:
            effect = f"needs: {', '.join(r.missing)}"
        else:
            effect = ""
        rows.append({"Policy": r.policy_id, "Result": OUTCOME_LABELS[r.outcome], "Check": r.title, "Effect": effect,
                     "Reason": r.reason})
    return rows


def comparison_rows(cmp: Comparison) -> list[dict[str, str]]:
    cur = cmp.currency

    def tc(value: Decimal, complete: bool) -> str:
        return money(value, cur) if complete else f">= {money(value, cur)} (incomplete)"

    dimensions: list[tuple[str, list[str]]] = [
        ("Base", [money(c.base, cur) for c in cmp.columns]),
        ("Recurring TC", [tc(c.recurring_floor, c.recurring_complete) for c in cmp.columns]),
        ("Year-1 TC", [tc(c.year1_floor, c.year1_complete) for c in cmp.columns]),
        ("Equity / yr", ["unknown" if c.equity is None else money(c.equity, cur) for c in cmp.columns]),
        ("Band position", [c.band_category for c in cmp.columns]),
        ("Remote fit", [c.remote for c in cmp.columns]),
        ("Travel", [c.travel for c in cmp.columns]),
        ("On-call", [c.on_call for c in cmp.columns]),
        ("Violations", ["; ".join(c.constraint_violations) or "none" for c in cmp.columns]),
        ("Critical missing", [", ".join(c.critical_missing) or "none" for c in cmp.columns]),
        ("Decision", [c.decision_state.value for c in cmp.columns]),
    ]
    return [{"Dimension": name, **{c.label: v for c, v in zip(cmp.columns, values, strict=True)}} for name, values in dimensions]


def render_analysis(a: Analysis) -> None:
    d, comp, cur = a.decision, a.compensation, a.compensation.currency
    st.markdown(f"## :{STATE_COLORS[d.state]}[{d.state.value}]")
    st.write(f"**{d.action.value}** - {a.action_summary}")
    st.caption(f"Deciding policies: {', '.join(d.deciding_policies) or 'fallback'} - {d.reason}")
    if a.next_question:
        st.info(f"Next question: {a.next_question}")
    for f in a.failures:
        st.warning(f"{f.failure_class.value}: {f.message}")

    cols = st.columns(3)
    cols[0].metric("Base", money(comp.base, cur))
    cols[1].metric("Recurring TC" + ("" if comp.recurring_complete else " (floor)"), money(comp.recurring_floor, cur))
    cols[2].metric("Year-1 TC" + ("" if comp.year1_complete else " (floor)"), money(comp.year1_floor, cur))

    tabs = st.tabs(["Compensation", "Band", "Leverage", "Missing info", "Policies", "JSON"])
    tabs[0].dataframe(compensation_rows(a), hide_index=True)
    with tabs[1]:
        band = a.band
        if band.category is BandCategory.UNKNOWN or band.low is None or band.high is None or band.midpoint is None:
            st.warning("No salary band: band position and compa-ratio cannot be computed.")
        else:
            st.write(f"{money(band.low, cur)} - {money(band.high, cur)}, midpoint {money(band.midpoint, cur)} "
                     f"({'derived' if band.midpoint_derived else 'stated'}); {band.source}, {band.confidence} confidence")
            c1, c2 = st.columns(2)
            c1.metric("Position", BAND_LABELS[band.category])
            c2.metric("Compa ratio", f"{band.compa_ratio:.3f}")
    with tabs[2]:
        lev = a.leverage
        st.metric("Leverage", lev.level.value + ("" if lev.points is None else f" ({lev.points} points)"))
        st.markdown("\n".join(f"- {line}" for line in lev.evidence))
    tabs[3].dataframe(
        [{"Field": m.field, "Critical": m.critical, "Ask": "recruiter" if m.ask_recruiter else "you", "Question": m.question}
         for m in a.missing],
        hide_index=True,
    )
    tabs[4].dataframe(policy_rows(a), hide_index=True)
    with tabs[5]:
        payload = json.dumps(a.to_dict(), indent=2, ensure_ascii=False)
        st.download_button("Download JSON", payload, file_name="analysis.json", mime="application/json")
        st.json(a.to_dict(), expanded=False)


def resolve_as_of(offer: OfferInput, picked: date, prefer_file: bool) -> tuple[date, str]:
    if prefer_file and offer.as_of:
        return offer.as_of, "file"
    return picked, "ui"


def record_in_audit(a: Analysis, offer: OfferInput, as_of_source: str) -> None:
    with st.expander("Audit trail"):
        log_path = Path(st.text_input("Audit log", str(audit.DEFAULT_PATH)))
        session = st.text_input("Session", offer.session or "ui")
        if st.button("Record this analysis"):
            try:
                analysis_id = audit.write_analysis(audit.AuditLog(log_path), a, session, as_of_source)
            except SalaryOpsError as exc:
                show_failure(exc)
            else:
                st.success(f"Recorded analysis {analysis_id} in {log_path} (session {session}).")


def load_upload() -> None:
    upload = st.session_state.get("upload")
    if upload is not None:
        st.session_state["offer_text"] = upload.getvalue().decode("utf-8")


def analyze_page(settings: PolicySettings, picked: date, prefer_file: bool) -> None:
    st.session_state.setdefault("offer_text", offer_template())
    st.file_uploader("Load an offer YAML", type=["yaml", "yml"], key="upload", on_change=load_upload)
    text = st.text_area("Offer YAML", key="offer_text", height=360)
    try:
        offer = parse_offer_yaml(text)
    except SalaryOpsError as exc:
        show_failure(exc)
        return
    day, source = resolve_as_of(offer, picked, prefer_file)
    analysis = analyze(offer, settings, day)
    st.caption(f"{offer.company or 'unknown company'} - {offer.role}; as of {day.isoformat()} ({source})")
    render_analysis(analysis)
    record_in_audit(analysis, offer, source)


def compare_page(settings: PolicySettings, picked: date, prefer_file: bool) -> None:
    uploads = st.file_uploader("Two or three offer YAML files", type=["yaml", "yml"], accept_multiple_files=True)
    fx_text = st.text_input("Exchange rates", placeholder="USD=31.5, EUR=34 (target units per 1 unit)")
    currency = st.text_input("Show in currency", placeholder="default: first offer's currency") or None
    if not uploads:
        st.caption("Upload offers to compare them dimension by dimension. There is no combined score.")
        return
    try:
        runs = []
        for up in uploads:
            offer = parse_offer_yaml(up.getvalue().decode("utf-8"), up.name)
            runs.append((Path(up.name).stem, analyze(offer, settings, resolve_as_of(offer, picked, prefer_file)[0])))
        fx = parse_fx([p.strip() for p in fx_text.split(",") if p.strip()])
        result = compare(runs, fx, currency)
    except SalaryOpsError as exc:
        show_failure(exc)
        return
    st.dataframe(comparison_rows(result), hide_index=True)
    for c in result.columns:
        if c.source_currency != result.currency:
            st.caption(f"{c.label}: converted from {c.source_currency} at {c.rate} {result.currency} per 1 {c.source_currency}")


def sidebar_settings() -> tuple[PolicySettings | None, date, bool]:
    st.sidebar.header("Settings")
    picked = st.sidebar.date_input("Analysis date", value=date.today())
    day = picked if isinstance(picked, date) else date.today()
    prefer_file = st.sidebar.checkbox("Use the file's as_of when present", value=True)
    policy_text = st.sidebar.text_area("Policy threshold overrides (YAML)", height=120, placeholder="near_midpoint_tolerance: 0.1")
    try:
        settings = parse_settings_yaml(policy_text)
    except SalaryOpsError as exc:
        with st.sidebar:
            show_failure(exc)
        return None, day, prefer_file
    return settings, day, prefer_file


def main() -> None:
    st.set_page_config(page_title="SalaryOps", layout="wide")
    st.title("SalaryOps")
    st.caption("Deterministic offer analysis with explicit unknowns. Decisions stay yours.")
    settings, picked, prefer_file = sidebar_settings()
    if settings is None:
        return
    analyze_tab, compare_tab = st.tabs(["Analyze", "Compare"])
    with analyze_tab:
        analyze_page(settings, picked, prefer_file)
    with compare_tab:
        compare_page(settings, picked, prefer_file)


main()

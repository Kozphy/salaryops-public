# Future work and explicit non-goals

Ideas that came up during the MVP and were deliberately left out. Each needs a concrete
reason before it is built.

## Out of scope for the MVP (non-goals)

- Job-board scraping, LinkedIn automation, automatic applications
- Resume generation, interview scheduling, applicant tracking, recruiter CRM
- Web scraping salary sites or complex market-data ingestion
- Autonomous negotiation with recruiters or automatically sending email
- Multi-agent architecture, RAG, vector databases
- Kubernetes, distributed workers, event bus, enterprise RBAC, authentication

## Possible later additions

- **Salary observations import.** Read the `salary-observations.tsv` produced by
  career-ops to suggest a band (always labelled `self_estimate`, low confidence) when no
  recruiter band exists.
- **Exchange-rate source.** `compare` only accepts user-supplied `--fx` rates. A dated,
  cited rate file could be added, but rates must stay explicit in the audit trail.
- **Country normalization.** Countries are matched by case-insensitive name, so
  "USA" and "United States" do not match. ISO 3166 codes would fix this.
- **Equity detail.** Cliffs, refreshers, and private-company valuation discounts. Today
  equity is annualized linearly over `vesting_years`.
- **Audit tail anchoring.** The hash chain detects edits and deletions inside the file but
  not truncation of the last records. Periodically recording the latest hash elsewhere
  would close that gap.
- **`audit show` command** to pretty-print one session's events.
- **Streamlit view** over the same `analyze()` function (Week 3, optional).

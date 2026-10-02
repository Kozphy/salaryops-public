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
- **Audit tail anchoring.** The hash chain detects edits and deletions inside the file but
  not truncation of the last records. Periodically recording the latest hash elsewhere
  would close that gap.
- **Back-loaded vesting.** Equity is annualized evenly over `vesting_years` (after any
  cliff). Schedules such as 5/15/40/40 would need a per-year vesting list.
- **Human review from the web view.** The Streamlit page records analyses but not
  `review` decisions; those stay in the CLI, where the reviewer is named explicitly.

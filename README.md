# SalaryOps - Negotiation Control Plane

SalaryOps turns a job offer into a decision you can defend. You describe the offer, the
salary band, your alternatives, and your hard constraints in a YAML file; SalaryOps
computes total compensation, places the base in the band, scores your leverage, lists what
you still need to ask, runs a fixed set of policies, and tells you the state you are in and
the next concrete step.

It is deterministic and local. There is no LLM in the decision path, no scraping, and no
network access: the same file and date always give the same answer, and every analysis is
written to a hash-chained audit log.

Three rules shape everything:

- **Unknown is never zero.** Every amount is a known value, a known zero, or `unknown`. An
  unknown bonus makes total compensation a *floor* (`>= NT$1,300,000`), never a guess.
- **Policies decide, not prose.** Each policy is a small Python function with an ID,
  evidence, and a reason. The decision cites the policies that produced it.
- **Humans own the irreversible calls.** SalaryOps never walks away on your behalf.
  `WALK_AWAY` and resolving `HUMAN_REVIEW` are recorded human decisions.

## Install

Requires Python 3.12+.

```bash
pip install -e .            # CLI
pip install -e ".[ui]"      # plus the Streamlit web view
pip install -e ".[dev,ui]"  # plus tests, ruff, mypy
```

`python -m salaryops` works the same as the `salaryops` command.

## Quick start

```bash
salaryops init offer.yaml           # commented template listing every field
# fill in what you know; leave the rest as `unknown`
salaryops analyze offer.yaml
```

Analyzing [`examples/offer_counter.yaml`](examples/offer_counter.yaml), where the base is
below the candidate's minimum but they hold a competing offer:

```text
COMPENSATION (TWD)
  Base          NT$1,300,000
  Bonus         NT$130,000
  Recurring TC  NT$1,430,000

SALARY BAND
  Position      Below band (BELOW_BAND)
  Compa ratio   0.788  (base / midpoint)

LEVERAGE (BATNA)
  Level         STRONG (4 points)
    - 1 competing offer (+2)
    - 2 late-stage interviews (+1)
    - currently employed (+1)

POLICY RESULTS
  FIRE  POLICY-009  Below minimum with leverage

DECISION
  State         COUNTER
  Action        COUNTER_TO_MINIMUM
  Next step     Counter with at least your minimum, citing your alternatives.
```

Other examples cover a first-contact offer with no band (`offer_basic.yaml`), a remote
conflict (`offer_remote_conflict.yaml`), a revised offer (`offer_revised.yaml`), and a
startup offer with private equity (`offer_startup.yaml`).

## The offer file

```yaml
as_of: 2026-10-02            # analysis date; defaults to today
company: Example Corp
role: Data Analyst

location: {country: Taiwan, city: Taipei, remote: false}

compensation:
  currency: TWD
  base: 1300000
  bonus: unknown             # number, 0, or unknown
  annualized_equity: 0
  sign_on: 0

salary_band: {low: 1400000, midpoint: 1650000, high: 1900000, source: recruiter, confidence: high}
batna: {competing_offers: 1, late_stage_interviews: 2, currently_employed: true}
recruiter: {type: in_house, offer_deadline: 2026-10-12}

constraints:
  candidate_country: Taiwan
  minimum_base: 1400000
  minimum_total_comp: 1600000
  acceptable_locations: [Taiwan]
  on_call_allowed: false
  max_travel_percent: 10
```

`salaryops init` writes the full, commented list of fields. Run `salaryops schema -o
offer.schema.json` and add `# yaml-language-server: $schema=offer.schema.json` as the first
line to get validation and autocompletion in editors that support it.

**Equity.** Give either `annualized_equity`, or a grant with `equity_grant` and
`vesting_years`. Optional detail:

- `vesting_cliff_months`: a cliff longer than 12 months removes equity from year-1 TC.
- `equity_kind: private` with `equity_discount` (e.g. `0.5`): private-company paper value is
  discounted. Private equity without a discount is treated as unknown and asked about.
- `equity_refresh_annual`: shown, never added to TC (refreshers are discretionary).

**Locations.** Countries are matched by ISO 3166 code, so `USA`, `United States`, and
`America` are the same place, as are `UK`, `England`, and `United Kingdom`.

## Commands

| Command | What it does |
| --- | --- |
| `analyze FILE` | Full analysis and decision; writes to the audit log (`--no-audit` to skip) |
| `missing FILE` | Missing information and the exact questions to ask, critical items first |
| `policies FILE` | Every policy result with its reason and the resulting decision |
| `compare A B [C]` | Up to three offers side by side, with `--fx USD=31.5` for other currencies. No combined score |
| `review FILE --decision STATE --reviewer NAME` | Record a human decision against the current state |
| `audit show [SESSION] [--analysis ID]` | List sessions, show one session's timeline, or one analysis's events |
| `audit verify` | Check the audit log's hash chain |
| `init [FILE]` | Write a commented offer template |
| `schema [-o FILE]` | Print the JSON Schema for offer files |
| `ui [--port N]` | Open the Streamlit web view |

Common options: `--as-of YYYY-MM-DD` (default: the file's `as_of`, else today),
`--policy-config FILE` (threshold overrides), `--json` (machine-readable output), and
`--audit-log FILE` (default `.salaryops/audit.jsonl`).

**Exit codes:** `0` the analysis completed, whatever the decision; `2` the input could not
be analyzed or a review transition is not allowed; `1` the audit log failed verification or
cannot be read.

## Decision states

Each gating policy either passes, fires (requesting a state), or cannot be evaluated
because information is missing (which requests `NEED_MORE_INFORMATION`). The strictest
requested state wins:

`CONSTRAINT_CONFLICT` > `HUMAN_REVIEW` > `NEED_MORE_INFORMATION` > `COUNTER` >
`READY_TO_NEGOTIATE` > `ACCEPTABLE`

| Policy | Fires when | State |
| --- | --- | --- |
| POLICY-001 | No salary band | NEED_MORE_INFORMATION |
| POLICY-002 | Critical information is missing | NEED_MORE_INFORMATION |
| POLICY-003 | Location or work authorization conflicts with your constraints | CONSTRAINT_CONFLICT |
| POLICY-004 | On-call required but not allowed | CONSTRAINT_CONFLICT |
| POLICY-005 | Travel above your maximum | CONSTRAINT_CONFLICT |
| POLICY-006 | Final offer below your minimum | HUMAN_REVIEW |
| POLICY-007 | Below your minimum with weak leverage | HUMAN_REVIEW |
| POLICY-008 | Sign-on bonus masks a recurring-pay gap | COUNTER |
| POLICY-009 | Below your minimum with leverage | COUNTER |
| POLICY-010 | Minimums met, base below midpoint, with leverage | READY_TO_NEGOTIATE |
| POLICY-011 | Deadline pressure while information is missing | HUMAN_REVIEW |
| POLICY-012 | Every gating policy passed | ACCEPTABLE |
| POLICY-013 | Band has low confidence (flag only) | - |
| POLICY-014 | Base above the band (flag only) | - |

Thresholds (midpoint tolerance, deadline windows, leverage points) can be tuned with
`--policy-config`; the policies themselves stay in code.

**Human transitions.** A reviewer may move any automatic state to `WALK_AWAY`, resolve
`HUMAN_REVIEW` to `COUNTER`, `ACCEPTABLE`, `NEED_MORE_INFORMATION`, or `WALK_AWAY`, and
escalate `CONSTRAINT_CONFLICT` to `HUMAN_REVIEW`. `WALK_AWAY` is terminal. Anything else is
rejected with exit code 2.

```bash
salaryops review examples/offer_counter.yaml --decision WALK_AWAY --reviewer me --comment "took the other offer"
```

## Audit trail

`analyze` and `review` append events to `.salaryops/audit.jsonl`: the input hash,
compensation formula, band position, leverage, every policy result, the decision, and any
human decision. Each record carries the hash of the previous one, so `audit verify` detects
any edit or deletion inside the file. A file that cannot be parsed stops the command with
`AUDIT_CORRUPT` instead of being overwritten.

Events are grouped by `session` (the file's `session` field, else the file name), so
re-analyzing a revised offer extends the same history:

```bash
salaryops audit show                       # all sessions
salaryops audit show offer_counter         # timeline of one session
salaryops audit show offer_counter --analysis <id>
```

## Web view

`salaryops ui` opens a local Streamlit page over the same `analyze()` function: edit or
upload an offer and see the decision, metrics, band, leverage, missing information,
policies, and JSON update as you type. A Compare tab takes two or three files and exchange
rates, and an expander records the current analysis in the audit log. The web view has no
logic of its own; anything it shows, the CLI shows too.

## Development

```bash
pip install -e ".[dev,ui]"
ruff check
mypy                    # strict
pytest --cov            # fails under 95% branch coverage
```

CI runs the same three checks on Python 3.12, 3.13, and 3.14
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

Layout: `models.py` (input schema and the tri-state `Amount`), `compensation.py`,
`salary_band.py`, `batna.py`, `missing_info.py`, `countries.py`, `policies.py`,
`decision.py` (pipeline, precedence, human transitions), `compare.py`, `audit.py`,
`report.py` (all terminal rendering), `cli.py`, and `ui.py`.

Deliberate non-goals and possible later work are in [`docs/FUTURE.md`](docs/FUTURE.md).

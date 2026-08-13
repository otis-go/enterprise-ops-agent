# enterprise-ops-agent

Synthetic business environment for an enterprise sales-ops agent.

**Current phase: 0C-1 — Golden Data plus 16 Background Customers.**
No agent, LLM, tool calling, RAG, embedding, memory, web UI or SQLite.

## What exists

```
data/
├── meta.yaml                 environment facts (reference time, timezone, current user, reps)
├── golden/
│   ├── README.md             human-facing walkthrough of G001-G008  (never loaded)
│   ├── customers.yaml        8 customers, one per golden case
│   ├── opportunities.yaml
│   ├── interactions.yaml
│   ├── followup_tasks.yaml
│   ├── emails.yaml
│   └── calendar_events.yaml
└── background/               16 ordinary CRM customers (never eval cases)
    ├── customers.yaml
    ├── opportunities.yaml
    ├── interactions.yaml
    ├── followup_tasks.yaml
    ├── emails.yaml
    └── calendar_events.yaml

src/eoa/
├── constants.py              closed enumerations + id formats. no environment facts.
├── models.py                 the schema — single source of truth
├── loader.py                 golden + background YAML -> one runtime Dataset
├── derive.py                 pure predicates (overdue, unanswered, open)
└── validate.py               cross-entity business rules

tests/
├── fixtures/golden_cases.yaml    G001-G008 expected behaviour  (never loaded by src/)
├── test_schema.py                field-level constraints
├── test_business_rules.py        cross-entity rules + derived logic + isolation
├── test_background_data.py       0C-1 source partition + diversity requirements
└── test_scenario_coverage.py     the runtime facts G001-G008 depend on
```

Runtime source counts:

| Entity | Golden | Background | Runtime total |
|---|---:|---:|---:|
| Customer | 8 | 16 | 24 |
| Opportunity | 5 | 12 | 17 |
| Interaction | 13 | 16 | 29 |
| FollowUpTask | 7 | 16 | 23 |
| Email | 16 | 20 | 36 |
| CalendarEvent | 9 | 7 | 16 |
| **All records** | **58** | **87** | **145** |

## Two boundaries that matter

**Facts vs. answers.** `data/golden/` and `data/background/` hold only what is
true in the simulated business. Anything resembling a correct answer — `should_prioritize`,
`expected_signals`, `must_not` — lives in `tests/fixtures/golden_cases.yaml`,
which no module under `src/eoa/` can reach. Both halves of that rule are
asserted by tests.

**Facts vs. derived state.** Nothing that depends on "now" is stored. There is
no `is_overdue`, no `is_read`, no `probability`, no sentiment label. Those are
computed in `derive.py` from the reference time in `data/meta.yaml`. Move the
reference time and every such conclusion moves with it.

## Usage

```bash
pip install -e ".[dev]"     # or: pip install pydantic pyyaml pytest

python -m eoa               # validate the complete runtime data, exit 1 on any violation
python -m pytest -v         # schema + business rules + background + scenario coverage
```

```python
from eoa import load_environment, validate_environment

meta, dataset = load_environment()
assert validate_environment(meta, dataset) == []
```

## Editing runtime data

Read `data/golden/README.md` before changing the eight reviewed customers; it
explains which facts are load-bearing. Background records belong in the six
matching files under `data/background/`. Quote every timestamp and re-run both
commands above afterwards.

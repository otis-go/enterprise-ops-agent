# enterprise-ops-agent

Synthetic business environment for an enterprise sales-ops agent.

**Current phase: 0B — data model and golden data only.**
No agent, LLM, tool calling, RAG, embedding, memory, web UI, background
customers or SQLite. Those are later phases.

## What exists

```
data/
├── meta.yaml                 environment facts (reference time, timezone, current user, reps)
└── golden/
    ├── README.md             human-facing walkthrough of G001-G008  (never loaded)
    ├── customers.yaml        8 customers, one per golden case
    ├── opportunities.yaml
    ├── interactions.yaml
    ├── followup_tasks.yaml
    ├── emails.yaml
    └── calendar_events.yaml

src/eoa/
├── constants.py              closed enumerations + id formats. no environment facts.
├── models.py                 the schema — single source of truth
├── loader.py                 YAML -> models, with the runtime/expectation boundary
├── derive.py                 pure predicates (overdue, unanswered, open)
└── validate.py               cross-entity business rules

tests/
├── fixtures/golden_cases.yaml    G001-G008 expected behaviour  (never loaded by src/)
├── test_schema.py                field-level constraints
├── test_business_rules.py        cross-entity rules + derived logic + isolation
└── test_scenario_coverage.py     the runtime facts G001-G008 depend on
```

## Two boundaries that matter

**Facts vs. answers.** `data/golden/` holds only what is true in the simulated
business. Anything resembling a correct answer — `should_prioritize`,
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

python -m eoa               # validate the golden data, exit 1 on any violation
python -m pytest -v         # schema + business rules + scenario coverage
```

```python
from eoa import load_environment, validate_environment

meta, dataset = load_environment()
assert validate_environment(meta, dataset) == []
```

## Editing golden data

Read `data/golden/README.md` first — it explains what each of the eight
customers represents and which facts are load-bearing. Quote every timestamp,
and re-run both commands above afterwards.

"""Cross-entity business rules, derived predicates, and runtime/test isolation.

Three groups:

1. the complete runtime data satisfies every rule;
2. each rule actually fires when the data is deliberately broken (a validator
   that never says no is worthless);
3. the derived pure functions behave as specified, on synthetic inputs.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from eoa.constants import OpportunityStage
from eoa.derive import (
    days_since_last_interaction,
    has_unanswered_inbound,
    is_open_opportunity,
    is_overdue_task,
    is_unanswered_inbound,
    is_unanswered_inbound_strict,
    last_interaction_at,
    overdue_tasks,
    unanswered_inbound_emails,
    upcoming_scheduled_events,
)
from eoa.loader import BUSINESS_FILES, RUNTIME_DIRNAMES
from eoa.models import Dataset, Email, FollowUpTask, Meta, Opportunity
from eoa.validate import validate_environment

CST = timezone(timedelta(hours=8))


def codes(violations) -> set[str]:
    return {violation.code for violation in violations}


def replace(dataset: Dataset, **changes) -> Dataset:
    return dataset.model_copy(update=changes)


def swap(records: list, index: int, **changes) -> list:
    updated = list(records)
    updated[index] = updated[index].model_copy(update=changes)
    return updated


# --------------------------------------------------------------------------- #
# 1. The real data is sound
# --------------------------------------------------------------------------- #


def test_runtime_data_has_no_violations(meta: Meta, dataset: Dataset):
    violations = validate_environment(meta, dataset)
    assert violations == [], "\n".join(str(v) for v in violations)


def test_current_user_is_a_known_rep(meta: Meta):
    assert meta.current_user in meta.sales_reps


def test_every_golden_customer_is_owned_by_the_current_user(meta: Meta, dataset: Dataset):
    """Keeps every golden case in scope regardless of how candidates are filtered."""
    for customer in dataset.customers:
        if customer.customer_id not in {f"CUST-00{n}" for n in range(1, 9)}:
            continue
        assert customer.owner_rep == meta.current_user


# --------------------------------------------------------------------------- #
# 2. Each rule fires on broken data
# --------------------------------------------------------------------------- #


def test_a2_detects_dangling_foreign_key(meta: Meta, dataset: Dataset):
    broken = replace(dataset, interactions=swap(dataset.interactions, 0, customer_id="CUST-404"))
    assert "A2" in codes(validate_environment(meta, broken))


def test_a3_detects_customer_opportunity_mismatch(meta: Meta, dataset: Dataset):
    index = next(i for i, t in enumerate(dataset.followup_tasks) if t.opportunity_id)
    other = next(
        c.customer_id
        for c in dataset.customers
        if c.customer_id != dataset.followup_tasks[index].customer_id
    )
    broken = replace(dataset, followup_tasks=swap(dataset.followup_tasks, index, customer_id=other))
    assert "A3" in codes(validate_environment(meta, broken))


def test_a5_detects_unknown_current_user(meta: Meta, dataset: Dataset):
    broken_meta = meta.model_copy(update={"current_user": "rep_nobody"})
    assert "A5" in codes(validate_environment(broken_meta, dataset))


def test_a6_detects_unknown_rep(meta: Meta, dataset: Dataset):
    broken = replace(dataset, followup_tasks=swap(dataset.followup_tasks, 0, owner="rep_ghost"))
    assert "A6" in codes(validate_environment(meta, broken))


def test_b2_detects_future_interaction(meta: Meta, dataset: Dataset):
    future = meta.reference_time + timedelta(days=1)
    broken = replace(dataset, interactions=swap(dataset.interactions, 0, occurred_at=future))
    assert "B2" in codes(validate_environment(meta, broken))


def test_b5_detects_completed_event_in_the_future(meta: Meta, dataset: Dataset):
    index = next(
        i for i, e in enumerate(dataset.calendar_events) if e.status.value == "completed"
    )
    broken = replace(
        dataset,
        calendar_events=swap(
            dataset.calendar_events, index,
            start_at=meta.reference_time + timedelta(days=1),
            end_at=meta.reference_time + timedelta(days=1, hours=1),
        ),
    )
    assert "B5" in codes(validate_environment(meta, broken))


def test_b5_detects_inverted_event_window(meta: Meta, dataset: Dataset):
    event = dataset.calendar_events[0]
    broken = replace(
        dataset,
        calendar_events=swap(dataset.calendar_events, 0, end_at=event.start_at),
    )
    assert "B5" in codes(validate_environment(meta, broken))


def test_b7_detects_reply_in_a_different_thread(meta: Meta, dataset: Dataset):
    index = next(i for i, e in enumerate(dataset.emails) if e.in_reply_to)
    broken = replace(dataset, emails=swap(dataset.emails, index, thread_id="THREAD-999"))
    assert "B7" in codes(validate_environment(meta, broken))


def test_c1_detects_prospect_with_a_contract(meta: Meta, dataset: Dataset):
    index = next(
        i for i, c in enumerate(dataset.customers) if c.lifecycle_status.value == "prospect"
    )
    broken = replace(
        dataset, customers=swap(dataset.customers, index, contract_end_date=date(2027, 1, 1))
    )
    assert "C1" in codes(validate_environment(meta, broken))


def test_c1_detects_active_customer_with_expired_contract(meta: Meta, dataset: Dataset):
    index = next(
        i for i, c in enumerate(dataset.customers) if c.lifecycle_status.value == "customer"
    )
    broken = replace(
        dataset, customers=swap(dataset.customers, index, contract_end_date=date(2026, 1, 1))
    )
    assert "C1" in codes(validate_environment(meta, broken))


def test_c1_allows_active_customer_without_a_contract_date(meta: Meta, dataset: Dataset):
    """The `customer` branch is the only one where null is legal."""
    index = next(
        i for i, c in enumerate(dataset.customers) if c.lifecycle_status.value == "customer"
    )
    relaxed = replace(dataset, customers=swap(dataset.customers, index, contract_end_date=None))
    assert "C1" not in codes(validate_environment(meta, relaxed))


def test_c2_detects_close_date_on_an_open_opportunity(meta: Meta, dataset: Dataset):
    index = next(i for i, o in enumerate(dataset.opportunities) if is_open_opportunity(o))
    broken = replace(
        dataset,
        opportunities=swap(dataset.opportunities, index, actual_close_date=date(2026, 8, 1)),
    )
    assert "C2" in codes(validate_environment(meta, broken))


def test_c4_detects_done_task_without_completed_at(meta: Meta, dataset: Dataset):
    index = next(i for i, t in enumerate(dataset.followup_tasks) if t.status.value == "done")
    broken = replace(
        dataset, followup_tasks=swap(dataset.followup_tasks, index, completed_at=None)
    )
    assert "C4" in codes(validate_environment(meta, broken))


def test_c6_detects_draft_with_a_sent_timestamp(meta: Meta, dataset: Dataset):
    index = next(i for i, e in enumerate(dataset.emails) if e.status.value == "draft")
    broken = replace(
        dataset,
        emails=swap(dataset.emails, index, sent_at=datetime(2026, 8, 10, 21, 0, tzinfo=CST)),
    )
    assert "C6" in codes(validate_environment(meta, broken))


def test_c6_detects_sent_mail_without_a_timestamp(meta: Meta, dataset: Dataset):
    index = next(i for i, e in enumerate(dataset.emails) if e.status.value == "sent")
    broken = replace(dataset, emails=swap(dataset.emails, index, sent_at=None))
    assert "C6" in codes(validate_environment(meta, broken))


def test_c7_requires_lost_reason_on_lost_opportunities(meta: Meta, dataset: Dataset):
    index = next(
        i for i, o in enumerate(dataset.opportunities) if o.stage is OpportunityStage.LOST
    )
    broken = replace(
        dataset, opportunities=swap(dataset.opportunities, index, lost_reason=None)
    )
    assert "C7" in codes(validate_environment(meta, broken))


def test_c7_forbids_lost_reason_on_live_opportunities(meta: Meta, dataset: Dataset):
    index = next(i for i, o in enumerate(dataset.opportunities) if is_open_opportunity(o))
    broken = replace(
        dataset,
        opportunities=swap(dataset.opportunities, index, lost_reason="budget_cancelled"),
    )
    assert "C7" in codes(validate_environment(meta, broken))


def test_d3_detects_mail_attributed_to_the_wrong_customer(meta: Meta, dataset: Dataset):
    index = next(i for i, e in enumerate(dataset.emails) if e.customer_id == "CUST-001")
    broken = replace(dataset, emails=swap(dataset.emails, index, customer_id="CUST-008"))
    assert "D3" in codes(validate_environment(meta, broken))


def test_d4_detects_sender_among_recipients(meta: Meta, dataset: Dataset):
    email = dataset.emails[0]
    broken = replace(
        dataset, emails=swap(dataset.emails, 0, to_addresses=[email.from_address])
    )
    assert "D4" in codes(validate_environment(meta, broken))


def test_e1_detects_an_ambiguous_unanswered_state(meta: Meta, dataset: Dataset):
    """A later `sent` in the thread that is not a reply to the inbound message.

    Thread-scoped logic would call EMAIL-004 answered; reply-scoped logic would
    not. That ambiguity must be reported, not silently resolved.
    """
    template = next(e for e in dataset.emails if e.email_id == "EMAIL-003")
    decoy = template.model_copy(
        update={
            "email_id": "EMAIL-900",
            "status": template.status,
            "created_at": datetime(2026, 8, 10, 12, 0, tzinfo=CST),
            "sent_at": datetime(2026, 8, 10, 12, 30, tzinfo=CST),
            "in_reply_to": None,
        }
    )
    broken = replace(dataset, emails=[*dataset.emails, decoy])
    assert "E1" in codes(validate_environment(meta, broken))


# --------------------------------------------------------------------------- #
# 3. Derived pure functions
# --------------------------------------------------------------------------- #


def _task(due: date, status: str) -> FollowUpTask:
    return FollowUpTask.model_validate(
        {
            "task_id": "TASK-900",
            "customer_id": "CUST-001",
            "opportunity_id": None,
            "title": "测试任务",
            "description": None,
            "owner": "rep_li_wei",
            "due_date": due.isoformat(),
            "status": status,
            "created_at": "2026-07-01T09:00:00+08:00",
            "completed_at": "2026-07-02T09:00:00+08:00" if status == "done" else None,
        }
    )


REFERENCE_DATE = date(2026, 8, 11)


@pytest.mark.parametrize(
    "due,status,expected",
    [
        (date(2026, 8, 10), "open", True),
        (date(2026, 8, 10), "in_progress", True),
        (date(2026, 8, 11), "open", False),          # due today is not overdue
        (date(2026, 8, 12), "open", False),
        (date(2026, 8, 10), "done", False),
        (date(2026, 8, 10), "cancelled", False),
    ],
)
def test_is_overdue_task(due, status, expected):
    assert is_overdue_task(_task(due, status), REFERENCE_DATE) is expected


def test_overdue_uses_meta_not_a_hard_coded_date(meta: Meta):
    """Move the reference date and the answer must move with it."""
    task = _task(date(2026, 8, 11), "open")
    assert is_overdue_task(task, meta.reference_date) is False
    assert is_overdue_task(task, meta.reference_date + timedelta(days=1)) is True


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("lead", True), ("qualified", True), ("demo", True),
        ("proposal", True), ("negotiation", True),
        ("won", False), ("lost", False),
    ],
)
def test_is_open_opportunity(stage, expected):
    opportunity = Opportunity.model_validate(
        {
            "opportunity_id": "OPP-900",
            "customer_id": "CUST-001",
            "name": "测试商机",
            "stage": stage,
            "amount_cny": 10000,
            "expected_close_date": "2026-12-31",
            "actual_close_date": "2026-08-01" if stage in {"won", "lost"} else None,
            "lost_reason": "budget_cancelled" if stage == "lost" else None,
            "owner_rep": "rep_li_wei",
            "created_at": "2026-01-01T09:00:00+08:00",
            "updated_at": "2026-01-01T09:00:00+08:00",
        }
    )
    assert is_open_opportunity(opportunity) is expected


def _mail(email_id: str, status: str, sent_at: str | None, in_reply_to: str | None = None) -> Email:
    return Email.model_validate(
        {
            "email_id": email_id,
            "thread_id": "THREAD-900",
            "customer_id": "CUST-001",
            "opportunity_id": None,
            "status": status,
            "from_address": "a@example.com" if status == "received" else "b@example.com",
            "to_addresses": ["b@example.com" if status == "received" else "a@example.com"],
            "cc_addresses": [],
            "subject": "测试邮件",
            "body": "这是一封用于单元测试的邮件正文，内容本身没有意义，长度足够通过模型的最小长度校验。",
            "created_at": sent_at or "2026-08-10T20:00:00+08:00",
            "sent_at": sent_at,
            "in_reply_to": in_reply_to,
        }
    )


def test_unanswered_inbound_when_no_reply_exists():
    inbound = _mail("EMAIL-901", "received", "2026-08-10T10:00:00+08:00")
    assert is_unanswered_inbound(inbound, [inbound]) is True


def test_answered_when_a_later_sent_reply_exists():
    inbound = _mail("EMAIL-901", "received", "2026-08-10T10:00:00+08:00")
    reply = _mail("EMAIL-902", "sent", "2026-08-10T18:00:00+08:00", "EMAIL-901")
    assert is_unanswered_inbound(inbound, [inbound, reply]) is False


def test_a_draft_does_not_count_as_a_reply():
    inbound = _mail("EMAIL-901", "received", "2026-08-10T10:00:00+08:00")
    draft = _mail("EMAIL-903", "draft", None, "EMAIL-901")
    assert is_unanswered_inbound(inbound, [inbound, draft]) is True
    assert is_unanswered_inbound_strict(inbound, [inbound, draft]) is True


def test_an_earlier_sent_message_does_not_count_as_a_reply():
    earlier = _mail("EMAIL-900", "sent", "2026-08-05T10:00:00+08:00")
    inbound = _mail("EMAIL-901", "received", "2026-08-10T10:00:00+08:00", "EMAIL-900")
    assert is_unanswered_inbound(inbound, [earlier, inbound]) is True


def test_outbound_mail_is_never_unanswered_inbound():
    outbound = _mail("EMAIL-902", "sent", "2026-08-10T18:00:00+08:00")
    assert is_unanswered_inbound(outbound, [outbound]) is False


def test_unanswered_set_on_the_runtime_data(dataset: Dataset):
    found = {email.email_id for email in unanswered_inbound_emails(dataset.emails)}
    assert found == {"EMAIL-004", "EMAIL-014"}


def test_both_unanswered_definitions_agree_on_the_runtime_data(dataset: Dataset):
    for email in dataset.emails:
        assert is_unanswered_inbound(email, dataset.emails) == is_unanswered_inbound_strict(
            email, dataset.emails
        ), email.email_id


def test_has_unanswered_inbound_narrows_by_customer(dataset: Dataset):
    assert has_unanswered_inbound(dataset.emails, "CUST-002") is True
    assert has_unanswered_inbound(dataset.emails, "CUST-007") is True
    assert has_unanswered_inbound(dataset.emails, "CUST-008") is False
    assert has_unanswered_inbound(dataset.emails, "CUST-001") is False


def test_days_since_last_interaction_matches_the_authored_gaps(meta: Meta, dataset: Dataset):
    expected = {
        "CUST-001": 8, "CUST-002": 6, "CUST-003": 12, "CUST-004": 5,
        "CUST-005": 7, "CUST-006": 20, "CUST-007": 14, "CUST-008": 2,
    }
    for customer_id, gap in expected.items():
        assert (
            days_since_last_interaction(dataset.interactions, customer_id, meta.reference_time)
            == gap
        ), customer_id


def test_last_interaction_is_none_for_an_unknown_customer(dataset: Dataset):
    assert last_interaction_at(dataset.interactions, "CUST-404") is None


def test_no_golden_task_is_overdue(meta: Meta, dataset: Dataset, data_dir: Path):
    """G001's task is due today; the reviewed Golden subset has no overdue task."""
    records = yaml.safe_load(
        (data_dir / "golden" / "followup_tasks.yaml").read_text(encoding="utf-8")
    )
    golden_task_ids = {record["task_id"] for record in records}
    golden_tasks = [
        task for task in dataset.followup_tasks if task.task_id in golden_task_ids
    ]
    assert {task.task_id for task in golden_tasks} == golden_task_ids
    assert overdue_tasks(golden_tasks, meta.reference_date) == []


def test_upcoming_events_respects_the_window(meta: Meta, dataset: Dataset):
    within_two = upcoming_scheduled_events(dataset.calendar_events, meta.reference_time, 2)
    assert {event.event_id for event in within_two} == {"EVT-005"}
    assert upcoming_scheduled_events(dataset.calendar_events, meta.reference_time, 0) == []


# --------------------------------------------------------------------------- #
# 4. Runtime / expectation isolation
# --------------------------------------------------------------------------- #

EXPECTATION_KEYS = (
    "should_prioritize",
    "expected_signals",
    "expected_tools",
    "must_not",
    "expected_answer",
    "case_id",
)


def _code_string_literals(tree: ast.AST) -> list[str]:
    """Every string literal in a module except docstrings.

    Prose may describe the runtime/expectation boundary; only executable
    references to it are a violation.
    """
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                docstring_nodes.add(id(node.body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


def test_runtime_code_cannot_reach_test_expectations(src_dir: Path):
    """No executable reference in src/eoa/ may point at the expectations file."""
    forbidden = ("golden_cases", "fixtures", "tests")
    for path in sorted(src_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for literal in _code_string_literals(tree):
            for token in forbidden:
                assert token not in literal, f"{path.name} references {token!r} in code"
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert "golden_case" not in node.id, path.name
            if isinstance(node, ast.Attribute):
                assert "golden_case" not in node.attr, path.name


def test_loader_only_knows_runtime_directories_and_the_six_business_files():
    assert RUNTIME_DIRNAMES == ("golden", "background")
    assert {filename for filename, _ in BUSINESS_FILES.values()} == {
        "customers.yaml", "opportunities.yaml", "interactions.yaml",
        "followup_tasks.yaml", "emails.yaml", "calendar_events.yaml",
    }


def test_runtime_yaml_contains_no_expected_answers(data_dir: Path):
    for dirname in RUNTIME_DIRNAMES:
        for path in sorted((data_dir / dirname).glob("*.yaml")):
            records = yaml.safe_load(path.read_text(encoding="utf-8"))
            for record in records:
                for key in EXPECTATION_KEYS:
                    assert key not in record, f"{dirname}/{path.name} leaks {key!r}"


def test_golden_readme_is_not_loaded(dataset: Dataset, data_dir: Path):
    """The human-facing README lives next to the data but is never parsed."""
    assert (data_dir / "golden" / "README.md").is_file()
    assert len(dataset.customers) == 24


def test_golden_cases_fixture_lives_outside_the_data_directory(
    data_dir: Path, project_root: Path
):
    assert not list(data_dir.rglob("golden_cases.yaml"))
    assert (project_root / "tests" / "fixtures" / "golden_cases.yaml").is_file()

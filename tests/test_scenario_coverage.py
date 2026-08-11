"""Scenario coverage for G001-G008.

This file is the bridge between the two halves of the environment: it reads
expectations from ``tests/fixtures/golden_cases.yaml`` and asserts that the
*runtime facts* those expectations depend on really exist in
``data/golden/*.yaml``.

It asserts structure only. Nothing here says what an agent should reply — only
that the data needed to reach a defensible answer is present and unambiguous.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from eoa.constants import CalendarEventStatus, OpportunityStage
from eoa.derive import (
    days_since_last_interaction,
    is_due_today,
    is_open_opportunity,
    is_overdue_task,
    unanswered_inbound_emails,
    upcoming_scheduled_events,
)
from eoa.models import Dataset, Meta

CASE_IDS = [f"G00{n}" for n in range(1, 9)]

# Closed vocabularies. A typo in the fixture must fail, not be ignored.
KNOWN_SIGNALS = {
    "due_today_task",
    "no_contact_8d",
    "unanswered_inbound",
    "high_value_opportunity",
    "no_progress_12d",
    "meeting_already_scheduled",
    "opportunity_lost",
    "contract_expiring_soon",
    "no_renewal_meeting",
    "product_question",
}
KNOWN_MUST_NOT = {"schedule_meeting", "routine_follow_up", "flag_as_high_priority"}


# --------------------------------------------------------------------------- #
# Fixture integrity
# --------------------------------------------------------------------------- #


def test_all_eight_cases_are_present(cases_by_id: dict[str, Any]):
    assert sorted(cases_by_id) == CASE_IDS


def test_each_case_anchors_a_distinct_customer(cases_by_id: dict[str, Any]):
    anchors = [case["anchor_customer"] for case in cases_by_id.values()]
    assert anchors == [f"CUST-00{n}" for n in range(1, 9)]
    assert len(set(anchors)) == 8


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_anchors_resolve_to_real_records(
    case_id: str, cases_by_id: dict[str, Any], dataset: Dataset
):
    case = cases_by_id[case_id]
    assert case["anchor_customer"] in dataset.customers_by_id
    if case["anchor_opportunity"] is not None:
        assert case["anchor_opportunity"] in dataset.opportunities_by_id


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_vocabularies_are_closed(case_id: str, cases_by_id: dict[str, Any]):
    case = cases_by_id[case_id]
    assert set(case["expected_signals"]) <= KNOWN_SIGNALS
    assert set(case["must_not"]) <= KNOWN_MUST_NOT
    assert isinstance(case["should_prioritize"], bool)


# --------------------------------------------------------------------------- #
# G001 - proposal, task due today, 8 days silent
# --------------------------------------------------------------------------- #


def test_g001_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    case = cases_by_id["G001"]
    customer_id, opportunity_id = case["anchor_customer"], case["anchor_opportunity"]

    opportunity = dataset.opportunities_by_id[opportunity_id]
    assert opportunity.customer_id == customer_id
    assert opportunity.stage is OpportunityStage.PROPOSAL
    assert is_open_opportunity(opportunity)

    due_today = [
        task
        for task in dataset.followup_tasks
        if task.customer_id == customer_id and is_due_today(task, meta.reference_date)
    ]
    assert due_today, "G001 needs an actionable task due on the reference date"
    assert all(task.due_date == meta.reference_date for task in due_today)

    assert days_since_last_interaction(dataset.interactions, customer_id, meta.reference_time) == 8


# --------------------------------------------------------------------------- #
# G002 - customer wrote yesterday, only a draft exists
# --------------------------------------------------------------------------- #


def test_g002_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    customer_id = cases_by_id["G002"]["anchor_customer"]

    unanswered = unanswered_inbound_emails(dataset.emails, customer_id)
    assert len(unanswered) == 1
    inbound = unanswered[0]
    assert (meta.reference_date - inbound.sent_at.date()).days == 1

    thread = [e for e in dataset.emails if e.thread_id == inbound.thread_id]
    later_sent = [
        e for e in thread
        if e.status.value == "sent" and e.sent_at and e.sent_at > inbound.sent_at
    ]
    assert later_sent == [], "a sent reply would destroy this scenario"

    drafts = [e for e in thread if e.status.value == "draft"]
    assert drafts, "G002 needs a draft to prove a draft is not an answer"
    assert all(draft.sent_at is None for draft in drafts)


# --------------------------------------------------------------------------- #
# G003 - large negotiation, 12 days without progress
# --------------------------------------------------------------------------- #


def test_g003_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    case = cases_by_id["G003"]
    customer_id, opportunity_id = case["anchor_customer"], case["anchor_opportunity"]

    opportunity = dataset.opportunities_by_id[opportunity_id]
    assert opportunity.customer_id == customer_id
    assert opportunity.stage is OpportunityStage.NEGOTIATION
    assert is_open_opportunity(opportunity)

    # A concrete authored amount. No "high value" threshold is defined in
    # Phase 0B, and this must NOT be asserted as the dataset maximum.
    assert opportunity.amount_cny == 500_000

    assert days_since_last_interaction(dataset.interactions, customer_id, meta.reference_time) == 12
    assert (meta.reference_date - opportunity.updated_at.date()).days == 12


# --------------------------------------------------------------------------- #
# G004 - proposal with a meeting already booked
# --------------------------------------------------------------------------- #


def test_g004_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    case = cases_by_id["G004"]
    customer_id, opportunity_id = case["anchor_customer"], case["anchor_opportunity"]

    opportunity = dataset.opportunities_by_id[opportunity_id]
    assert opportunity.customer_id == customer_id
    assert opportunity.stage is OpportunityStage.PROPOSAL

    upcoming = upcoming_scheduled_events(
        dataset.calendar_events, meta.reference_time, within_days=2, customer_id=customer_id
    )
    assert len(upcoming) == 1
    event = upcoming[0]
    assert event.status is CalendarEventStatus.SCHEDULED
    assert meta.reference_time < event.start_at <= meta.reference_time + timedelta(days=2)
    assert event.opportunity_id == opportunity_id

    assert "schedule_meeting" in case["must_not"]


# --------------------------------------------------------------------------- #
# G005 - lost deal, budget cancelled
# --------------------------------------------------------------------------- #


def test_g005_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    case = cases_by_id["G005"]
    customer_id, opportunity_id = case["anchor_customer"], case["anchor_opportunity"]

    opportunity = dataset.opportunities_by_id[opportunity_id]
    assert opportunity.customer_id == customer_id
    assert opportunity.stage is OpportunityStage.LOST
    assert not is_open_opportunity(opportunity)
    assert opportunity.actual_close_date is not None
    assert opportunity.actual_close_date <= meta.reference_date

    # The reason is a structured field. It must not require reading prose.
    assert opportunity.lost_reason == "budget_cancelled"

    # Nothing else may make this customer look urgent.
    assert unanswered_inbound_emails(dataset.emails, customer_id) == []
    assert not any(
        is_overdue_task(task, meta.reference_date)
        for task in dataset.followup_tasks
        if task.customer_id == customer_id
    )
    assert case["should_prioritize"] is False


# --------------------------------------------------------------------------- #
# G006 - contract expiring, no renewal motion
# --------------------------------------------------------------------------- #


def test_g006_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    case = cases_by_id["G006"]
    customer = dataset.customers_by_id[case["anchor_customer"]]

    assert customer.lifecycle_status.value == "customer"
    assert customer.contract_end_date is not None
    assert (customer.contract_end_date - meta.reference_date).days == 20

    assert case["anchor_opportunity"] is None
    assert not [
        o for o in dataset.opportunities if o.customer_id == customer.customer_id
    ], "no renewal opportunity has been created yet"

    scheduled = [
        event
        for event in dataset.calendar_events
        if event.customer_id == customer.customer_id
        and event.status is CalendarEventStatus.SCHEDULED
    ]
    assert scheduled == [], "no renewal meeting may be on the calendar"


# --------------------------------------------------------------------------- #
# G007 - unanswered product question
# --------------------------------------------------------------------------- #


def test_g007_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    customer_id = cases_by_id["G007"]["anchor_customer"]

    unanswered = unanswered_inbound_emails(dataset.emails, customer_id)
    assert len(unanswered) == 1
    inbound = unanswered[0]
    assert (meta.reference_date - inbound.sent_at.date()).days == 1

    # Only that a product-capability question was asked. Phase 0B deliberately
    # does not encode the correct answer, and there is no knowledge base.
    assert "功能" in inbound.body

    assert not any(
        task.customer_id == customer_id for task in dataset.followup_tasks
    ), "the question has not been captured as a task — that is the point"


# --------------------------------------------------------------------------- #
# G008 - healthy customer, negative control
# --------------------------------------------------------------------------- #


def test_g008_runtime_facts(cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta):
    case = cases_by_id["G008"]
    customer_id = case["anchor_customer"]

    assert days_since_last_interaction(dataset.interactions, customer_id, meta.reference_time) == 2

    assert not any(
        is_overdue_task(task, meta.reference_date)
        for task in dataset.followup_tasks
        if task.customer_id == customer_id
    )
    assert unanswered_inbound_emails(dataset.emails, customer_id) == []
    assert case["expected_signals"] == []
    assert case["should_prioritize"] is False


# --------------------------------------------------------------------------- #
# Cross-case invariants
# --------------------------------------------------------------------------- #


def test_negative_cases_carry_no_urgency_signal(
    cases_by_id: dict[str, Any], dataset: Dataset, meta: Meta
):
    for case in cases_by_id.values():
        if case["should_prioritize"]:
            continue
        customer_id = case["anchor_customer"]
        assert unanswered_inbound_emails(dataset.emails, customer_id) == [], case["case_id"]
        assert not any(
            is_overdue_task(task, meta.reference_date) or is_due_today(task, meta.reference_date)
            for task in dataset.followup_tasks
            if task.customer_id == customer_id
        ), case["case_id"]


def test_unanswered_inbound_is_confined_to_g002_and_g007(dataset: Dataset):
    owners = {email.customer_id for email in unanswered_inbound_emails(dataset.emails)}
    assert owners == {"CUST-002", "CUST-007"}


def test_only_g004_has_an_upcoming_scheduled_meeting(dataset: Dataset, meta: Meta):
    upcoming = upcoming_scheduled_events(dataset.calendar_events, meta.reference_time, within_days=30)
    assert {event.customer_id for event in upcoming} == {"CUST-004"}

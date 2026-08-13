"""Cross-entity business rules for the complete runtime dataset.

Field-level constraints are enforced by :mod:`eoa.models` at parse time. This
module carries everything that needs more than one record, or needs the
reference time, to decide.

Every rule returns :class:`Violation` objects rather than raising, so a single
run reports *all* problems in hand-written YAML instead of only the first.

The rule codes match the Phase 0A design document (A* structural, B* temporal,
C* state machine, D* domain, E* derived-logic consistency).

Note on scope: this module knows nothing about G001-G008. Those expectations
live in ``tests/fixtures/golden_cases.yaml`` and are asserted by
``tests/test_scenario_coverage.py``, which reuses the predicates in
:mod:`eoa.derive`. Runtime code stays free of evaluation answers.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .constants import (
    TERMINAL_OPPORTUNITY_STAGES,
    CalendarEventStatus,
    EmailStatus,
    LifecycleStatus,
    OpportunityStage,
    TaskStatus,
)
from .derive import is_unanswered_inbound, is_unanswered_inbound_strict
from .models import Dataset, Meta


@dataclass(frozen=True)
class Violation:
    code: str
    entity: str
    entity_id: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.code}] {self.entity} {self.entity_id}: {self.message}"


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


# --------------------------------------------------------------------------- #
# A — structural integrity
# --------------------------------------------------------------------------- #


def _check_unique_ids(dataset: Dataset) -> list[Violation]:
    out: list[Violation] = []
    groups = (
        ("customer", [c.customer_id for c in dataset.customers]),
        ("opportunity", [o.opportunity_id for o in dataset.opportunities]),
        ("interaction", [i.interaction_id for i in dataset.interactions]),
        ("followup_task", [t.task_id for t in dataset.followup_tasks]),
        ("email", [e.email_id for e in dataset.emails]),
        ("calendar_event", [v.event_id for v in dataset.calendar_events]),
    )
    for entity, ids in groups:
        seen: set[str] = set()
        for identifier in ids:
            if identifier in seen:
                out.append(
                    Violation("A1", entity, identifier, "duplicate identifier")
                )
            seen.add(identifier)

    names: set[str] = set()
    for customer in dataset.customers:
        if customer.name in names:
            out.append(
                Violation(
                    "A1", "customer", customer.customer_id, f"duplicate name {customer.name!r}"
                )
            )
        names.add(customer.name)
    return out


def _check_foreign_keys(dataset: Dataset) -> list[Violation]:
    out: list[Violation] = []
    customer_ids = set(dataset.customers_by_id)
    opportunity_ids = set(dataset.opportunities_by_id)
    email_ids = set(dataset.emails_by_id)

    def check(entity: str, entity_id: str, field: str, value: str | None, pool: set[str]) -> None:
        if value is not None and value not in pool:
            out.append(
                Violation("A2", entity, entity_id, f"{field}={value} does not exist")
            )

    for opportunity in dataset.opportunities:
        check("opportunity", opportunity.opportunity_id, "customer_id", opportunity.customer_id, customer_ids)
    for interaction in dataset.interactions:
        check("interaction", interaction.interaction_id, "customer_id", interaction.customer_id, customer_ids)
        check("interaction", interaction.interaction_id, "opportunity_id", interaction.opportunity_id, opportunity_ids)
    for task in dataset.followup_tasks:
        check("followup_task", task.task_id, "customer_id", task.customer_id, customer_ids)
        check("followup_task", task.task_id, "opportunity_id", task.opportunity_id, opportunity_ids)
    for email in dataset.emails:
        check("email", email.email_id, "customer_id", email.customer_id, customer_ids)
        check("email", email.email_id, "opportunity_id", email.opportunity_id, opportunity_ids)
        check("email", email.email_id, "in_reply_to", email.in_reply_to, email_ids)
        if email.in_reply_to == email.email_id:
            out.append(Violation("A2", "email", email.email_id, "in_reply_to points at itself"))
    for event in dataset.calendar_events:
        check("calendar_event", event.event_id, "customer_id", event.customer_id, customer_ids)
        check("calendar_event", event.event_id, "opportunity_id", event.opportunity_id, opportunity_ids)
    return out


def _check_denormalisation(dataset: Dataset) -> list[Violation]:
    """A record carrying both FKs must agree with the opportunity's customer."""
    out: list[Violation] = []
    opportunities = dataset.opportunities_by_id

    records = (
        [("interaction", i.interaction_id, i.customer_id, i.opportunity_id) for i in dataset.interactions]
        + [("followup_task", t.task_id, t.customer_id, t.opportunity_id) for t in dataset.followup_tasks]
        + [("email", e.email_id, e.customer_id, e.opportunity_id) for e in dataset.emails]
        + [("calendar_event", v.event_id, v.customer_id, v.opportunity_id) for v in dataset.calendar_events]
    )
    for entity, entity_id, customer_id, opportunity_id in records:
        if opportunity_id is None:
            continue
        opportunity = opportunities.get(opportunity_id)
        if opportunity is None:
            continue  # already reported by A2
        if customer_id is None:
            out.append(
                Violation(
                    "A3", entity, entity_id,
                    f"has opportunity_id={opportunity_id} but no customer_id",
                )
            )
        elif customer_id != opportunity.customer_id:
            out.append(
                Violation(
                    "A3", entity, entity_id,
                    f"customer_id={customer_id} but {opportunity_id} belongs to "
                    f"{opportunity.customer_id}",
                )
            )
    return out


def _check_reps(meta: Meta, dataset: Dataset) -> list[Violation]:
    out: list[Violation] = []
    reps = set(meta.sales_reps)

    if meta.current_user not in reps:
        out.append(
            Violation("A5", "meta", "current_user", f"{meta.current_user} is not in sales_reps")
        )

    def check(entity: str, entity_id: str, field: str, values: Iterable[str]) -> None:
        for value in values:
            if value not in reps:
                out.append(
                    Violation("A6", entity, entity_id, f"{field}={value!r} is not a known sales rep")
                )

    for customer in dataset.customers:
        check("customer", customer.customer_id, "owner_rep", [customer.owner_rep])
    for opportunity in dataset.opportunities:
        check("opportunity", opportunity.opportunity_id, "owner_rep", [opportunity.owner_rep])
    for interaction in dataset.interactions:
        check("interaction", interaction.interaction_id, "participants_internal", interaction.participants_internal)
    for task in dataset.followup_tasks:
        check("followup_task", task.task_id, "owner", [task.owner])
    for event in dataset.calendar_events:
        check("calendar_event", event.event_id, "attendees_internal", event.attendees_internal)
    return out


# --------------------------------------------------------------------------- #
# B — temporal rules
# --------------------------------------------------------------------------- #


def _check_temporal(meta: Meta, dataset: Dataset) -> list[Violation]:
    out: list[Violation] = []
    ref = meta.reference_time
    customers = dataset.customers_by_id
    emails = dataset.emails_by_id

    for customer in dataset.customers:
        if customer.created_at > ref:
            out.append(Violation("B4", "customer", customer.customer_id, "created_at is after reference_time"))

    for opportunity in dataset.opportunities:
        if opportunity.created_at > ref:
            out.append(Violation("B4", "opportunity", opportunity.opportunity_id, "created_at is after reference_time"))
        if opportunity.updated_at > ref:
            out.append(Violation("B4", "opportunity", opportunity.opportunity_id, "updated_at is after reference_time"))
        if opportunity.updated_at < opportunity.created_at:
            out.append(Violation("B4", "opportunity", opportunity.opportunity_id, "updated_at precedes created_at"))
        customer = customers.get(opportunity.customer_id)
        if customer and opportunity.created_at < customer.created_at:
            out.append(Violation("B6", "opportunity", opportunity.opportunity_id, "created before its customer"))

    for interaction in dataset.interactions:
        if interaction.occurred_at > ref:
            out.append(Violation("B2", "interaction", interaction.interaction_id, "occurred_at is in the future"))
        customer = customers.get(interaction.customer_id)
        if customer and interaction.occurred_at < customer.created_at:
            out.append(Violation("B6", "interaction", interaction.interaction_id, "occurred before its customer existed"))

    for task in dataset.followup_tasks:
        if task.created_at > ref:
            out.append(Violation("B4", "followup_task", task.task_id, "created_at is after reference_time"))
        customer = customers.get(task.customer_id)
        if customer and task.created_at < customer.created_at:
            out.append(Violation("B6", "followup_task", task.task_id, "created before its customer existed"))

    for email in dataset.emails:
        if email.created_at > ref:
            out.append(Violation("B4", "email", email.email_id, "created_at is after reference_time"))
        if email.sent_at is not None and email.sent_at > ref:
            out.append(Violation("B3", "email", email.email_id, "sent_at is in the future"))
        if email.customer_id is not None:
            customer = customers.get(email.customer_id)
            if customer and email.created_at < customer.created_at:
                out.append(Violation("B6", "email", email.email_id, "created before its customer existed"))
        if email.in_reply_to is not None:
            parent = emails.get(email.in_reply_to)
            if parent is not None:
                if parent.thread_id != email.thread_id:
                    out.append(
                        Violation("B7", "email", email.email_id, f"replies to {parent.email_id} in a different thread")
                    )
                if parent.sent_at is None:
                    out.append(
                        Violation("B7", "email", email.email_id, f"replies to {parent.email_id}, which was never sent")
                    )
                elif email.effective_at <= parent.sent_at:
                    out.append(
                        Violation("B7", "email", email.email_id, f"is not later than the {parent.email_id} it replies to")
                    )

    for event in dataset.calendar_events:
        if event.end_at <= event.start_at:
            out.append(Violation("B5", "calendar_event", event.event_id, "end_at is not after start_at"))
        if event.status is CalendarEventStatus.COMPLETED and event.end_at > ref:
            out.append(Violation("B5", "calendar_event", event.event_id, "is completed but ends after reference_time"))
        if event.status is CalendarEventStatus.SCHEDULED and event.start_at <= ref:
            out.append(Violation("B5", "calendar_event", event.event_id, "is scheduled but starts at or before reference_time"))
    return out


# --------------------------------------------------------------------------- #
# C — state machines
# --------------------------------------------------------------------------- #


def _check_state_machines(meta: Meta, dataset: Dataset) -> list[Violation]:
    out: list[Violation] = []
    ref = meta.reference_time
    ref_date = meta.reference_date

    for customer in dataset.customers:
        status = customer.lifecycle_status
        end = customer.contract_end_date
        if status is LifecycleStatus.PROSPECT and end is not None:
            out.append(Violation("C1", "customer", customer.customer_id, "prospect must not have contract_end_date"))
        if status is LifecycleStatus.CUSTOMER and end is not None and end < ref_date:
            out.append(Violation("C1", "customer", customer.customer_id, "active customer has an already expired contract_end_date"))
        if status is LifecycleStatus.FORMER_CUSTOMER:
            if end is None:
                out.append(Violation("C1", "customer", customer.customer_id, "former_customer requires contract_end_date"))
            elif end >= ref_date:
                out.append(Violation("C1", "customer", customer.customer_id, "former_customer contract_end_date is not in the past"))

    for opportunity in dataset.opportunities:
        terminal = opportunity.stage in TERMINAL_OPPORTUNITY_STAGES
        if terminal and opportunity.actual_close_date is None:
            out.append(Violation("C2", "opportunity", opportunity.opportunity_id, f"stage={opportunity.stage} requires actual_close_date"))
        if not terminal and opportunity.actual_close_date is not None:
            out.append(Violation("C2", "opportunity", opportunity.opportunity_id, f"stage={opportunity.stage} must not have actual_close_date"))
        if opportunity.actual_close_date is not None and opportunity.actual_close_date > ref_date:
            out.append(Violation("C2", "opportunity", opportunity.opportunity_id, "actual_close_date is in the future"))
        is_lost = opportunity.stage is OpportunityStage.LOST
        if is_lost and not opportunity.lost_reason:
            out.append(Violation("C7", "opportunity", opportunity.opportunity_id, "stage=lost requires lost_reason"))
        if not is_lost and opportunity.lost_reason is not None:
            out.append(Violation("C7", "opportunity", opportunity.opportunity_id, f"stage={opportunity.stage} must not have lost_reason"))

    for task in dataset.followup_tasks:
        done = task.status is TaskStatus.DONE
        if done and task.completed_at is None:
            out.append(Violation("C4", "followup_task", task.task_id, "status=done requires completed_at"))
        if not done and task.completed_at is not None:
            out.append(Violation("C4", "followup_task", task.task_id, f"status={task.status} must not have completed_at"))
        if task.completed_at is not None:
            if task.completed_at > ref:
                out.append(Violation("C4", "followup_task", task.task_id, "completed_at is in the future"))
            if task.completed_at < task.created_at:
                out.append(Violation("C4", "followup_task", task.task_id, "completed_at precedes created_at"))

    for email in dataset.emails:
        if email.status is EmailStatus.DRAFT:
            if email.sent_at is not None:
                out.append(Violation("C6", "email", email.email_id, "draft must not have sent_at"))
        elif email.sent_at is None:
            out.append(Violation("C6", "email", email.email_id, f"status={email.status} requires sent_at"))
        elif email.status is EmailStatus.SENT and email.created_at > email.sent_at:
            out.append(Violation("C6", "email", email.email_id, "outbound mail was created after it was sent"))
        elif email.status is EmailStatus.RECEIVED and email.created_at < email.sent_at:
            out.append(Violation("C6", "email", email.email_id, "inbound mail was recorded before the customer sent it"))
    return out


# --------------------------------------------------------------------------- #
# D — domain rules
# --------------------------------------------------------------------------- #


def _check_domain_rules(dataset: Dataset) -> list[Violation]:
    out: list[Violation] = []
    customers = dataset.customers_by_id

    for email in dataset.emails:
        if email.from_address in email.to_addresses or email.from_address in email.cc_addresses:
            out.append(Violation("D4", "email", email.email_id, "from_address also appears as a recipient"))
        if email.customer_id is None:
            continue
        customer = customers.get(email.customer_id)
        if customer is None:
            continue  # already reported by A2
        expected = customer.contact_domain
        if not any(_domain(address) == expected for address in email.all_addresses):
            out.append(
                Violation(
                    "D3", "email", email.email_id,
                    f"is attributed to {customer.customer_id} but no address uses {expected}",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# E — derived-logic consistency
# --------------------------------------------------------------------------- #


def _check_derived_consistency(dataset: Dataset) -> list[Violation]:
    """Thread-scoped and reply-scoped "unanswered" must agree on every record.

    If they disagreed, an evaluation question about unanswered mail would have
    two defensible answers.
    """
    out: list[Violation] = []
    emails = dataset.emails
    for email in emails:
        thread_level = is_unanswered_inbound(email, emails)
        reply_level = is_unanswered_inbound_strict(email, emails)
        if thread_level != reply_level:
            out.append(
                Violation(
                    "E1", "email", email.email_id,
                    f"ambiguous: thread-scoped unanswered={thread_level} but "
                    f"reply-scoped unanswered={reply_level}",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def validate_environment(meta: Meta, dataset: Dataset) -> list[Violation]:
    """Run every cross-entity rule. Empty list means the environment is sound."""
    return [
        *_check_unique_ids(dataset),
        *_check_foreign_keys(dataset),
        *_check_denormalisation(dataset),
        *_check_reps(meta, dataset),
        *_check_temporal(meta, dataset),
        *_check_state_machines(meta, dataset),
        *_check_domain_rules(dataset),
        *_check_derived_consistency(dataset),
    ]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    from .loader import DEFAULT_DATA_DIR, load_environment

    argv = sys.argv[1:] if argv is None else argv
    data_dir = Path(argv[0]) if argv else DEFAULT_DATA_DIR
    meta, dataset = load_environment(data_dir)
    violations = validate_environment(meta, dataset)
    for violation in violations:
        print(violation)
    counts = (
        len(dataset.customers), len(dataset.opportunities), len(dataset.interactions),
        len(dataset.followup_tasks), len(dataset.emails), len(dataset.calendar_events),
    )
    print(
        f"\n{sum(counts)} records "
        f"(customers={counts[0]} opportunities={counts[1]} interactions={counts[2]} "
        f"tasks={counts[3]} emails={counts[4]} events={counts[5]}) -> "
        f"{len(violations)} violation(s)"
    )
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

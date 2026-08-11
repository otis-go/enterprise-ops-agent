"""Derived business predicates.

Nothing here is stored in the golden data. Every value below is computed from
business facts plus the reference time, so it can never go stale in the way a
persisted ``is_overdue`` or ``is_read`` flag would.

All functions are pure: same inputs, same output, no I/O, no globals.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from .constants import ACTIVE_TASK_STATUSES, TERMINAL_OPPORTUNITY_STAGES, EmailStatus
from .models import CalendarEvent, Email, FollowUpTask, Interaction, Opportunity

# --------------------------------------------------------------------------- #
# FollowUpTask
# --------------------------------------------------------------------------- #


def is_overdue_task(task: FollowUpTask, reference_date: date) -> bool:
    """A task is overdue when it is still actionable and its due date has passed.

    Due *today* is not overdue: the comparison is strict.
    """
    return task.status in ACTIVE_TASK_STATUSES and task.due_date < reference_date


def is_due_today(task: FollowUpTask, reference_date: date) -> bool:
    return task.status in ACTIVE_TASK_STATUSES and task.due_date == reference_date


def overdue_tasks(
    tasks: Iterable[FollowUpTask],
    reference_date: date,
    customer_id: str | None = None,
) -> list[FollowUpTask]:
    return [
        task
        for task in tasks
        if is_overdue_task(task, reference_date)
        and (customer_id is None or task.customer_id == customer_id)
    ]


# --------------------------------------------------------------------------- #
# Opportunity
# --------------------------------------------------------------------------- #


def is_open_opportunity(opportunity: Opportunity) -> bool:
    """Open means not yet in a terminal stage (``won`` / ``lost``)."""
    return opportunity.stage not in TERMINAL_OPPORTUNITY_STAGES


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #


def is_unanswered_inbound(email: Email, emails: Iterable[Email]) -> bool:
    """Canonical definition: a received e-mail with no later *sent* reply.

    Scope is the thread. A ``draft`` never counts as an answer — that is the
    whole reason this replaces a persisted ``is_read`` flag.
    """
    if email.status is not EmailStatus.RECEIVED or email.sent_at is None:
        return False
    return not any(
        other.thread_id == email.thread_id
        and other.status is EmailStatus.SENT
        and other.sent_at is not None
        and other.sent_at > email.sent_at
        for other in emails
    )


def is_unanswered_inbound_strict(email: Email, emails: Iterable[Email]) -> bool:
    """Stricter variant scoped to direct replies via ``in_reply_to``.

    Kept so the golden data can be asserted unambiguous: both definitions must
    agree on every record, otherwise an evaluation question would have two
    defensible answers.
    """
    if email.status is not EmailStatus.RECEIVED or email.sent_at is None:
        return False
    return not any(
        other.in_reply_to == email.email_id and other.status is EmailStatus.SENT
        for other in emails
    )


def unanswered_inbound_emails(
    emails: Iterable[Email], customer_id: str | None = None
) -> list[Email]:
    all_emails = list(emails)
    return [
        email
        for email in all_emails
        if is_unanswered_inbound(email, all_emails)
        and (customer_id is None or email.customer_id == customer_id)
    ]


def has_unanswered_inbound(
    emails: Iterable[Email], customer_id: str | None = None
) -> bool:
    """Does this mailbox (optionally narrowed to one customer) owe a reply?"""
    return bool(unanswered_inbound_emails(emails, customer_id))


# --------------------------------------------------------------------------- #
# Interaction / CalendarEvent
# --------------------------------------------------------------------------- #


def last_interaction_at(
    interactions: Iterable[Interaction], customer_id: str
) -> datetime | None:
    timestamps = [
        item.occurred_at for item in interactions if item.customer_id == customer_id
    ]
    return max(timestamps) if timestamps else None


def days_since_last_interaction(
    interactions: Iterable[Interaction], customer_id: str, reference_time: datetime
) -> int | None:
    """Whole days between the last logged interaction and the reference instant."""
    occurred_at = last_interaction_at(interactions, customer_id)
    if occurred_at is None:
        return None
    return (reference_time.date() - occurred_at.date()).days


def upcoming_scheduled_events(
    events: Iterable[CalendarEvent],
    reference_time: datetime,
    within_days: int,
    customer_id: str | None = None,
) -> list[CalendarEvent]:
    """Scheduled events starting after the reference instant, inside the window."""
    from .constants import CalendarEventStatus

    horizon = reference_time + timedelta(days=within_days)
    return [
        event
        for event in events
        if event.status is CalendarEventStatus.SCHEDULED
        and reference_time < event.start_at <= horizon
        and (customer_id is None or event.customer_id == customer_id)
    ]

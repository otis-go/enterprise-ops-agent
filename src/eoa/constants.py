"""Fixed vocabulary for the synthetic business environment.

This module holds ONLY values that are genuinely constant properties of the
schema: closed enumerations, identifier formats and the e-mail address format.

It deliberately holds NO environment facts. `reference_time`, `timezone`,
`current_user` and `sales_reps` live in ``data/meta.yaml`` and reach the code
exclusively through :class:`eoa.models.Meta`.
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleStatus(StrEnum):
    """Objective contractual relationship with a customer."""

    PROSPECT = "prospect"
    CUSTOMER = "customer"
    FORMER_CUSTOMER = "former_customer"


class OpportunityStage(StrEnum):
    """Sales stage. ``WON``/``LOST`` are the only terminal stages."""

    LEAD = "lead"
    QUALIFIED = "qualified"
    DEMO = "demo"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


TERMINAL_OPPORTUNITY_STAGES: frozenset[OpportunityStage] = frozenset(
    {OpportunityStage.WON, OpportunityStage.LOST}
)


class InteractionChannel(StrEnum):
    MEETING = "meeting"
    CALL = "call"
    EMAIL = "email"
    IM = "im"
    SITE_VISIT = "site_visit"


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


ACTIVE_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.OPEN, TaskStatus.IN_PROGRESS}
)


class EmailStatus(StrEnum):
    """Direction is carried entirely by status.

    ``received`` is inbound; ``sent`` and ``draft`` are outbound. There is no
    separate ``direction`` field — it would be fully derivable from this one.
    """

    RECEIVED = "received"
    SENT = "sent"
    DRAFT = "draft"


class CalendarEventStatus(StrEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Human-readable, git-diffable identifiers. Deliberately not UUIDs.
ID_PATTERNS: dict[str, str] = {
    "customer": r"^CUST-\d{3}$",
    "opportunity": r"^OPP-\d{3}$",
    "interaction": r"^INT-\d{3}$",
    "followup_task": r"^TASK-\d{3}$",
    "email": r"^EMAIL-\d{3}$",
    "calendar_event": r"^EVT-\d{3}$",
    "thread": r"^THREAD-\d{3}$",
}

# Intentionally permissive: enough to catch typos in hand-written YAML without
# pulling in the `email-validator` dependency.
EMAIL_PATTERN = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"

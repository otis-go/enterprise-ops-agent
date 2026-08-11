"""Schema for the synthetic business environment — the single source of truth.

Six business entities plus :class:`Meta` (environment facts) and
:class:`Dataset` (the loaded golden data as a whole).

Every model uses ``extra="forbid"`` so a typo in hand-written YAML fails loudly
instead of being silently dropped, and ``frozen=True`` because the golden data
is a read-only environment.

Field-level constraints (types, enums, id formats, nullability) live here.
Cross-entity and cross-record rules live in :mod:`eoa.validate`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from .constants import (
    EMAIL_PATTERN,
    ID_PATTERNS,
    CalendarEventStatus,
    EmailStatus,
    InteractionChannel,
    LifecycleStatus,
    OpportunityStage,
    TaskStatus,
)

EmailAddress = Annotated[str, StringConstraints(pattern=EMAIL_PATTERN)]
CustomerId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["customer"])]
OpportunityId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["opportunity"])]
InteractionId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["interaction"])]
FollowUpTaskId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["followup_task"])]
EmailId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["email"])]
CalendarEventId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["calendar_event"])]
ThreadId = Annotated[str, StringConstraints(pattern=ID_PATTERNS["thread"])]

NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Meta(_Base):
    """Environment facts loaded from ``data/meta.yaml``.

    Passed explicitly into every function that needs a notion of "now". No
    module is allowed to re-derive these values from a hard-coded literal.
    """

    reference_time: AwareDatetime
    timezone: NonEmptyStr
    current_user: NonEmptyStr
    sales_reps: list[NonEmptyStr] = Field(min_length=1)

    @property
    def reference_date(self) -> date:
        """Calendar date of the reference instant, in its own UTC offset."""
        return self.reference_time.date()


class Customer(_Base):
    customer_id: CustomerId
    name: NonEmptyStr
    lifecycle_status: LifecycleStatus
    contract_end_date: date | None = None
    owner_rep: NonEmptyStr
    primary_contact_name: NonEmptyStr
    primary_contact_title: str | None = None
    primary_contact_email: EmailAddress
    created_at: AwareDatetime

    @property
    def contact_domain(self) -> str:
        return self.primary_contact_email.rsplit("@", 1)[-1].lower()


class Opportunity(_Base):
    opportunity_id: OpportunityId
    customer_id: CustomerId
    name: NonEmptyStr
    stage: OpportunityStage
    amount_cny: int = Field(gt=0)
    expected_close_date: date
    actual_close_date: date | None = None
    lost_reason: NonEmptyStr | None = None
    owner_rep: NonEmptyStr
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Interaction(_Base):
    interaction_id: InteractionId
    customer_id: CustomerId
    opportunity_id: OpportunityId | None = None
    channel: InteractionChannel
    occurred_at: AwareDatetime
    duration_minutes: int | None = Field(default=None, gt=0)
    participants_internal: list[NonEmptyStr] = Field(min_length=1)
    participants_customer: list[NonEmptyStr] = Field(min_length=1)
    summary: Annotated[str, StringConstraints(min_length=20)]


class FollowUpTask(_Base):
    task_id: FollowUpTaskId
    customer_id: CustomerId
    opportunity_id: OpportunityId | None = None
    title: NonEmptyStr
    description: str | None = None
    owner: NonEmptyStr
    due_date: date
    status: TaskStatus
    created_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class Email(_Base):
    email_id: EmailId
    thread_id: ThreadId
    customer_id: CustomerId | None = None
    opportunity_id: OpportunityId | None = None
    status: EmailStatus
    from_address: EmailAddress
    to_addresses: list[EmailAddress] = Field(min_length=1)
    cc_addresses: list[EmailAddress] = Field(default_factory=list)
    subject: NonEmptyStr
    body: Annotated[str, StringConstraints(min_length=30)]
    created_at: AwareDatetime
    sent_at: AwareDatetime | None = None
    in_reply_to: EmailId | None = None

    @property
    def all_addresses(self) -> list[str]:
        return [self.from_address, *self.to_addresses, *self.cc_addresses]

    @property
    def effective_at(self) -> AwareDatetime:
        """Best available timestamp: when it was sent, or when drafted."""
        return self.sent_at or self.created_at


class CalendarEvent(_Base):
    event_id: CalendarEventId
    title: NonEmptyStr
    customer_id: CustomerId | None = None
    opportunity_id: OpportunityId | None = None
    start_at: AwareDatetime
    end_at: AwareDatetime
    location: str | None = None
    attendees_internal: list[NonEmptyStr] = Field(min_length=1)
    attendees_customer: list[NonEmptyStr] = Field(default_factory=list)
    status: CalendarEventStatus


class Dataset(_Base):
    """All golden business facts, already parsed and field-validated."""

    customers: list[Customer]
    opportunities: list[Opportunity]
    interactions: list[Interaction]
    followup_tasks: list[FollowUpTask]
    emails: list[Email]
    calendar_events: list[CalendarEvent]

    @property
    def customers_by_id(self) -> dict[str, Customer]:
        return {c.customer_id: c for c in self.customers}

    @property
    def opportunities_by_id(self) -> dict[str, Opportunity]:
        return {o.opportunity_id: o for o in self.opportunities}

    @property
    def emails_by_id(self) -> dict[str, Email]:
        return {e.email_id: e for e in self.emails}

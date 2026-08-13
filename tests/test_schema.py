"""Field-level schema tests.

Covers what a single record can and cannot look like: types, enums, id
formats, nullability, and the fields that were deliberately *removed* during
Phase 0A. Cross-record rules live in test_business_rules.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from eoa.constants import (
    EMAIL_PATTERN,
    ID_PATTERNS,
    CalendarEventStatus,
    EmailStatus,
    InteractionChannel,
    LifecycleStatus,
    OpportunityStage,
    TaskStatus,
)
from eoa.models import (
    CalendarEvent,
    Customer,
    Dataset,
    Email,
    FollowUpTask,
    Interaction,
    Meta,
    Opportunity,
)

ENTITY_MODELS = [
    Customer,
    Opportunity,
    Interaction,
    FollowUpTask,
    Email,
    CalendarEvent,
]

# Fields removed during Phase 0A design review. Each entry records *why*, so a
# future re-addition has to be a deliberate decision rather than a slip.
REMOVED_FIELDS = {
    Customer: [
        "tier",              # no golden case needs it
        "industry",          # no golden case needs it
        "region",            # no golden case needs it
        "status",            # replaced by lifecycle_status
        "at_risk",           # derived business label, not a fact
        "notes",             # unsupported free text
        "email_domain",      # redundant with the customer_id FK on Email
        "contract_end_at",   # renamed to contract_end_date
    ],
    Opportunity: [
        "next_step",         # expressed as a FollowUpTask instead
        "probability",       # derivable from stage
        "currency",          # single-currency in Phase 0
        "source",            # unsupported
    ],
    Interaction: [
        "sentiment",         # a model output, would leak the label
        "source_ref",        # no golden case needs it
    ],
    FollowUpTask: [
        "priority",          # would write the prioritisation answer into the data
        "source_interaction_id",  # no golden case needs it
        "is_overdue",        # derived from due_date vs reference_date
    ],
    Email: [
        "direction",         # fully derivable from status
        "is_read",           # read != answered; replaced by derived logic
        "attachments",       # unsupported
    ],
    CalendarEvent: [
        "meeting_type",              # derivable from customer_id being null
        "related_interaction_id",    # no golden case needs it
    ],
}

EXPECTED_ENUM_VALUES = {
    LifecycleStatus: {"prospect", "customer", "former_customer"},
    OpportunityStage: {
        "lead", "qualified", "demo", "proposal", "negotiation", "won", "lost",
    },
    InteractionChannel: {"meeting", "call", "email", "im", "site_visit"},
    TaskStatus: {"open", "in_progress", "done", "cancelled"},
    EmailStatus: {"received", "sent", "draft"},
    CalendarEventStatus: {"scheduled", "completed", "cancelled"},
}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("enum_type,expected", EXPECTED_ENUM_VALUES.items())
def test_enum_vocabulary_is_closed_and_exact(enum_type, expected):
    assert {member.value for member in enum_type} == expected


def test_id_patterns_cover_every_entity():
    assert set(ID_PATTERNS) == {
        "customer", "opportunity", "interaction",
        "followup_task", "email", "calendar_event", "thread",
    }


def test_constants_module_holds_no_environment_facts():
    """reference_time / current_user / sales_reps must live in data/meta.yaml."""
    import eoa.constants as constants

    source = constants.__file__
    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("2026-08-11", "reference_time =", "current_user =", "Asia/Shanghai"):
        assert forbidden not in text, f"{forbidden!r} must not be hard-coded in constants.py"


# --------------------------------------------------------------------------- #
# Field presence / absence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model,removed",
    [(model, field) for model, fields in REMOVED_FIELDS.items() for field in fields],
)
def test_removed_fields_stay_removed(model, removed):
    assert removed not in model.model_fields


def test_customer_field_set_is_exactly_as_designed():
    assert set(Customer.model_fields) == {
        "customer_id", "name", "lifecycle_status", "contract_end_date",
        "owner_rep", "primary_contact_name", "primary_contact_title",
        "primary_contact_email", "created_at",
    }


def test_opportunity_has_lost_reason():
    assert "lost_reason" in Opportunity.model_fields
    assert Opportunity.model_fields["lost_reason"].default is None


def test_email_has_created_at_and_optional_sent_at():
    assert "created_at" in Email.model_fields
    assert Email.model_fields["created_at"].is_required()
    assert not Email.model_fields["sent_at"].is_required()


@pytest.mark.parametrize("model", ENTITY_MODELS)
def test_unknown_fields_are_rejected(model):
    assert model.model_config["extra"] == "forbid"


@pytest.mark.parametrize("model", ENTITY_MODELS)
def test_records_are_immutable(model):
    assert model.model_config["frozen"] is True


# --------------------------------------------------------------------------- #
# Parsing the real files
# --------------------------------------------------------------------------- #


def test_all_six_runtime_entity_types_load(dataset: Dataset):
    assert len(dataset.customers) == 24
    assert dataset.opportunities
    assert dataset.interactions
    assert dataset.followup_tasks
    assert dataset.emails
    assert dataset.calendar_events


def test_meta_loads_from_yaml(meta: Meta):
    assert meta.timezone == "Asia/Shanghai"
    assert meta.current_user in meta.sales_reps
    assert meta.reference_date == date(2026, 8, 11)


def test_yaml_datetimes_keep_their_utc_offset(meta: Meta, dataset: Dataset):
    """PyYAML's timestamp resolver would silently normalise these to naive UTC."""
    expected_offset = timedelta(hours=8)
    assert meta.reference_time.utcoffset() == expected_offset
    assert meta.reference_time.hour == 9

    for customer in dataset.customers:
        assert customer.created_at.utcoffset() == expected_offset
    for interaction in dataset.interactions:
        assert interaction.occurred_at.utcoffset() == expected_offset


@pytest.mark.parametrize(
    "records,attr,pattern_key",
    [
        ("customers", "customer_id", "customer"),
        ("opportunities", "opportunity_id", "opportunity"),
        ("interactions", "interaction_id", "interaction"),
        ("followup_tasks", "task_id", "followup_task"),
        ("emails", "email_id", "email"),
        ("calendar_events", "event_id", "calendar_event"),
    ],
)
def test_identifiers_match_their_pattern(dataset: Dataset, records, attr, pattern_key):
    import re

    pattern = re.compile(ID_PATTERNS[pattern_key])
    for record in getattr(dataset, records):
        assert pattern.match(getattr(record, attr))


def test_email_addresses_are_well_formed(dataset: Dataset):
    import re

    pattern = re.compile(EMAIL_PATTERN)
    for email in dataset.emails:
        for address in email.all_addresses:
            assert pattern.match(address), address
    for customer in dataset.customers:
        assert pattern.match(customer.primary_contact_email)


# --------------------------------------------------------------------------- #
# Negative cases
# --------------------------------------------------------------------------- #


def _customer_payload(**overrides):
    payload = {
        "customer_id": "CUST-999",
        "name": "测试客户",
        "lifecycle_status": "prospect",
        "contract_end_date": None,
        "owner_rep": "rep_li_wei",
        "primary_contact_name": "测试联系人",
        "primary_contact_title": None,
        "primary_contact_email": "test@example.com",
        "created_at": "2026-01-01T09:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def test_valid_customer_payload_parses():
    assert Customer.model_validate(_customer_payload()).customer_id == "CUST-999"


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        Customer.model_validate(_customer_payload(created_at="2026-01-01T09:00:00"))


def test_bad_identifier_is_rejected():
    with pytest.raises(ValidationError):
        Customer.model_validate(_customer_payload(customer_id="CUSTOMER-1"))


def test_unknown_enum_value_is_rejected():
    with pytest.raises(ValidationError):
        Customer.model_validate(_customer_payload(lifecycle_status="at_risk"))


def test_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        Customer.model_validate(_customer_payload(tier="strategic"))


def test_malformed_email_address_is_rejected():
    with pytest.raises(ValidationError):
        Customer.model_validate(_customer_payload(primary_contact_email="not-an-email"))


def test_non_positive_amount_is_rejected():
    with pytest.raises(ValidationError):
        Opportunity.model_validate(
            {
                "opportunity_id": "OPP-999",
                "customer_id": "CUST-999",
                "name": "测试商机",
                "stage": "lead",
                "amount_cny": 0,
                "expected_close_date": "2026-12-31",
                "actual_close_date": None,
                "lost_reason": None,
                "owner_rep": "rep_li_wei",
                "created_at": "2026-01-01T09:00:00+08:00",
                "updated_at": "2026-01-01T09:00:00+08:00",
            }
        )


def test_interaction_requires_participants_on_both_sides():
    payload = {
        "interaction_id": "INT-999",
        "customer_id": "CUST-999",
        "opportunity_id": None,
        "channel": "call",
        "occurred_at": "2026-01-01T09:00:00+08:00",
        "duration_minutes": 30,
        "participants_internal": [],
        "participants_customer": ["某人"],
        "summary": "这是一段长度足够通过最小长度校验的测试摘要文本。",
    }
    with pytest.raises(ValidationError):
        Interaction.model_validate(payload)


def test_record_cannot_be_mutated(dataset: Dataset):
    customer = dataset.customers[0]
    with pytest.raises(ValidationError):
        customer.name = "改名"


def test_calendar_event_requires_an_internal_attendee():
    with pytest.raises(ValidationError):
        CalendarEvent.model_validate(
            {
                "event_id": "EVT-999",
                "title": "测试会议",
                "customer_id": None,
                "opportunity_id": None,
                "start_at": "2026-01-01T09:00:00+08:00",
                "end_at": "2026-01-01T10:00:00+08:00",
                "location": None,
                "attendees_internal": [],
                "attendees_customer": [],
                "status": "completed",
            }
        )


def test_followup_task_rejects_unknown_status():
    with pytest.raises(ValidationError):
        FollowUpTask.model_validate(
            {
                "task_id": "TASK-999",
                "customer_id": "CUST-999",
                "opportunity_id": None,
                "title": "测试任务",
                "description": None,
                "owner": "rep_li_wei",
                "due_date": "2026-01-01",
                "status": "blocked",
                "created_at": "2026-01-01T09:00:00+08:00",
                "completed_at": None,
            }
        )

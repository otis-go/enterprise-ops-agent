"""Enterprise Ops Agent — synthetic business environment.

Phase 0 scope: schema, runtime YAML loading, validation, SQLite building.
No agent, LLM, CRM tool, natural-language query, RAG, embedding, memory or web UI.
"""

from .constants import (
    CalendarEventStatus,
    EmailStatus,
    InteractionChannel,
    LifecycleStatus,
    OpportunityStage,
    TaskStatus,
)
from .loader import load_dataset, load_environment, load_meta
from .models import (
    CalendarEvent,
    Customer,
    Dataset,
    Email,
    FollowUpTask,
    Interaction,
    Meta,
    Opportunity,
)
from .validate import Violation, validate_environment

__all__ = [
    "CalendarEvent",
    "CalendarEventStatus",
    "Customer",
    "Dataset",
    "Email",
    "EmailStatus",
    "FollowUpTask",
    "Interaction",
    "InteractionChannel",
    "LifecycleStatus",
    "Meta",
    "Opportunity",
    "OpportunityStage",
    "TaskStatus",
    "Violation",
    "load_dataset",
    "load_environment",
    "load_meta",
    "validate_environment",
]

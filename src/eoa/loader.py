"""Load the synthetic business environment from ``data/``.

Two hard boundaries are enforced here:

1. **Runtime never reads test expectations.** Only ``data/meta.yaml`` and the
   six whitelisted business files under ``data/golden/`` and
   ``data/background/`` are loadable. The evaluation fixture is unreachable
   from this module by construction, and adjacent documentation is never read.
2. **YAML timestamps stay strings.** PyYAML's implicit timestamp resolver
   converts ``2026-08-11T09:00:00+08:00`` into a *naive UTC* ``datetime``,
   silently destroying the UTC offset. The resolver is stripped below so every
   temporal value reaches pydantic as text and keeps its ``+08:00``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

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

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

META_FILENAME = "meta.yaml"
RUNTIME_DIRNAMES = ("golden", "background")

# The complete, closed list of business-fact files. Anything not named here is
# not part of the runtime environment.
BUSINESS_FILES: dict[str, tuple[str, type]] = {
    "customers": ("customers.yaml", Customer),
    "opportunities": ("opportunities.yaml", Opportunity),
    "interactions": ("interactions.yaml", Interaction),
    "followup_tasks": ("followup_tasks.yaml", FollowUpTask),
    "emails": ("emails.yaml", Email),
    "calendar_events": ("calendar_events.yaml", CalendarEvent),
}

# Backwards-compatible public name from Phase 0B. It still describes the same
# closed six-file whitelist; only the source directories have expanded.
GOLDEN_FILES = BUSINESS_FILES


class _NoTimestampLoader(yaml.SafeLoader):
    """SafeLoader with the implicit ``timestamp`` resolver removed."""


_NoTimestampLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"missing environment file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_NoTimestampLoader)


def load_meta(data_dir: Path | str = DEFAULT_DATA_DIR) -> Meta:
    """Load environment facts (reference time, timezone, current user, reps)."""
    payload = _read_yaml(Path(data_dir) / META_FILENAME)
    if not isinstance(payload, dict):
        raise ValueError(f"{META_FILENAME} must contain a mapping")
    return Meta.model_validate(payload)


def load_dataset(data_dir: Path | str = DEFAULT_DATA_DIR) -> Dataset:
    """Load Golden and Background business facts into one runtime dataset."""
    root = Path(data_dir)
    collections: dict[str, list[Any]] = {
        field_name: [] for field_name in BUSINESS_FILES
    }
    for dirname in RUNTIME_DIRNAMES:
        source_dir = root / dirname
        for field_name, (filename, model) in BUSINESS_FILES.items():
            payload = _read_yaml(source_dir / filename)
            if payload is None:
                payload = []
            if not isinstance(payload, list):
                raise ValueError(f"{source_dir / filename} must contain a list of records")
            collections[field_name].extend(
                model.model_validate(item) for item in payload
            )
    return Dataset.model_validate(collections)


def load_environment(data_dir: Path | str = DEFAULT_DATA_DIR) -> tuple[Meta, Dataset]:
    """Load environment facts and the complete runtime dataset together."""
    return load_meta(data_dir), load_dataset(data_dir)

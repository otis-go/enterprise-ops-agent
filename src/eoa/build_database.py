"""Build the SQLite runtime artifact from validated YAML source data.

YAML remains the source of truth. Every build loads the complete environment
through :mod:`eoa.loader`, validates it with :mod:`eoa.validate`, writes a new
database in one transaction, then atomically replaces the requested artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel

from .constants import (
    CalendarEventStatus,
    EmailStatus,
    InteractionChannel,
    LifecycleStatus,
    OpportunityStage,
    TaskStatus,
)
from .loader import DEFAULT_DATA_DIR, PROJECT_ROOT, load_environment
from .models import (
    CalendarEvent,
    Customer,
    Dataset,
    Email,
    FollowUpTask,
    Interaction,
    Opportunity,
)
from .validate import validate_environment

DEFAULT_DATABASE_PATH = PROJECT_ROOT / "storage" / "enterprise_ops.db"


def _enum_sql(enum_type: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_type)


SCHEMA_SQL = (
    f"""
    CREATE TABLE customers (
        customer_id TEXT NOT NULL PRIMARY KEY,
        name TEXT NOT NULL,
        lifecycle_status TEXT NOT NULL
            CHECK (lifecycle_status IN ({_enum_sql(LifecycleStatus)})),
        contract_end_date TEXT,
        owner_rep TEXT NOT NULL,
        primary_contact_name TEXT NOT NULL,
        primary_contact_title TEXT,
        primary_contact_email TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE opportunities (
        opportunity_id TEXT NOT NULL PRIMARY KEY,
        customer_id TEXT NOT NULL,
        name TEXT NOT NULL,
        stage TEXT NOT NULL CHECK (stage IN ({_enum_sql(OpportunityStage)})),
        amount_cny INTEGER NOT NULL CHECK (amount_cny > 0),
        expected_close_date TEXT NOT NULL,
        actual_close_date TEXT,
        lost_reason TEXT,
        owner_rep TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    f"""
    CREATE TABLE interactions (
        interaction_id TEXT NOT NULL PRIMARY KEY,
        customer_id TEXT NOT NULL,
        opportunity_id TEXT,
        channel TEXT NOT NULL CHECK (channel IN ({_enum_sql(InteractionChannel)})),
        occurred_at TEXT NOT NULL,
        duration_minutes INTEGER CHECK (duration_minutes > 0),
        participants_internal TEXT NOT NULL
            CHECK (json_valid(participants_internal)
                   AND json_type(participants_internal) = 'array'),
        participants_customer TEXT NOT NULL
            CHECK (json_valid(participants_customer)
                   AND json_type(participants_customer) = 'array'),
        summary TEXT NOT NULL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    f"""
    CREATE TABLE followup_tasks (
        task_id TEXT NOT NULL PRIMARY KEY,
        customer_id TEXT NOT NULL,
        opportunity_id TEXT,
        title TEXT NOT NULL,
        description TEXT,
        owner TEXT NOT NULL,
        due_date TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ({_enum_sql(TaskStatus)})),
        created_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    f"""
    CREATE TABLE emails (
        email_id TEXT NOT NULL PRIMARY KEY,
        thread_id TEXT NOT NULL,
        customer_id TEXT,
        opportunity_id TEXT,
        status TEXT NOT NULL CHECK (status IN ({_enum_sql(EmailStatus)})),
        from_address TEXT NOT NULL,
        to_addresses TEXT NOT NULL
            CHECK (json_valid(to_addresses) AND json_type(to_addresses) = 'array'),
        cc_addresses TEXT NOT NULL
            CHECK (json_valid(cc_addresses) AND json_type(cc_addresses) = 'array'),
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        in_reply_to TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (in_reply_to) REFERENCES emails(email_id)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    f"""
    CREATE TABLE calendar_events (
        event_id TEXT NOT NULL PRIMARY KEY,
        title TEXT NOT NULL,
        customer_id TEXT,
        opportunity_id TEXT,
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        location TEXT,
        attendees_internal TEXT NOT NULL
            CHECK (json_valid(attendees_internal)
                   AND json_type(attendees_internal) = 'array'),
        attendees_customer TEXT NOT NULL
            CHECK (json_valid(attendees_customer)
                   AND json_type(attendees_customer) = 'array'),
        status TEXT NOT NULL CHECK (status IN ({_enum_sql(CalendarEventStatus)})),
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
)

TABLE_MODELS: dict[str, type[BaseModel]] = {
    "customers": Customer,
    "opportunities": Opportunity,
    "interactions": Interaction,
    "followup_tasks": FollowUpTask,
    "emails": Email,
    "calendar_events": CalendarEvent,
}
TABLE_NAMES = tuple(TABLE_MODELS)


class EnvironmentValidationError(ValueError):
    """Raised before database I/O when the runtime Dataset is invalid."""


@dataclass(frozen=True)
class BuildResult:
    database_path: Path
    counts: dict[str, int]

    @property
    def total_records(self) -> int:
        return sum(self.counts.values())


def connect_database(database_path: Path | str) -> sqlite3.Connection:
    """Open a database connection with SQLite foreign keys enforced."""
    connection = sqlite3.connect(Path(database_path))
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("SQLite foreign key enforcement could not be enabled")
    return connection


def _sqlite_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return int(value)
    return value


def _create_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_SQL:
        connection.execute(statement)


def _insert_dataset(connection: sqlite3.Connection, dataset: Dataset) -> None:
    for table_name, model in TABLE_MODELS.items():
        columns = tuple(model.model_fields)
        placeholders = ", ".join("?" for _ in columns)
        sql = (
            f"INSERT INTO {table_name} ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        rows = [
            tuple(_sqlite_value(getattr(record, column)) for column in columns)
            for record in getattr(dataset, table_name)
        ]
        connection.executemany(sql, rows)


def _dataset_counts(dataset: Dataset) -> dict[str, int]:
    return {table_name: len(getattr(dataset, table_name)) for table_name in TABLE_NAMES}


def build_database(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    data_dir: Path | str = DEFAULT_DATA_DIR,
) -> BuildResult:
    """Build and atomically publish a complete SQLite runtime database."""
    meta, dataset = load_environment(data_dir)
    violations = validate_environment(meta, dataset)
    if violations:
        detail = "\n".join(str(violation) for violation in violations)
        raise EnvironmentValidationError(
            f"runtime Dataset has {len(violations)} violation(s):\n{detail}"
        )

    target = Path(database_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")

    try:
        connection = connect_database(temporary)
        try:
            connection.execute("BEGIN")
            try:
                _create_schema(connection)
                _insert_dataset(connection, dataset)
                foreign_key_errors = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if foreign_key_errors:
                    raise sqlite3.IntegrityError(
                        f"foreign key check failed: {foreign_key_errors}"
                    )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.close()

        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return BuildResult(database_path=target, counts=_dataset_counts(dataset))


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the SQLite runtime artifact from validated YAML data."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args(argv)

    try:
        result = build_database(args.output, args.data_dir)
    except Exception as error:  # pragma: no cover - CLI display path
        print(f"Database build failed: {error}", file=sys.stderr)
        return 1

    print(f"Built {_display_path(result.database_path)}")
    print(f"{result.total_records} records")
    print(" ".join(f"{name}={count}" for name, count in result.counts.items()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

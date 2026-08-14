"""Phase 0C-2 tests for deterministic YAML -> SQLite rebuilding."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import pytest
import yaml

import eoa.build_database as builder
from eoa.build_database import TABLE_MODELS, TABLE_NAMES, build_database, connect_database
from eoa.loader import load_dataset
from eoa.models import Dataset

EXPECTED_COUNTS = {
    "customers": 24,
    "opportunities": 17,
    "interactions": 29,
    "followup_tasks": 23,
    "emails": 36,
    "calendar_events": 16,
}
EXPECTATION_NAMES = {
    "case_id",
    "should_prioritize",
    "expected_signals",
    "expected_tools",
    "must_not",
    "expected_answer",
}
PRIMARY_KEYS = {
    "customers": "customer_id",
    "opportunities": "opportunity_id",
    "interactions": "interaction_id",
    "followup_tasks": "task_id",
    "emails": "email_id",
    "calendar_events": "event_id",
}
NULLABLE_COLUMNS = {
    "customers": {"contract_end_date", "primary_contact_title"},
    "opportunities": {"actual_close_date", "lost_reason"},
    "interactions": {"opportunity_id", "duration_minutes"},
    "followup_tasks": {"opportunity_id", "description", "completed_at"},
    "emails": {"customer_id", "opportunity_id", "sent_at", "in_reply_to"},
    "calendar_events": {"customer_id", "opportunity_id", "location"},
}
FOREIGN_KEYS = {
    "customers": set(),
    "opportunities": {("customer_id", "customers", "customer_id")},
    "interactions": {
        ("customer_id", "customers", "customer_id"),
        ("opportunity_id", "opportunities", "opportunity_id"),
    },
    "followup_tasks": {
        ("customer_id", "customers", "customer_id"),
        ("opportunity_id", "opportunities", "opportunity_id"),
    },
    "emails": {
        ("customer_id", "customers", "customer_id"),
        ("opportunity_id", "opportunities", "opportunity_id"),
        ("in_reply_to", "emails", "email_id"),
    },
    "calendar_events": {
        ("customer_id", "customers", "customer_id"),
        ("opportunity_id", "opportunities", "opportunity_id"),
    },
}


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "runtime" / "enterprise_ops.db"


@pytest.fixture
def built_database(database_path: Path, data_dir: Path) -> Path:
    result = build_database(database_path, data_dir)
    assert result.database_path == database_path.resolve()
    return result.database_path


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        if not row[0].startswith("sqlite_")
    }


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table_name: connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]
        for table_name in TABLE_NAMES
    }


def test_database_builds_from_current_runtime_yaml(
    built_database: Path, dataset: Dataset
):
    assert built_database.is_file()
    with closing(connect_database(built_database)) as connection:
        assert _counts(connection) == {
            table_name: len(getattr(dataset, table_name))
            for table_name in TABLE_NAMES
        }
        assert sum(_counts(connection).values()) == 145


def test_database_has_exactly_six_business_tables_and_model_fields(
    built_database: Path,
):
    with closing(connect_database(built_database)) as connection:
        assert _table_names(connection) == set(TABLE_NAMES)
        for table_name, model in TABLE_MODELS.items():
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            assert columns == set(model.model_fields)


def test_schema_maps_primary_keys_nullability_and_foreign_keys(
    built_database: Path,
):
    with closing(connect_database(built_database)) as connection:
        for table_name in TABLE_NAMES:
            table_info = connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
            primary_keys = {row[1] for row in table_info if row[5] == 1}
            assert primary_keys == {PRIMARY_KEYS[table_name]}
            assert {row[1] for row in table_info if row[3] == 0} == NULLABLE_COLUMNS[
                table_name
            ]

            foreign_keys = {
                (row[3], row[2], row[4])
                for row in connection.execute(f"PRAGMA foreign_key_list({table_name})")
            }
            assert foreign_keys == FOREIGN_KEYS[table_name]


def test_key_golden_and_background_records_are_queryable(built_database: Path):
    with closing(connect_database(built_database)) as connection:
        assert connection.execute(
            "SELECT name FROM customers WHERE customer_id = ?", ("CUST-001",)
        ).fetchone() == ("恒泰精密制造有限公司",)
        assert connection.execute(
            "SELECT name FROM customers WHERE customer_id = ?", ("CUST-116",)
        ).fetchone() == ("晨光环保工程有限公司",)
        assert connection.execute(
            "SELECT stage FROM opportunities WHERE opportunity_id = ?", ("OPP-109",)
        ).fetchone() == ("won",)

        participants = connection.execute(
            "SELECT participants_internal FROM interactions WHERE interaction_id = ?",
            ("INT-003",),
        ).fetchone()[0]
        assert json.loads(participants) == ["rep_li_wei", "rep_chen_hao"]


def test_foreign_key_enforcement_is_enabled_and_effective(built_database: Path):
    with closing(connect_database(built_database)) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO opportunities (
                    opportunity_id, customer_id, name, stage, amount_cny,
                    expected_close_date, actual_close_date, lost_reason,
                    owner_rep, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "OPP-999", "CUST-999", "不存在客户的商机", "lead", 1000,
                    "2026-12-31", None, None, "rep_li_wei",
                    "2026-08-01T09:00:00+08:00",
                    "2026-08-01T09:00:00+08:00",
                ),
            )
            connection.commit()
        connection.rollback()


def test_consecutive_rebuilds_replace_instead_of_duplicate(
    database_path: Path, data_dir: Path
):
    first = build_database(database_path, data_dir)
    second = build_database(database_path, data_dir)

    assert first.counts == second.counts == EXPECTED_COUNTS
    with closing(connect_database(database_path)) as connection:
        assert _counts(connection) == EXPECTED_COUNTS


def test_rebuild_drops_records_removed_from_temporary_yaml(
    database_path: Path, data_dir: Path, tmp_path: Path
):
    temporary_data = tmp_path / "data"
    shutil.copytree(data_dir, temporary_data)
    build_database(database_path, temporary_data)

    emails_path = temporary_data / "background" / "emails.yaml"
    emails = yaml.safe_load(emails_path.read_text(encoding="utf-8"))
    emails = [email for email in emails if email["email_id"] != "EMAIL-120"]
    emails_path.write_text(
        yaml.safe_dump(emails, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    rebuilt = build_database(database_path, temporary_data)
    assert rebuilt.counts["emails"] == EXPECTED_COUNTS["emails"] - 1
    with closing(connect_database(database_path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM emails WHERE email_id = ?", ("EMAIL-120",)
        ).fetchone() == (0,)


@pytest.mark.parametrize("existing_database", [False, True])
def test_failed_build_does_not_publish_partial_database(
    database_path: Path,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_database: bool,
):
    original_bytes = None
    if existing_database:
        build_database(database_path, data_dir)
        original_bytes = database_path.read_bytes()
    original_insert = builder._insert_dataset

    def fail_after_insert(connection: sqlite3.Connection, dataset: Dataset) -> None:
        original_insert(connection, dataset)
        raise RuntimeError("injected failure after inserts")

    monkeypatch.setattr(builder, "_insert_dataset", fail_after_insert)
    with pytest.raises(RuntimeError, match="injected failure"):
        build_database(database_path, data_dir)

    if existing_database:
        assert database_path.read_bytes() == original_bytes
        with closing(connect_database(database_path)) as connection:
            assert _counts(connection) == EXPECTED_COUNTS
    else:
        assert not database_path.exists()
    assert not list(database_path.parent.glob("*.tmp"))


def test_foreign_key_check_failure_cleans_up_and_preserves_existing_database(
    database_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    build_database(database_path, data_dir)
    original_bytes = database_path.read_bytes()
    original_insert = builder._insert_dataset

    def insert_dangling_foreign_key(
        connection: sqlite3.Connection, dataset: Dataset
    ) -> None:
        original_insert(connection, dataset)
        connection.execute(
            """
            INSERT INTO opportunities (
                opportunity_id, customer_id, name, stage, amount_cny,
                expected_close_date, actual_close_date, lost_reason,
                owner_rep, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "OPP-999", "CUST-999", "故障注入商机", "lead", 1000,
                "2026-12-31", None, None, "rep_li_wei",
                "2026-08-01T09:00:00+08:00",
                "2026-08-01T09:00:00+08:00",
            ),
        )

    monkeypatch.setattr(builder, "_insert_dataset", insert_dangling_foreign_key)
    with pytest.raises(sqlite3.IntegrityError, match="foreign key check failed"):
        build_database(database_path, data_dir)

    assert database_path.read_bytes() == original_bytes
    assert not list(database_path.parent.glob("*.tmp"))
    with closing(connect_database(database_path)) as connection:
        assert _counts(connection) == EXPECTED_COUNTS
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_invalid_runtime_data_is_rejected_before_database_io(
    database_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    meta, dataset = builder.load_environment(data_dir)
    broken = dataset.model_copy(
        update={
            "interactions": [
                dataset.interactions[0].model_copy(update={"customer_id": "CUST-999"}),
                *dataset.interactions[1:],
            ]
        }
    )
    monkeypatch.setattr(builder, "load_environment", lambda _: (meta, broken))

    with pytest.raises(builder.EnvironmentValidationError, match="violation"):
        build_database(database_path, data_dir)
    assert not database_path.parent.exists()


def test_runtime_database_contains_no_eval_expectations(built_database: Path):
    with closing(connect_database(built_database)) as connection:
        assert _table_names(connection) == set(TABLE_NAMES)
        schema = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        ).lower()
        assert not EXPECTATION_NAMES & set(schema.replace("(", " ").split())

        all_columns = {
            row[1]
            for table_name in TABLE_NAMES
            for row in connection.execute(f"PRAGMA table_info({table_name})")
        }
        assert EXPECTATION_NAMES.isdisjoint(all_columns)


def test_database_rows_match_loaded_dataset_exactly(
    built_database: Path, data_dir: Path
):
    def assert_semantically_equal(database_value, model_value) -> None:
        if model_value is None:
            assert database_value is None
        elif isinstance(model_value, datetime):
            assert database_value == model_value.isoformat()
        elif isinstance(model_value, date):
            assert database_value == model_value.isoformat()
        elif isinstance(model_value, Enum):
            assert database_value == model_value.value
        elif isinstance(model_value, list):
            assert json.loads(database_value) == model_value
        else:
            assert database_value == model_value

    dataset = load_dataset(data_dir)
    with closing(connect_database(built_database)) as connection:
        for table_name, model in TABLE_MODELS.items():
            columns = tuple(model.model_fields)
            primary_key = next(
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table_name})")
                if row[5] == 1
            )
            database_rows = connection.execute(
                f"SELECT {', '.join(columns)} FROM {table_name} ORDER BY {primary_key}"
            ).fetchall()
            model_records = sorted(
                getattr(dataset, table_name),
                key=lambda record: getattr(record, primary_key),
            )
            assert len(database_rows) == len(model_records)
            for database_row, model_record in zip(
                database_rows, model_records, strict=True
            ):
                for column, database_value in zip(
                    columns, database_row, strict=True
                ):
                    assert_semantically_equal(
                        database_value, getattr(model_record, column)
                    )


def test_representative_sqlite_values_use_independent_literals(built_database: Path):
    """Spot-check storage encoding without reusing the production serializer."""
    with closing(connect_database(built_database)) as connection:
        customer = connection.execute(
            """
            SELECT created_at, contract_end_date, primary_contact_title
            FROM customers WHERE customer_id = ?
            """,
            ("CUST-001",),
        ).fetchone()
        assert customer == ("2026-06-10T09:30:00+08:00", None, "采购总监")

        opportunity = connection.execute(
            """
            SELECT amount_cny, expected_close_date, actual_close_date
            FROM opportunities WHERE opportunity_id = ?
            """,
            ("OPP-001",),
        ).fetchone()
        assert opportunity == (280000, "2026-08-28", None)

        participants = connection.execute(
            """
            SELECT participants_internal, participants_customer
            FROM interactions WHERE interaction_id = ?
            """,
            ("INT-003",),
        ).fetchone()
        assert participants == (
            '["rep_li_wei","rep_chen_hao"]',
            '["李国强","王倩"]',
        )


def test_cli_main_builds_database_and_reports_success(
    database_path: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    exit_code = builder.main(
        ["--data-dir", str(data_dir), "--output", str(database_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert database_path.is_file()
    assert "Built" in captured.out
    assert "145 records" in captured.out
    assert captured.err == ""
    with closing(connect_database(database_path)) as connection:
        assert _counts(connection) == EXPECTED_COUNTS


def test_cli_main_failure_reports_stderr_and_publishes_no_partial_database(
    database_path: Path,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    def fail_insert(connection: sqlite3.Connection, dataset: Dataset) -> None:
        raise RuntimeError("injected CLI build failure")

    monkeypatch.setattr(builder, "_insert_dataset", fail_insert)
    exit_code = builder.main(
        ["--data-dir", str(data_dir), "--output", str(database_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Database build failed: injected CLI build failure" in captured.err
    assert not database_path.exists()
    assert not list(database_path.parent.glob("*.tmp"))

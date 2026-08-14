"""Phase 0D final acceptance and system-fixture isolation tests."""

from __future__ import annotations

import sqlite3
import shutil
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Callable

import pytest
import yaml

import scripts.validate_data as validator
from eoa.build_database import TABLE_NAMES, build_database
from eoa.constants import OpportunityStage
from eoa.derive import (
    is_open_opportunity,
    is_overdue_task,
    unanswered_inbound_emails,
    upcoming_scheduled_events,
)
from eoa.loader import load_environment
from eoa.models import Dataset, Meta


def test_current_phase0_environment_passes_final_validation():
    result = validator.validate_data()

    assert result.record_count == 145
    assert result.case_ids == tuple(f"G00{number}" for number in range(1, 9))


@pytest.mark.parametrize(
    ("case_id", "customer_id"),
    [(f"G00{number}", f"CUST-00{number}") for number in range(1, 9)],
)
def test_each_golden_case_anchors_an_existing_customer(
    case_id: str,
    customer_id: str,
    cases_by_id: dict,
    dataset: Dataset,
):
    assert cases_by_id[case_id]["anchor_customer"] == customer_id
    assert customer_id in dataset.customers_by_id


def test_g005_has_no_active_opportunity(dataset: Dataset):
    assert not [
        opportunity
        for opportunity in dataset.opportunities
        if opportunity.customer_id == "CUST-005" and is_open_opportunity(opportunity)
    ]


def test_g004_has_a_future_meeting_after_reference_time(dataset: Dataset, meta: Meta):
    meetings = upcoming_scheduled_events(
        dataset.calendar_events,
        meta.reference_time,
        within_days=2,
        customer_id="CUST-004",
    )
    assert len(meetings) == 1
    assert meetings[0].start_at > meta.reference_time


def test_g006_contract_ends_in_exactly_twenty_days(dataset: Dataset, meta: Meta):
    customer = dataset.customers_by_id["CUST-006"]
    assert customer.contract_end_date is not None
    assert customer.contract_end_date - meta.reference_date == timedelta(days=20)


@pytest.mark.parametrize("customer_id", ["CUST-002", "CUST-007"])
def test_g002_and_g007_have_unanswered_inbound_email(
    customer_id: str, dataset: Dataset
):
    assert len(unanswered_inbound_emails(dataset.emails, customer_id)) == 1


def test_g008_has_no_overdue_followup(dataset: Dataset, meta: Meta):
    assert not any(
        task.customer_id == "CUST-008" and is_overdue_task(task, meta.reference_date)
        for task in dataset.followup_tasks
    )


def _replace_record(dataset: Dataset, collection: str, old, new) -> Dataset:
    records = list(getattr(dataset, collection))
    records[records.index(old)] = new
    return dataset.model_copy(update={collection: records})


def _lost_without_reason(meta: Meta, dataset: Dataset) -> Dataset:
    del meta
    record = next(item for item in dataset.opportunities if item.stage is OpportunityStage.LOST)
    return _replace_record(
        dataset,
        "opportunities",
        record,
        record.model_copy(update={"lost_reason": None}),
    )


def _active_with_actual_close(meta: Meta, dataset: Dataset) -> Dataset:
    record = next(item for item in dataset.opportunities if is_open_opportunity(item))
    return _replace_record(
        dataset,
        "opportunities",
        record,
        record.model_copy(update={"actual_close_date": meta.reference_date}),
    )


def _lost_without_actual_close(meta: Meta, dataset: Dataset) -> Dataset:
    del meta
    record = next(item for item in dataset.opportunities if item.stage is OpportunityStage.LOST)
    return _replace_record(
        dataset,
        "opportunities",
        record,
        record.model_copy(update={"actual_close_date": None}),
    )


def _draft_with_sent_at(meta: Meta, dataset: Dataset) -> Dataset:
    record = next(item for item in dataset.emails if item.status.value == "draft")
    return _replace_record(
        dataset,
        "emails",
        record,
        record.model_copy(update={"sent_at": meta.reference_time}),
    )


def _event_with_inverted_times(meta: Meta, dataset: Dataset) -> Dataset:
    del meta
    record = dataset.calendar_events[0]
    return _replace_record(
        dataset,
        "calendar_events",
        record,
        record.model_copy(update={"end_at": record.start_at}),
    )


def _interaction_with_dangling_customer(meta: Meta, dataset: Dataset) -> Dataset:
    del meta
    record = dataset.interactions[0]
    return _replace_record(
        dataset,
        "interactions",
        record,
        record.model_copy(update={"customer_id": "CUST-999"}),
    )


@pytest.mark.parametrize(
    ("break_dataset", "expected_error"),
    [
        (_lost_without_reason, "stage=lost requires lost_reason"),
        (_lost_without_actual_close, "stage=lost requires actual_close_date"),
        (_active_with_actual_close, "must not have actual_close_date"),
        (_draft_with_sent_at, "draft must not have sent_at"),
        (_event_with_inverted_times, "end_at is not after start_at"),
        (_interaction_with_dangling_customer, "customer_id=CUST-999 does not exist"),
    ],
    ids=[
        "lost-missing-reason",
        "lost-missing-close-date",
        "active-has-close-date",
        "draft-has-sent-at",
        "event-start-not-before-end",
        "dangling-customer-id",
    ],
)
def test_final_validator_rejects_invalid_runtime_facts(
    break_dataset: Callable[[Meta, Dataset], Dataset],
    expected_error: str,
    meta: Meta,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
):
    broken = break_dataset(meta, dataset)
    monkeypatch.setattr(validator, "load_environment", lambda _: (meta, broken))

    with pytest.raises(validator.Phase0DataValidationError, match=expected_error):
        validator.validate_data()


def test_crm_timeout_fixture_is_minimal_and_independently_readable(
    project_root: Path,
):
    fixture_path = project_root / "data" / "fixtures" / "crm_timeout.yaml"
    payload = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))

    assert payload == {
        "fixture_id": "CRM-TIMEOUT-001",
        "system": "crm",
        "operation": "query",
        "failure": "timeout",
    }


def test_crm_timeout_fixture_is_not_loaded_into_runtime_or_sqlite(
    tmp_path: Path,
    data_dir: Path,
):
    _, runtime_dataset = load_environment(data_dir)
    assert sum(len(getattr(runtime_dataset, table)) for table in TABLE_NAMES) == 145

    database_path = tmp_path / "enterprise_ops.db"
    build_database(database_path, data_dir)
    with closing(sqlite3.connect(database_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if not row[0].startswith("sqlite_")
        }
        dump = "\n".join(connection.iterdump())

    assert tables == set(TABLE_NAMES)
    assert "CRM-TIMEOUT-001" not in dump
    assert "fixture_id" not in dump


def test_misplaced_crm_timeout_fixture_fails_hygiene_but_loader_ignores_it(
    tmp_path: Path,
    data_dir: Path,
):
    temporary_data = tmp_path / "data"
    shutil.copytree(data_dir, temporary_data)
    misplaced = temporary_data / "golden" / "crm_timeout.yaml"
    shutil.copy2(temporary_data / "fixtures" / "crm_timeout.yaml", misplaced)

    _, runtime_dataset = load_environment(temporary_data)
    assert sum(len(getattr(runtime_dataset, table)) for table in TABLE_NAMES) == 145

    with pytest.raises(
        validator.Phase0DataValidationError,
        match=r"\[P0D-ISOLATION\].*crm_timeout\.yaml",
    ):
        validator.validate_data(
            data_dir=temporary_data,
            crm_timeout_path=temporary_data / "fixtures" / "crm_timeout.yaml",
        )


def test_validate_data_cli_reports_success(capsys: pytest.CaptureFixture[str]):
    exit_code = validator.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines() == [
        "Phase 0 data validation passed",
        "145 runtime records",
        "G001-G008 ground truth verified",
        "CRM timeout fixture isolated",
    ]
    assert captured.err == ""


def test_validate_data_cli_reports_failure_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    failure = validator.Phase0DataValidationError(
        ["[C7] opportunity OPP-999: stage=lost requires lost_reason"]
    )
    monkeypatch.setattr(validator, "validate_data", lambda: (_ for _ in ()).throw(failure))

    assert validator.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stage=lost requires lost_reason" in captured.err

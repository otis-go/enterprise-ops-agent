"""Phase 0C-1 coverage for Golden + Background runtime data."""

from __future__ import annotations

from pathlib import Path

import yaml

from eoa.constants import OpportunityStage
from eoa.derive import is_overdue_task
from eoa.models import Dataset, Meta
from eoa.validate import validate_environment

GOLDEN_CUSTOMER_IDS = {f"CUST-00{number}" for number in range(1, 9)}
ENTITY_FILES = {
    "customers": "customers.yaml",
    "opportunities": "opportunities.yaml",
    "interactions": "interactions.yaml",
    "followup_tasks": "followup_tasks.yaml",
    "emails": "emails.yaml",
    "calendar_events": "calendar_events.yaml",
}


def _records(data_dir: Path, dirname: str, filename: str) -> list[dict]:
    payload = yaml.safe_load((data_dir / dirname / filename).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def _customer_ids(data_dir: Path, dirname: str) -> set[str]:
    return {
        record["customer_id"]
        for record in _records(data_dir, dirname, "customers.yaml")
    }


def test_customer_partition_is_exact(dataset: Dataset, data_dir: Path):
    golden_ids = _customer_ids(data_dir, "golden")
    background_ids = _customer_ids(data_dir, "background")
    runtime_ids = set(dataset.customers_by_id)

    assert golden_ids == GOLDEN_CUSTOMER_IDS
    assert len(golden_ids) == 8
    assert len(background_ids) == 16
    assert len(runtime_ids) == 24
    assert golden_ids.isdisjoint(background_ids)
    assert runtime_ids == golden_ids | background_ids


def test_each_runtime_directory_has_all_six_entity_files(data_dir: Path):
    for dirname in ("golden", "background"):
        assert {path.name for path in (data_dir / dirname).glob("*.yaml")} == set(
            ENTITY_FILES.values()
        )


def test_runtime_totals_equal_both_sources(dataset: Dataset, data_dir: Path):
    for field_name, filename in ENTITY_FILES.items():
        expected = len(_records(data_dir, "golden", filename)) + len(
            _records(data_dir, "background", filename)
        )
        assert len(getattr(dataset, field_name)) == expected


def test_complete_runtime_data_has_valid_foreign_keys_and_business_rules(
    meta: Meta, dataset: Dataset
):
    violations = validate_environment(meta, dataset)
    assert violations == [], "\n".join(str(violation) for violation in violations)


def test_runtime_has_a_real_overdue_background_task(
    meta: Meta, dataset: Dataset, data_dir: Path
):
    background_ids = _customer_ids(data_dir, "background")
    overdue_background_tasks = [
        task
        for task in dataset.followup_tasks
        if task.customer_id in background_ids
        and is_overdue_task(task, meta.reference_date)
    ]
    assert overdue_background_tasks


def test_runtime_has_won_background_opportunity(dataset: Dataset, data_dir: Path):
    background_ids = _customer_ids(data_dir, "background")
    won_background_opportunities = [
        opportunity
        for opportunity in dataset.opportunities
        if opportunity.customer_id in background_ids
        and opportunity.stage is OpportunityStage.WON
    ]
    assert won_background_opportunities


def test_zhang_min_and_chen_hao_own_background_customers(
    dataset: Dataset, data_dir: Path
):
    background_ids = _customer_ids(data_dir, "background")
    background = [
        customer
        for customer in dataset.customers
        if customer.customer_id in background_ids
    ]
    owners = {customer.owner_rep for customer in background}
    assert {"rep_zhang_min", "rep_chen_hao"} <= owners


def test_no_background_customer_is_added_as_a_golden_case(
    golden_cases: list[dict], data_dir: Path
):
    background_ids = _customer_ids(data_dir, "background")
    anchors = {case["anchor_customer"] for case in golden_cases}
    assert anchors == GOLDEN_CUSTOMER_IDS
    assert anchors.isdisjoint(background_ids)

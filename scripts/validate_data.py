"""Offline Phase 0 acceptance validation.

This script deliberately sits outside ``src/eoa``: it may read evaluation
fixtures while runtime code remains limited to business facts.  Business data
still follows the production path ``loader -> Pydantic -> validate_environment``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from eoa.constants import CalendarEventStatus, OpportunityStage  # noqa: E402
from eoa.derive import (  # noqa: E402
    days_since_last_interaction,
    is_due_today,
    is_open_opportunity,
    is_overdue_task,
    unanswered_inbound_emails,
    upcoming_scheduled_events,
)
from eoa.loader import (  # noqa: E402
    BUSINESS_FILES,
    DEFAULT_DATA_DIR,
    RUNTIME_DIRNAMES,
    load_environment,
)
from eoa.models import Dataset, Meta  # noqa: E402
from eoa.validate import validate_environment  # noqa: E402

DEFAULT_GOLDEN_CASES_PATH = PROJECT_ROOT / "tests" / "fixtures" / "golden_cases.yaml"
DEFAULT_CRM_TIMEOUT_PATH = PROJECT_ROOT / "data" / "fixtures" / "crm_timeout.yaml"

EXPECTED_CASE_IDS = tuple(f"G00{number}" for number in range(1, 9))
EXPECTED_COUNTS = {
    "customers": 24,
    "opportunities": 17,
    "interactions": 29,
    "followup_tasks": 23,
    "emails": 36,
    "calendar_events": 16,
}


@dataclass(frozen=True)
class ValidationResult:
    record_count: int
    case_ids: tuple[str, ...]


class Phase0DataValidationError(ValueError):
    """All final acceptance failures from one validation run."""

    def __init__(self, failures: list[str]):
        self.failures = tuple(failures)
        super().__init__("Phase 0 data validation failed:\n" + "\n".join(failures))


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"missing offline validation fixture: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _cases_by_id(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_yaml(path)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{path} must contain a list of mappings")
    case_ids = [item.get("case_id") for item in payload]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError(f"{path} contains duplicate case_id values")
    return {item["case_id"]: item for item in payload}


def _verify_runtime_counts(dataset: Dataset, fail: Callable[[str, str], None]) -> None:
    for collection, expected in EXPECTED_COUNTS.items():
        actual = len(getattr(dataset, collection))
        if actual != expected:
            fail("P0D-COUNT", f"{collection} expected {expected}, found {actual}")


def _verify_runtime_directory_hygiene(
    data_dir: Path, fail: Callable[[str, str], None]
) -> None:
    allowed_filenames = {filename for filename, _ in BUSINESS_FILES.values()}
    for dirname in RUNTIME_DIRNAMES:
        source_dir = data_dir / dirname
        allowed_paths = {
            (source_dir / filename).resolve() for filename in allowed_filenames
        }
        for path in sorted(source_dir.rglob("*")):
            if (
                path.is_file()
                and path.suffix.lower() in {".yaml", ".yml"}
                and path.resolve() not in allowed_paths
            ):
                fail("P0D-ISOLATION", f"unexpected runtime YAML file: {path}")


def _verify_ground_truth(
    meta: Meta,
    dataset: Dataset,
    cases: dict[str, dict[str, Any]],
    fail: Callable[[str, str], None],
) -> None:
    if tuple(sorted(cases)) != EXPECTED_CASE_IDS:
        fail(
            "P0D-GT",
            f"expected case ids {list(EXPECTED_CASE_IDS)}, found {sorted(cases)}",
        )
        return

    expected_customers = tuple(f"CUST-00{number}" for number in range(1, 9))
    anchored_customers = tuple(cases[case_id].get("anchor_customer") for case_id in EXPECTED_CASE_IDS)
    if anchored_customers != expected_customers:
        fail("P0D-GT", f"G001-G008 customer anchors changed: {anchored_customers}")

    for case_id, customer_id in zip(EXPECTED_CASE_IDS, expected_customers, strict=True):
        if customer_id not in dataset.customers_by_id:
            fail("P0D-GT", f"{case_id} anchor customer {customer_id} does not exist")

    def opportunity(case_id: str):
        opportunity_id = cases[case_id].get("anchor_opportunity")
        record = dataset.opportunities_by_id.get(opportunity_id)
        if record is None:
            fail("P0D-GT", f"{case_id} anchor opportunity {opportunity_id!r} does not exist")
        elif record.customer_id != cases[case_id]["anchor_customer"]:
            fail("P0D-GT", f"{case_id} opportunity belongs to {record.customer_id}")
        return record

    g001 = opportunity("G001")
    if g001 is not None:
        if g001.stage is not OpportunityStage.PROPOSAL:
            fail("P0D-G001", "anchor opportunity is not an active proposal")
        if not any(
            task.customer_id == "CUST-001" and is_due_today(task, meta.reference_date)
            for task in dataset.followup_tasks
        ):
            fail("P0D-G001", "no actionable follow-up is due on the reference date")
    if days_since_last_interaction(dataset.interactions, "CUST-001", meta.reference_time) != 8:
        fail("P0D-G001", "last interaction is not exactly 8 days before reference time")

    g002_unanswered = unanswered_inbound_emails(dataset.emails, "CUST-002")
    if len(g002_unanswered) != 1:
        fail("P0D-G002", f"expected one unanswered inbound email, found {len(g002_unanswered)}")
    else:
        inbound = g002_unanswered[0]
        if inbound.sent_at is None or (meta.reference_date - inbound.sent_at.date()).days != 1:
            fail("P0D-G002", "unanswered inbound email was not sent one day ago")
        thread = [email for email in dataset.emails if email.thread_id == inbound.thread_id]
        drafts = [email for email in thread if email.status.value == "draft"]
        if not drafts or any(draft.sent_at is not None for draft in drafts):
            fail("P0D-G002", "unanswered thread no longer contains an unsent draft")

    g003 = opportunity("G003")
    if g003 is not None:
        if g003.stage is not OpportunityStage.NEGOTIATION:
            fail("P0D-G003", "anchor opportunity is not an active negotiation")
        if g003.amount_cny != 500_000:
            fail("P0D-G003", f"amount_cny changed to {g003.amount_cny}")
        if (meta.reference_date - g003.updated_at.date()).days != 12:
            fail("P0D-G003", "opportunity was not last updated 12 days ago")
    if days_since_last_interaction(dataset.interactions, "CUST-003", meta.reference_time) != 12:
        fail("P0D-G003", "last interaction is not exactly 12 days before reference time")

    g004 = opportunity("G004")
    if g004 is not None and g004.stage is not OpportunityStage.PROPOSAL:
        fail("P0D-G004", "anchor opportunity is no longer a proposal")
    g004_events = upcoming_scheduled_events(
        dataset.calendar_events,
        meta.reference_time,
        within_days=2,
        customer_id="CUST-004",
    )
    if len(g004_events) != 1:
        fail("P0D-G004", f"expected one future meeting within two days, found {len(g004_events)}")
    elif g004 is not None and g004_events[0].opportunity_id != g004.opportunity_id:
        fail("P0D-G004", "future meeting is not linked to the anchor opportunity")

    g005 = opportunity("G005")
    if g005 is not None:
        if (
            g005.stage is not OpportunityStage.LOST
            or g005.actual_close_date is None
            or g005.actual_close_date > meta.reference_date
            or g005.lost_reason != "budget_cancelled"
        ):
            fail("P0D-G005", "lost opportunity facts have drifted")
    active_g005 = [
        item
        for item in dataset.opportunities
        if item.customer_id == "CUST-005" and is_open_opportunity(item)
    ]
    if active_g005:
        fail("P0D-G005", f"active opportunities exist: {[item.opportunity_id for item in active_g005]}")
    if unanswered_inbound_emails(dataset.emails, "CUST-005"):
        fail("P0D-G005", "an unanswered inbound email exists")
    if any(
        task.customer_id == "CUST-005" and is_overdue_task(task, meta.reference_date)
        for task in dataset.followup_tasks
    ):
        fail("P0D-G005", "an overdue follow-up exists")

    g006 = dataset.customers_by_id.get("CUST-006")
    if g006 is not None:
        if g006.lifecycle_status.value != "customer" or g006.contract_end_date is None:
            fail("P0D-G006", "anchor is not an active customer with a contract end date")
        elif (g006.contract_end_date - meta.reference_date).days != 20:
            fail("P0D-G006", "contract does not end exactly 20 days after reference date")
    if cases["G006"].get("anchor_opportunity") is not None:
        fail("P0D-G006", "evaluation fixture unexpectedly anchors an opportunity")
    if any(item.customer_id == "CUST-006" for item in dataset.opportunities):
        fail("P0D-G006", "a renewal opportunity exists")
    if any(
        event.customer_id == "CUST-006" and event.status is CalendarEventStatus.SCHEDULED
        for event in dataset.calendar_events
    ):
        fail("P0D-G006", "a renewal meeting is scheduled")

    g007_unanswered = unanswered_inbound_emails(dataset.emails, "CUST-007")
    if len(g007_unanswered) != 1:
        fail("P0D-G007", f"expected one unanswered inbound email, found {len(g007_unanswered)}")
    else:
        inbound = g007_unanswered[0]
        if (
            inbound.sent_at is None
            or (meta.reference_date - inbound.sent_at.date()).days != 1
            or "功能" not in inbound.body
        ):
            fail("P0D-G007", "unanswered product question facts have drifted")
    if any(task.customer_id == "CUST-007" for task in dataset.followup_tasks):
        fail("P0D-G007", "product question has unexpectedly become a follow-up task")

    if days_since_last_interaction(dataset.interactions, "CUST-008", meta.reference_time) != 2:
        fail("P0D-G008", "last interaction is not exactly 2 days before reference time")
    if any(
        task.customer_id == "CUST-008" and is_overdue_task(task, meta.reference_date)
        for task in dataset.followup_tasks
    ):
        fail("P0D-G008", "an overdue follow-up exists")
    if unanswered_inbound_emails(dataset.emails, "CUST-008"):
        fail("P0D-G008", "an unanswered inbound email exists")


def _verify_crm_timeout_fixture(
    path: Path, payload: Any, fail: Callable[[str, str], None]
) -> None:
    expected = {
        "fixture_id": "CRM-TIMEOUT-001",
        "system": "crm",
        "operation": "query",
        "failure": "timeout",
    }
    if payload != expected:
        fail("P0D-FIXTURE", f"{path} must contain only {expected}")
    if "fixtures" in RUNTIME_DIRNAMES:
        fail("P0D-FIXTURE", "runtime loader includes the fixtures directory")
    if path.parent.name in RUNTIME_DIRNAMES:
        fail("P0D-FIXTURE", f"timeout fixture is inside runtime source {path.parent.name!r}")


def validate_data(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    golden_cases_path: Path | str = DEFAULT_GOLDEN_CASES_PATH,
    crm_timeout_path: Path | str = DEFAULT_CRM_TIMEOUT_PATH,
) -> ValidationResult:
    """Validate all Phase 0 facts and offline acceptance fixtures."""
    data_root = Path(data_dir)
    meta, dataset = load_environment(data_root)
    failures: list[str] = []

    def fail(rule: str, message: str) -> None:
        failures.append(f"[{rule}] {message}")

    for violation in validate_environment(meta, dataset):
        failures.append(str(violation))

    _verify_runtime_counts(dataset, fail)
    _verify_runtime_directory_hygiene(data_root, fail)
    cases = _cases_by_id(Path(golden_cases_path))
    _verify_ground_truth(meta, dataset, cases, fail)
    timeout_fixture = _read_yaml(Path(crm_timeout_path))
    _verify_crm_timeout_fixture(Path(crm_timeout_path), timeout_fixture, fail)

    if failures:
        raise Phase0DataValidationError(failures)

    return ValidationResult(
        record_count=sum(len(getattr(dataset, name)) for name in EXPECTED_COUNTS),
        case_ids=tuple(sorted(cases)),
    )


def main() -> int:
    try:
        result = validate_data()
    except Exception as error:
        print(error, file=sys.stderr)
        return 1

    print("Phase 0 data validation passed")
    print(f"{result.record_count} runtime records")
    print("G001-G008 runtime scenario facts verified")
    print("CRM timeout fixture isolated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

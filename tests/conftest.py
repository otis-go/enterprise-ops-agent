"""Shared fixtures.

Note the asymmetry, which is intentional:

* ``meta`` and ``dataset`` come from ``data/`` through the ordinary runtime
  loader — tests see exactly what the runtime sees.
* ``golden_cases`` is read here, in the test package, with its own YAML call.
  ``src/eoa`` has no code path that can reach it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from eoa.loader import load_dataset, load_meta
from eoa.models import Dataset, Meta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SRC_DIR = PROJECT_ROOT / "src"
GOLDEN_CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "golden_cases.yaml"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def src_dir() -> Path:
    return SRC_DIR


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def meta() -> Meta:
    return load_meta(DATA_DIR)


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return load_dataset(DATA_DIR)


@pytest.fixture(scope="session")
def golden_cases() -> list[dict[str, Any]]:
    with GOLDEN_CASES_PATH.open("r", encoding="utf-8") as handle:
        cases = yaml.safe_load(handle)
    assert isinstance(cases, list)
    return cases


@pytest.fixture(scope="session")
def cases_by_id(golden_cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {case["case_id"]: case for case in golden_cases}

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

DATA = ROOT / "data"


@pytest.fixture(scope="session")
def pings() -> pd.DataFrame:
    return pd.read_csv(DATA / "synthetic_gps_pings.csv", dtype=str, keep_default_na=False)


@pytest.fixture(scope="session")
def labels() -> pd.DataFrame:
    return pd.read_csv(DATA / "ping_labels.csv", dtype=str, keep_default_na=False)


@pytest.fixture(scope="session")
def expected() -> pd.DataFrame:
    return pd.read_csv(DATA / "expected_results.csv")


@pytest.fixture(scope="session")
def corrected_result(pings):
    from src.distance_calculator import process
    return process(pings)

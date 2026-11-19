"""Smoke test: the Streamlit dashboard renders without exceptions and shows the key metrics."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_dashboard_renders_default_dataset():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception, at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Total GPS records"] == "2894"
    assert labels["Rejected records"] == "200"
    assert labels["Distance before fix (km)"] == "434.25"
    assert labels["Distance after fix (km)"] == "134.32"


def test_dashboard_utc9_variant_renders():
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(s for s in at.selectbox if s.label == "Daily report cut").set_value("00:00 UTC+9").run()
    assert not at.exception, at.exception
    assert {m.label: m.value for m in at.metric}["Distance after fix (km)"] == "134.32"

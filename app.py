"""QA reporting dashboard.  Run:  streamlit run app.py

SYNTHETIC DATA ONLY.  The "original" numbers come from an intentionally defective baseline written for this
assessment (baseline/faulty_calculator.py) - not from any employer's production system.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from baseline.faulty_calculator import calculate_daily_distance  # noqa: E402
from src.defect_detector import ADVERTISER_RATE_USD_PER_KM, anomaly_scan, detect_defects  # noqa: E402
from src.distance_calculator import EngineConfig, process  # noqa: E402
from src.geo import to_latlon  # noqa: E402
from src.reporting import build_before_after, financial_summary  # noqa: E402

st.set_page_config(page_title="GPS distance QA", layout="wide")
st.title("GPS campaign-distance QA dashboard")
st.warning("All data is SYNTHETIC. The 'original' calculation is an intentionally defective baseline written for this "
           "assessment, not an employer's production code.", icon="⚠️")


@st.cache_data(show_spinner="Running corrected engine, baseline and defect ablations...")
def compute(csv_bytes: bytes | None, offset: float):
    import io
    pings = pd.read_csv(io.BytesIO(csv_bytes) if csv_bytes else ROOT / "data" / "synthetic_gps_pings.csv", dtype=str, keep_default_na=False)
    cfg = EngineConfig(report_utc_offset_hours=offset)
    res = process(pings, cfg)
    base = calculate_daily_distance(pings, offset)
    ba = build_before_after(pings, base, cfg)
    findings = detect_defects(pings, cfg)
    return pings, res, base, ba, findings, anomaly_scan(pings, cfg)


with st.sidebar:
    st.header("Inputs")
    up = st.file_uploader("Ping CSV (optional; default = data/synthetic_gps_pings.csv)", type="csv")
    offset = st.selectbox("Daily report cut", [0.0, 9.0], format_func=lambda v: "00:00 UTC" if v == 0 else "00:00 UTC+9")
    st.caption(f"Synthetic advertiser rate: USD {ADVERTISER_RATE_USD_PER_KM:.2f} / km")
    st.caption("No AI model runs inside this tool: AI (Claude) was used to BUILD it; see reports/ai_usage_log.md.")

try:
    pings, res, base, ba, findings, scan = compute(up.getvalue() if up else None, offset)
except ValueError as exc:
    st.error(f"Input rejected: {exc}")
    st.stop()
fs = financial_summary(ba)

c = st.columns(6)
c[0].metric("Total GPS records", scan["total_records"])
c[1].metric("Valid records", scan["valid_records"])
c[2].metric("Rejected records", scan["rejected_records"])
c[3].metric("Duplicates", scan["duplicate_records"])
c[4].metric("GPS noise (>100 m)", scan["gps_noise_records"])
c[5].metric("Impossible speed", scan["impossible_speed_records"])
c = st.columns(5)
c[0].metric("Distance before fix (km)", f"{fs['original_km']:.2f}")
c[1].metric("Distance after fix (km)", f"{fs['corrected_km']:.2f}")
c[2].metric("Difference (km)", f"{fs['difference_km']:+.2f}", f"{fs['difference_pct']:+.0f}%", delta_color="inverse")
c[3].metric("Advertiser over-billing (USD)", f"{fs['advertiser_overbilling_usd']:+.2f}")
c[4].metric("Driver payout error (USD)", f"{fs['driver_payout_error_usd']:+.2f}")

t1, t2, t3, t4, t5 = st.tabs(["Per-driver daily distance", "Defect summary", "Rejected pings (audit)", "Map", "Data quality"])
with t1:
    st.dataframe(ba, width="stretch", hide_index=True)
    chart = ba.pivot_table(index="driver_id", values=["original_km", "corrected_km"], aggfunc="sum")
    st.bar_chart(chart)
with t2:
    st.dataframe(pd.DataFrame([{
        "ID": f.defect_id, "Title": f.title, "Severity": f.severity, "Priority": f.priority, "Records": f.records_affected,
        "Net km": round(f.net_km, 3), "Abs driver-day km": round(f.abs_driver_day_km, 3),
        "Advertiser USD": round(f.advertiser_impact_usd, 2), "Driver USD": round(f.driver_impact_usd, 2),
        "Drivers": ", ".join(f.affected_drivers)} for f in findings]), width="stretch", hide_index=True)
    for f in findings:
        with st.expander(f"{f.defect_id} - {f.title}"):
            st.write("**Root cause:** " + f.root_cause)
            st.write("**Violated rule:** " + f.rule)
            for e in f.evidence:
                st.code(e)
    st.caption("Impact = km added when ONLY that rule is removed from the corrected engine (marginal). "
               "Overlapping rules mask each other (e.g. a 400 m offset also implies >150 km/h), so impacts do not sum to the total.")
with t3:
    reason = st.multiselect("Reason", sorted(res.audit["reject_reason"].dropna().unique()))
    a = res.audit[res.audit["status"] == "REJECTED"]
    st.dataframe(a[a["reject_reason"].isin(reason)] if reason else a, width="stretch", hide_index=True)
with t4:
    d = st.selectbox("Driver", sorted(pings["driver_id"].replace("", pd.NA).dropna().unique()))
    m = pings.merge(res.audit[["ping_id", "status", "reject_reason"]], on="ping_id")
    m = m[m["driver_id"] == d].copy()
    m["lat"] = pd.to_numeric(m["lat"], errors="coerce"); m["lon"] = pd.to_numeric(m["lon"], errors="coerce")
    m = m.dropna(subset=["lat", "lon"])
    m = m[(m.lat.abs() <= 90) & (m.lon.abs() <= 180)]
    m["color"] = m["status"].map({"ACCEPTED": "#2e7d32", "REJECTED": "#c62828"})
    st.map(m, latitude="lat", longitude="lon", color="color", size=20)
    st.caption("green = accepted, red = rejected")
with t5:
    st.json(scan)

"""Mutation check: inject one bug at a time into the corrected engine; the suite must FAIL for each.
Run: python tools_mutation_check.py   (restores sources afterwards)"""
import shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MUTANTS = [
    ("src/gps_validator.py", "return round(distance_m, THRESHOLD_PRECISION) > max_offset_m", "return round(distance_m, THRESHOLD_PRECISION) >= max_offset_m", "off-road uses >= (exactly 100 m wrongly rejected)"),
    ("src/gps_validator.py", "round(speed_kmh, THRESHOLD_PRECISION) > max_speed_kmh", "round(speed_kmh, THRESHOLD_PRECISION) >= max_speed_kmh", "speed uses >= (exactly 150 km/h wrongly rejected)"),
    ("src/distance_calculator.py", "gap = cfg.gap_rule and dt > cfg.max_gap_s", "gap = cfg.gap_rule and dt >= cfg.max_gap_s", "gap rule off-by-one (30 s edge not billed)"),
    ("src/distance_calculator.py", 'day_mode: str = "split"', 'day_mode: str = "end"', "midnight segment not split"),
    ("src/distance_calculator.py", 'ordering: str = "event"', 'ordering: str = "arrival"', "arrival-order processing"),
    ("src/distance_calculator.py", "dedupe: bool = True", "dedupe: bool = False", "duplicates not collapsed"),
    ("src/distance_calculator.py", 'zone_mode: str = "clip"', 'zone_mode: str = "endpoint"', "zone END-ping rule"),
    ("src/distance_calculator.py", "report_utc_offset_hours: float = 0.0", "report_utc_offset_hours: float = 9.0", "wrong default day cut"),
    ("src/campaign_zone.py", "if cross < -1e-9:", "if cross <= 1e-9:", "zone boundary treated as outside"),
]
rows = []
for rel, old, new, label in MUTANTS:
    f = ROOT / rel
    orig = f.read_text()
    assert old in orig, (rel, old)
    f.write_text(orig.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                            "--deselect", "tests/test_regression.py::test_meta_regression_test_PASSES_on_corrected",
                            "--deselect", "tests/test_regression.py::test_meta_regression_test_FAILS_on_baseline"],
                           cwd=ROOT, capture_output=True, text=True)
        last = r.stdout.strip().splitlines()[-1]
        fails = [l.split(" ")[1].split("::", 1)[1].split(" -")[0] for l in r.stdout.splitlines() if l.startswith("FAILED")]
        skip = ("dataset", "oracle", "other_seeds", "dashboard", "scan_counts", "before_after", "defects_are", "ground_truth", "regression_test")
        unit = [f for f in fails if not any(k in f for k in skip)]
        rows.append((label, "KILLED" if r.returncode != 0 else "SURVIVED", str(len(fails)),
                     (unit[0] if unit else "(only dataset/oracle tests)")[:70]))
    finally:
        f.write_text(orig)
print("| Mutant | Result | Failing tests | A non-dataset test that failed |\n|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(r) + " |")

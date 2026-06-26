#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COVERAGE_OUT="${COVERAGE_OUT:-/coverage-out}"
COVERAGE_MIN="${COVERAGE_MIN:-45}"
C_OUT="${COVERAGE_OUT}/c"
PY_OUT="${COVERAGE_OUT}/python"

rm -rf "$C_OUT" "$PY_OUT"
mkdir -p "$C_OUT/html" "$PY_OUT/html"

meson_status=0
pytest_status=0
c_coverage_status=0
c_html_status=0
summary_status=0

meson test -C libageos/build --print-errorlogs || meson_status=$?

COVERAGE_FILE="${PY_OUT}/.coverage" pytest -m "not integration" \
    --cov=ageos \
    --cov-report=term-missing \
    --cov-report="xml:${PY_OUT}/coverage.xml" \
    --cov-report="html:${PY_OUT}/html" \
    --cov-fail-under="$COVERAGE_MIN" \
    "$@" || pytest_status=$?

ninja -C libageos/build coverage-xml || c_coverage_status=$?
ninja -C libageos/build coverage-html || c_html_status=$?

if [[ -f libageos/build/meson-logs/coverage.xml ]]; then
    cp libageos/build/meson-logs/coverage.xml "$C_OUT/coverage.xml"
fi
if [[ -d libageos/build/meson-logs/coveragereport ]]; then
    rm -rf "$C_OUT/html"
    cp -a libageos/build/meson-logs/coveragereport "$C_OUT/html"
fi

python3 - "$COVERAGE_OUT" "$COVERAGE_MIN" <<'PY' || summary_status=$?
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

coverage_out = Path(sys.argv[1])
threshold = float(sys.argv[2])


def root_line_coverage(xml_path: Path) -> float | None:
    if not xml_path.exists():
        return None

    root = ET.parse(xml_path).getroot()
    line_rate = root.attrib.get("line-rate")
    if line_rate is not None:
        return round(float(line_rate) * 100, 2)

    lines_valid = int(root.attrib.get("lines-valid", "0"))
    lines_covered = int(root.attrib.get("lines-covered", "0"))
    if lines_valid == 0:
        return None
    return round(lines_covered / lines_valid * 100, 2)


def class_line_totals(class_node: ET.Element) -> tuple[int, int]:
    lines = class_node.findall(".//line")
    valid = len(lines)
    covered = sum(1 for line in lines if int(line.attrib.get("hits", "0")) > 0)
    return valid, covered


def c_entry(xml_path: Path) -> dict[str, object]:
    if not xml_path.exists():
        return {"line_coverage": None, "threshold": threshold, "status": "unavailable"}

    root = ET.parse(xml_path).getroot()
    total_valid = 0
    total_covered = 0

    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename", "")
        basename = Path(filename).name
        if not basename.endswith(".c") or basename.endswith("_test.c"):
            continue
        valid, covered = class_line_totals(class_node)
        total_valid += valid
        total_covered += covered

    coverage = None if total_valid == 0 else round(total_covered / total_valid * 100, 2)
    return {
        "line_coverage": coverage,
        "threshold": threshold,
        "status": "unavailable" if coverage is None else ("pass" if coverage >= threshold else "fail"),
    }


def entry(xml_path: Path) -> dict[str, object]:
    coverage = root_line_coverage(xml_path)
    return {
        "line_coverage": coverage,
        "threshold": threshold,
        "status": "unavailable" if coverage is None else ("pass" if coverage >= threshold else "fail"),
    }


summary = {
    "threshold": threshold,
    "c": c_entry(coverage_out / "c" / "coverage.xml"),
    "python": entry(coverage_out / "python" / "coverage.xml"),
}

(coverage_out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

if summary["c"]["status"] != "pass":
    raise SystemExit(1)
PY

chmod -R a+rX "$COVERAGE_OUT"

if (( meson_status != 0 || pytest_status != 0 || c_coverage_status != 0 || c_html_status != 0 || summary_status != 0 )); then
    exit 1
fi

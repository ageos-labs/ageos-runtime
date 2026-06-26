#!/usr/bin/env bash
set -euo pipefail

COVERAGE_DIR="${1:-.ci-artifacts/coverage}"
BADGE_PATH="${2:-.github/badges/coverage.svg}"

python3 - "$COVERAGE_DIR" "$BADGE_PATH" <<'PY'
import sys
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

coverage_dir = Path(sys.argv[1])
badge_path = Path(sys.argv[2])


def line_totals(node: ET.Element) -> tuple[int, int]:
    lines = node.findall(".//line")
    valid = len(lines)
    covered = sum(1 for line in lines if int(line.attrib.get("hits", "0")) > 0)
    return valid, covered


def coverage_totals(xml_path: Path, *, production_c: bool = False) -> tuple[int, int]:
    if not xml_path.exists():
        raise SystemExit(f"missing coverage report: {xml_path}")

    root = ET.parse(xml_path).getroot()
    total_valid = 0
    total_covered = 0
    seen = set()

    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename or filename in seen:
            continue

        if production_c:
            basename = Path(filename).name
            if not basename.endswith(".c") or basename.endswith("_test.c"):
                continue

        valid, covered = line_totals(class_node)
        total_valid += valid
        total_covered += covered
        seen.add(filename)

    if total_valid == 0:
        raise SystemExit(f"coverage report contains no measured lines: {xml_path}")

    return total_covered, total_valid


def badge_color(coverage: float) -> str:
    if coverage >= 85:
        return "#4c1"
    if coverage >= 70:
        return "#dfb317"
    if coverage >= 50:
        return "#fe7d37"
    return "#e05d44"


c_covered, c_valid = coverage_totals(coverage_dir / "c" / "coverage.xml", production_c=True)
py_covered, py_valid = coverage_totals(coverage_dir / "python" / "coverage.xml")

covered = c_covered + py_covered
valid = c_valid + py_valid
coverage = covered / valid * 100

label = "coverage"
message = f"{coverage:.1f}%"
color = badge_color(coverage)

label_width = max(62, len(label) * 7 + 10)
message_width = max(50, len(message) * 7 + 10)
width = label_width + message_width

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" aria-label="{escape(label)}: {escape(message)}">
  <title>{escape(label)}: {escape(message)}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{width}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{message_width}" height="20" fill="{color}"/>
    <rect width="{width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{label_width / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{escape(label)}</text>
    <text x="{label_width / 2:.1f}" y="14">{escape(label)}</text>
    <text x="{label_width + message_width / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{escape(message)}</text>
    <text x="{label_width + message_width / 2:.1f}" y="14">{escape(message)}</text>
  </g>
</svg>
"""

badge_path.parent.mkdir(parents=True, exist_ok=True)
badge_path.write_text(svg, encoding="utf-8")

print(f"coverage={coverage:.2f}% covered={covered} valid={valid}")
print(f"wrote {badge_path}")
PY

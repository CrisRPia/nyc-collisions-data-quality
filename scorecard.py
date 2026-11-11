#!/usr/bin/env python3
"""Compare the unchanged source and cleaned snapshot: uv run scorecard.py.

Rules use the same purpose and denominators in both states. Unverified accuracy
and publication latency remain unmeasured; no composite quality score is made.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
import html
import json
from pathlib import Path
import re
from pydantic import ValidationError

from analysis.analyze_quality import (
    numeric, populated, ratio,
    spatial_assessment, write_csv,
)
from analysis.data_types import (
    LOCATION_COORDINATES_ADAPTER, ASSESSMENT_ADAPTER, BOUNDARIES_ADAPTER, CLEAN_ROWS_ADAPTER, METADATA_ADAPTER,
    RAW_ROWS_ADAPTER, BoroughBoundaries, Comparison, QualityMetric, ScorecardSummary,
)
from cleanup import (
    check_location_is_redundant, duplicate_groups, file_hash, nullable_text,
    preferred_vehicle_spellings, verify_source_snapshots,
)

from analysis.domain import BOROUGHS, COUNT_FIELDS, VEHICLE_FIELDS, geographic_domain, usable_coordinates


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data/raw/nyc_collisions_2025.json"
CLEAN = ROOT / "data/cleaned/nyc_collisions_2025.json"
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"

type Row = Mapping[str, object]


def measure(
    rows: Sequence[Row], templates: Sequence[QualityMetric], fields: Sequence[str],
    spellings: Mapping[str, str], geometry: BoroughBoundaries, *, cleaned: bool,
) -> list[QualityMetric]:
    """Recompute rules on either representation using a frozen spelling map."""
    metrics = {metric["rule_id"]: metric.copy() for metric in templates}

    def record(rule_id: str, eligible: int | None, exceptions: int | None) -> None:
        metric = metrics[rule_id]
        metric["eligible"], metric["exceptions"] = eligible, exceptions
        if eligible is None or exceptions is None:
            metric["conforming"] = metric["conforming_pct"] = None
        else:
            if not 0 <= exceptions <= eligible:
                raise ValueError(f"Invalid denominator for {rule_id}")
            metric["conforming"] = eligible - exceptions
            metric["conforming_pct"] = ratio(eligible - exceptions, eligible)

    n = len(rows)
    core = ("collision_id", "crash_date", "crash_time", "number_of_persons_injured", "number_of_persons_killed")
    record("C01", n, sum(any(not populated(row.get(field)) for field in core) for row in rows))
    record("C02", n, sum(not populated(row.get("borough")) for row in rows))
    present_pairs = [row for row in rows if all(populated(row.get(field)) for field in ("latitude", "longitude"))]
    record("C03", n, n - len(present_pairs))

    bad_dates, bad_times = 0, 0
    for row in rows:
        try:
            date = datetime.fromisoformat(str(row.get("crash_date", "")))
            bad_dates += date.year != 2025
        except ValueError:
            bad_dates += 1
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(row.get("crash_time", "")))
        bad_times += not match or not (0 <= int(match[1]) <= 23 and 0 <= int(match[2]) <= 59 and 0 <= int(match[3] or 0) <= 59)
    record("V01", n, bad_dates)
    record("V02", n, bad_times)

    bad_counts = 0
    for row in rows:
        for field in COUNT_FIELDS:
            value = numeric(row.get(field))
            bad_counts += value is None or value < 0 or value != value.to_integral_value()
    record("V03", n * len(COUNT_FIELDS), bad_counts)

    invalid_global, zero_pairs = 0, 0
    for row in present_pairs:
        lat, lon = numeric(row.get("latitude")), numeric(row.get("longitude"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            invalid_global += 1
        elif lat == 0 and lon == 0:
            zero_pairs += 1
    record("V04", len(present_pairs), invalid_global)
    record("V05", len(present_pairs), zero_pairs)
    domain = geographic_domain(geometry)
    domain_eligible = [row for row in present_pairs
                       if (lat := numeric(row.get("latitude"))) is not None
                       and (lon := numeric(row.get("longitude"))) is not None
                       and -90 <= lat <= 90 and -180 <= lon <= 180]
    record("V08", len(domain_eligible), sum(not usable_coordinates(row, domain) for row in domain_eligible))

    record("S01", n, sum(populated(row.get("latitude")) != populated(row.get("longitude")) for row in rows))
    boroughs = [row.get("borough") for row in rows if populated(row.get("borough"))]
    record("V06", len(boroughs), sum(not isinstance(value, str) or value not in BOROUGHS for value in boroughs))
    zips = [row.get("zip_code") for row in rows if populated(row.get("zip_code"))]
    record("V07", len(zips), sum(not isinstance(value, str) or re.fullmatch(r"\d{5}", value) is None for value in zips))

    if cleaned and not any(isinstance(row.get("location"), dict) for row in rows):
        record("S02", None, None)
        metrics["S02"]["interpretation"] = "not_applicable"
        metrics["S02"]["note"] = "The redundant location column was removed after agreement checks; no after-state comparison pair exists. This is schema simplification."
    else:
        comparable, disagreeing = 0, 0
        for row in rows:
            location = row.get("location")
            if isinstance(location, dict):
                # Validate this nested boundary rather than asserting its type.
                try:
                    values = LOCATION_COORDINATES_ADAPTER.validate_python(location, strict=True)
                except ValidationError:
                    continue
                if all(numeric(row.get(field)) is not None and numeric(values.get(field)) is not None for field in ("latitude", "longitude")):
                    comparable += 1
                    disagreeing += any(numeric(row.get(field)) != numeric(values.get(field)) for field in ("latitude", "longitude"))
        record("S02", comparable, disagreeing)

    vehicle_cells, variants = 0, 0
    for row in rows:
        for field in VEHICLE_FIELDS:
            value = nullable_text(row.get(field))
            if value:
                vehicle_cells += 1
                variants += value != spellings.get(value.casefold())
    record("S03", vehicle_cells, variants)
    metrics["S03"]["note"] = "The modal spelling map is frozen from the raw snapshot and applied to both states. Abbreviations and different words remain separate."

    for outcome in ("injured", "killed"):
        comparable, subset_bad, unequal = 0, 0, 0
        for row in rows:
            total = numeric(row.get(f"number_of_persons_{outcome}"))
            parts = [numeric(row.get(f"number_of_{role}_{outcome}")) for role in ("pedestrians", "cyclist", "motorist")]
            known = [part for part in parts if part is not None]
            if total is not None and len(known) == len(parts):
                comparable += 1
                subset_bad += any(part > total for part in known)
                unequal += sum(known) != total
        record(f"S04_{outcome}", comparable, subset_bad)
        record(f"R01_{outcome}", comparable, unequal)
        metrics[f"R01_{outcome}"]["note"] = "Review signal only: the three roles are not established as an exhaustive partition. Totals are preserved."

    ids = Counter(nullable_text(row.get("collision_id")) for row in rows if populated(row.get("collision_id")))
    record("U01", sum(ids.values()), sum(count - 1 for count in ids.values()))
    duplicates = duplicate_groups(rows, fields)
    record("U02", n, sum(len(indices) - 1 for indices in duplicates.values()))
    metrics["U02"]["rule"] = "Common source fields excluding ID and redundant location have no exact duplicate excess"
    metrics["U02"]["note"] = "Same common-field set in both states; generated status/flags are excluded. Excess matches are candidates, not confirmed duplicate events. No deletion."

    # Derived boroughs cannot validate themselves. Compare only supplied labels.
    spatial_rows = [dict(row) for row in rows]
    if cleaned:
        for row in spatial_rows:
            if row.get("borough_origin") != "source":
                row["borough"] = ""
    spatial, _, disagreements, _, outside = spatial_assessment(spatial_rows, geometry)
    record("S05", spatial["eligible_known_borough_records"], len(disagreements))
    metrics["S05"]["note"] = "Only source-supplied boroughs are eligible in both states. Derived boroughs are excluded to avoid self-validation; GPS accuracy remains unverified."
    record("R02", len(present_pairs) - invalid_global - zero_pairs, len(outside))

    # Fixed whole-snapshot denominator makes lost mapping coverage visible.
    usable = sum(usable_coordinates(row, domain) for row in rows)
    metrics["C05"] = {
        "rule_id": "C05", "dimension": "Completeness",
        "rule": "Both coordinates are present and within the NYC domain including its margin",
        "unit": "records", "eligible": n,
        "conforming": usable,
        "exceptions": n - usable,
        "conforming_pct": ratio(usable, n),
        "interpretation": "usable_coordinate_coverage",
        "note": "All records are eligible. Coordinates outside the reference envelope plus the configured margin remain in the dataset and generate domain alerts.",
        "dmbok2_reference": metrics["C03"]["dmbok2_reference"],
    }
    return list(metrics.values())


def compare(before: Sequence[QualityMetric], after: Sequence[QualityMetric]) -> list[Comparison]:
    comparisons: list[Comparison] = []
    for first, last in zip(before, after, strict=True):
        if first["rule_id"] != last["rule_id"]:
            raise ValueError("Before/after rules do not match")
        left, right = first["conforming_pct"], last["conforming_pct"]
        comparisons.append({
            "rule_id": first["rule_id"], "dimension": first["dimension"],
            "rule": last["rule"], "unit": first["unit"],
            "before_eligible": first["eligible"], "before_exceptions": first["exceptions"],
            "before_conforming_pct": left,
            "after_eligible": last["eligible"], "after_exceptions": last["exceptions"],
            "after_conforming_pct": right,
            "change_percentage_points": round(right - left, 6) if left is not None and right is not None else None,
            "interpretation": last["interpretation"], "note": last["note"],
            "dmbok2_reference": last["dmbok2_reference"],
        })
    return comparisons


def render_report(comparisons: Sequence[Comparison], record_count: int) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value))

    def pct(value: float | None) -> str:
        return "Not measured / N/A" if value is None else f"{value:.3f}%"

    def count(value: int | None) -> str:
        return "—" if value is None else f"{value:,}"

    by_id = {item["rule_id"]: item for item in comparisons}
    bars: list[str] = []
    for rule_id, label in (("C02", "Borough populated"), ("S03", "Vehicle spelling conformance"), ("V05", "Populated coordinates are nonzero"), ("C05", "Usable coordinate coverage")):
        item = by_id[rule_id]
        tracks: list[str] = []
        for state, color in (("before", "1"), ("after", "2")):
            value = item["before_conforming_pct"] if state == "before" else item["after_conforming_pct"]
            tracks.append(f'<div class="barrow"><span>{state.title()}</span><div class="track"><div style="width:{value or 0}%;background:var(--chart-{color}, {"GrayText" if color == "1" else "LinkText"})"></div></div><strong>{pct(value)}</strong></div>')
        bars.append(f'<section class="metric"><h3>{label}</h3>{"".join(tracks)}</section>')

    sections: list[str] = []
    for dimension in ("Completeness", "Consistency", "Validity", "Uniqueness", "Accuracy", "Timeliness"):
        rows: list[str] = []
        for item in comparisons:
            if item["dimension"] != dimension:
                continue
            delta = item["change_percentage_points"]
            change = "—" if delta is None else f"{delta:+.3f} pp"
            rows.append(f'<tr><td><strong>{esc(item["rule_id"])}</strong> {esc(item["rule"])}<small>{esc(item["note"])}</small><small>{esc(item["interpretation"])} · {esc(item["unit"])}</small></td><td>{count(item["before_exceptions"])} / {count(item["before_eligible"])}<small>{pct(item["before_conforming_pct"])}</small></td><td>{count(item["after_exceptions"])} / {count(item["after_eligible"])}<small>{pct(item["after_conforming_pct"])}</small></td><td>{change}</td></tr>')
        sections.append(f'<details><summary>{dimension}</summary><div class="scroll"><table><thead><tr><th>Rule and interpretation</th><th>Before: exceptions / eligible<br>Conformance</th><th>After: exceptions / eligible<br>Conformance</th><th>Change</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Data Quality Scorecard — before and after</title>
<style>html,body{{background:transparent;color:var(--foreground,CanvasText);font:15px/1.5 var(--font-sans,system-ui,sans-serif);margin:0}}main{{max-width:1100px;margin:24px auto 48px}}h1{{font-size:26px;line-height:1.2}}h2{{font-size:20px}}h3{{font-size:15px;margin:0 0 8px}}p{{max-width:1000px}}.metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 28px;margin:24px 0}}.barrow{{display:grid;grid-template-columns:48px 1fr 84px;align-items:center;gap:8px;margin:5px 0}}.track{{height:12px;background:var(--muted,ButtonFace)}}.track div{{height:12px}}strong{{font-weight:600}}.barrow strong{{text-align:right;font-variant-numeric:tabular-nums;font-size:13px}}details{{border-bottom:1px solid var(--border,GrayText);padding:12px 0}}summary{{font-weight:600;cursor:pointer}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{text-align:left;vertical-align:top;padding:12px 8px;border-bottom:1px solid var(--border,GrayText)}}td:first-child{{min-width:230px}}td:not(:first-child){{min-width:100px;font-variant-numeric:tabular-nums}}small{{display:block;color:var(--muted-foreground,GrayText);margin-top:5px}}a{{color:var(--chart-2,LinkText)}}code{{font-size:13px}}@media(max-width:600px){{.metrics{{grid-template-columns:1fr}}h1{{font-size:23px}}}}@media print{{details::details-content{{display:block}}main{{margin:0}}}}</style></head><body><main>
<h1>Data Quality Scorecard: before and after</h1>
<p>{record_count:,} crash records retained. Same annual 2025 analytical purpose; no composite quality score. Bars show conformance on a 0–100% scale.</p>
<div class="metrics">{"".join(bars)}</div>
<p><strong>Interpret the changes carefully.</strong> Missing boroughs are derived conditionally on correct GPS. Supplied coordinates, including values outside the NYC domain, are retained. Geographic-domain alerts use the official reference envelope plus an approximate 5 km margin. Usable coordinate coverage is reported separately from coordinate presence.</p>
<p>Potential duplicate events and person/role-total differences remain review items. Accuracy and publication latency are unmeasured. Derived boroughs are excluded from the borough-reference agreement check.</p>
<h2>Rules across all six dimensions</h2><p>Expand a dimension to see counts, changing denominators, conformance and limitations. N/A is not a passing score. Changes are percentage points, not percent changes.</p>
{"".join(sections)}
<h2>Proposed continuous monitoring</h2><ol><li>Archive each new snapshot, acquisition time, hashes and reference versions.</li><li>Validate the source and rerun assessment, cleanup and scorecard before publishing.</li><li>Stop on unreadable inputs or failed provenance checks. Export domain violations as record-level alerts while retaining the records and supplied values.</li><li>Track rates by load and month; review alerts weekly and rules monthly with the proposed data steward.</li></ol>
<p>This is a proposal; no scheduled service or organizational roles have been deployed. Thresholds require data-consumer approval.</p>
<p><a href="monitoring_policy.txt">Full monitoring policy</a> · <a href="../results/quality_scorecard_before_after.csv">CSV scorecard</a> · <a href="../results/quality_scorecard_before_after.json">JSON scorecard and provenance</a></p>
<p><small>DMBOK2 Chapter 13: rules and consumer needs (printed pp. 467–468, 478–479), measurement and denominators (pp. 482–483), remediation and monitoring (pp. 484–486). Reproduce with <code>uv run --locked scorecard.py</code>.</small></p>
</main></body></html>'''


def main(root: Path = ROOT) -> None:
    raw_path = root / "data/raw/nyc_collisions_2025.json"
    clean_path = root / "data/cleaned/nyc_collisions_2025.json"
    results_dir = root / "results"
    reports_dir = root / "reports"
    verify_source_snapshots(root)
    raw_hash, cleaned_hash = file_hash(raw_path), file_hash(clean_path)
    raw = RAW_ROWS_ADAPTER.validate_json(raw_path.read_bytes(), strict=True)
    cleaned = CLEAN_ROWS_ADAPTER.validate_json(clean_path.read_bytes(), strict=True)
    assessment = ASSESSMENT_ADAPTER.validate_json((results_dir / "quality_metrics.json").read_bytes(), strict=True)
    if assessment["raw_sha256"] != raw_hash:
        raise ValueError("The baseline belongs to another raw snapshot; rerun the assessment")
    if [row.get("collision_id") for row in raw] != [row.get("collision_id") for row in cleaned]:
        raise ValueError("Before/after record identities or order differ")
    for original, final in zip(raw, cleaned, strict=True):
        if "location" not in final:
            check_location_is_redundant(original)
        if populated(original.get("borough")) and final.get("borough") != original.get("borough"):
            raise ValueError("A supplied borough was overwritten")
    metadata = METADATA_ADAPTER.validate_json((root / "data/reference/nyc_collisions_metadata.json").read_bytes(), strict=True)
    observed = {field for row in raw for field in row}
    fields = [column["fieldName"] for column in metadata["columns"] if column["fieldName"] in observed and column["fieldName"] != "location"]
    geometry = BOUNDARIES_ADAPTER.validate_json((root / "data/reference/nyc_borough_boundaries.geojson").read_bytes(), strict=True)
    spellings = preferred_vehicle_spellings(raw)
    before = measure(raw, assessment["metrics"], fields, spellings, geometry, cleaned=False)
    after = measure(cleaned, assessment["metrics"], fields, spellings, geometry, cleaned=True)
    for actual, baseline in zip(before[:len(assessment["metrics"])], assessment["metrics"], strict=True):
        if (actual["eligible"], actual["exceptions"], actual["conforming_pct"]) != (baseline["eligible"], baseline["exceptions"], baseline["conforming_pct"]):
            raise ValueError(f"Baseline disagreement for {actual['rule_id']}")
    comparisons = compare(before, after)
    if file_hash(raw_path) != raw_hash or file_hash(clean_path) != cleaned_hash:
        raise ValueError("An input changed while computing the scorecard")
    payload: ScorecardSummary = {
        "raw_sha256": raw_hash, "cleaned_sha256": cleaned_hash,
        "reference_sha256": file_hash(root / "data/reference/nyc_borough_boundaries.geojson"),
        "records_before": len(raw), "records_after": len(cleaned),
        "before": before, "after": after, "comparison": comparisons,
        "method": "Same rules and explicit denominators; frozen source spelling map; source-supplied boroughs only for reference agreement; no composite score.",
    }
    _ = (results_dir / "quality_scorecard_before_after.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(results_dir / "quality_scorecard_before_after.csv", comparisons)
    _ = (reports_dir / "quality_scorecard_before_after.html").write_text(render_report(comparisons, len(raw)), encoding="utf-8")
    print(f"Compared {len(comparisons)} rules; retained all {len(raw):,} records.")


if __name__ == "__main__":
    main()

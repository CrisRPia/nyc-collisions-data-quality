#!/usr/bin/env python3
"""Assess the unchanged NYC snapshot against documented DMBOK2-based rules.

Run: uv run analysis/analyze_quality.py
Uses the local reference snapshots, NumPy and matplotlib; makes no network calls.
Outputs an assessment, not a cleaned dataset or a composite quality score.
"""
from __future__ import annotations

import collections
from collections.abc import Iterable, Mapping, Sequence
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from typing import Annotated

import numpy as np
from matplotlib.path import Path as PolygonPath
from numpy.typing import NDArray
from pydantic import AfterValidator, ConfigDict, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
# Make the shared schemas available when uv executes this file directly.
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from analysis.data_types import (
    BOUNDARIES_ADAPTER, HUMAN_ADDRESS_ADAPTER, MANIFEST_ADAPTER, METADATA_ADAPTER,
    RAW_ROWS_ADAPTER, AssessmentSummary, BoroughBoundaries, FieldProfile,
    QualityMetric, RawRow, SourceFreshness, SpatialStats,
)

RAW = ROOT / "data/raw/nyc_collisions_2025.json"
REFERENCE = ROOT / "data/reference/nyc_borough_boundaries.geojson"
METADATA = ROOT / "data/reference/nyc_collisions_metadata.json"
MANIFEST = ROOT / "data/reference/assessment_sources.json"
from analysis.domain import (
    BOROUGHS, COUNT_FIELDS, VEHICLE_FIELDS, coordinate_value, geographic_domain,
    usable_coordinates,
)
BOOK_REFS = {
    "purpose": "Chapter 13, printed pp. 457, 476-479; PDF pp. 461, 480-483",
    "dimensions": "Chapter 13, printed pp. 460-462, Table 29; PDF pp. 464-466",
    "rules": "Chapter 13, printed pp. 467-468, 478; PDF pp. 471-472, 482",
    "metrics": "Chapter 13, printed pp. 482-483, Table 30; PDF pp. 486-487",
    "remediation": "Chapter 13, printed pp. 484-486; PDF pp. 488-490",
}


def check_boolean_dtype(mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """Check dtype as well as array membership at the matplotlib boundary."""
    if mask.dtype != np.dtype(np.bool_):
        raise ValueError("Expected a boolean coordinate mask")
    return mask


BOOLEAN_MASK_ADAPTER: TypeAdapter[NDArray[np.bool_]] = TypeAdapter(
    Annotated[NDArray[np.bool_], AfterValidator(check_boolean_dtype)],
    config=ConfigDict(arbitrary_types_allowed=True),
)


def populated(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def numeric(value: object) -> Decimal | None:
    if not populated(value):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ratio(n: int, d: int) -> float | None:
    return round(100 * n / d, 6) if d else None


def write_csv(
    path: Path, records: Iterable[Mapping[str, object]], fields: Sequence[str] | None = None,
) -> None:
    records = list(records)
    fields = fields or (list(records[0]) if records else [])
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def spatial_assessment(
    rows: Sequence[Mapping[str, object]], geometry: BoroughBoundaries,
) -> tuple[SpatialStats, list[list[str]], list[int], list[int], list[int]]:
    """Reference consistency / recovery candidates, conditional on source GPS.

    The 0.0003-degree outline band is a conservative approximate exclusion,
    not a metric-distance guarantee. It is retained from the initial pilot.
    A production recovery process should use a projected CRS / metric buffer.
    """
    points = np.array([
        [float(numeric(r.get("longitude")) or 0), float(numeric(r.get("latitude")) or 0)]
        for r in rows
    ])
    valid = np.isfinite(points).all(axis=1)
    valid &= (np.abs(points[:, 0]) <= 180) & (np.abs(points[:, 1]) <= 90)
    valid &= np.array([
        numeric(r.get("latitude")) is not None and numeric(r.get("longitude")) is not None
        for r in rows
    ])
    valid &= ~np.all(BOOLEAN_MASK_ADAPTER.validate_python(points == 0), axis=1)
    names = [str(r.get("borough") or "").strip().upper() for r in rows]
    matches: list[list[str]] = [[] for _ in rows]
    near = np.zeros(len(rows), dtype=bool)
    envelope_parts: list[NDArray[np.float64]] = []
    margin = 0.0003
    for feature in geometry["features"]:
        name = feature["properties"]["boroname"].upper()
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        contained = np.zeros(len(rows), dtype=bool)
        for polygon in polygons:
            ring = np.asarray(polygon[0], dtype=np.float64)
            envelope_parts.append(ring)
            lo, hi = ring.min(axis=0) - margin, ring.max(axis=0) + margin
            idx = np.flatnonzero(valid & np.all((points >= lo) & (points <= hi), axis=1))
            if not len(idx):
                continue
            path = PolygonPath(ring)
            inside = BOOLEAN_MASK_ADAPTER.validate_python(path.contains_points(points[idx]))
            close = BOOLEAN_MASK_ADAPTER.validate_python(path.contains_points(points[idx], radius=margin) != path.contains_points(points[idx], radius=-margin))
            for hole in polygon[1:]:
                path = PolygonPath(np.asarray(hole))
                inside &= ~BOOLEAN_MASK_ADAPTER.validate_python(path.contains_points(points[idx]))
                close |= BOOLEAN_MASK_ADAPTER.validate_python(path.contains_points(points[idx], radius=margin) != path.contains_points(points[idx], radius=-margin))
            contained[idx] |= inside
            near[idx] |= close
        for i in np.flatnonzero(contained):
            matches[i].append(name)
    safe = [i for i, m in enumerate(matches) if len(m) == 1 and not near[i]]
    known = [i for i in safe if names[i] in BOROUGHS]
    disagreements = [i for i in known if matches[i][0] != names[i]]
    recovery = [i for i in safe if not names[i]]
    rings = np.concatenate(envelope_parts)
    lo, hi = rings.min(axis=0), rings.max(axis=0)
    outside_envelope = [int(i) for i in np.flatnonzero(valid & ~np.all((points >= lo) & (points <= hi), axis=1))]
    stats: SpatialStats = {
        "reference_features": len(geometry["features"]),
        "boundary_exclusion_degrees": margin,
        "eligible_known_borough_records": len(known),
        "agreeing_records": len(known) - len(disagreements),
        "disagreeing_records": len(disagreements),
        "agreement_pct": ratio(len(known) - len(disagreements), len(known)),
        "missing_borough_recovery_candidates": len(recovery),
        "missing_borough_remaining_if_candidates_accepted": sum(not n for n in names) - len(recovery),
        "outside_reference_envelope_flags": len(outside_envelope),
        "excluded_or_unmatched_records": len(rows) - len(safe),
        "limits": "Conditional on correct coordinates; current land-only polygons; approximate boundary band. Agreement is not ground-truth crash-location accuracy. No recovery was applied.",
    }
    return stats, matches, disagreements, recovery, outside_envelope


def source_text(row: RawRow, field: str) -> str:
    """Read a scalar source column; location is the only nested column."""
    value = row.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Expected text in source field {field!r}")
    return value


def assess(root: Path = ROOT) -> None:
    raw_path = root / "data/raw/nyc_collisions_2025.json"
    metadata_path = root / "data/reference/nyc_collisions_metadata.json"
    reference_path = root / "data/reference/nyc_borough_boundaries.geojson"
    manifest_path = root / "data/reference/assessment_sources.json"
    raw_bytes = raw_path.read_bytes()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    # Validate representation without performing any cleaning or coercion.
    rows = RAW_ROWS_ADAPTER.validate_json(raw_bytes, strict=True)
    metadata = METADATA_ADAPTER.validate_json(metadata_path.read_bytes(), strict=True)
    geometry = BOUNDARIES_ADAPTER.validate_json(reference_path.read_bytes(), strict=True)
    manifest = MANIFEST_ADAPTER.validate_json(manifest_path.read_bytes(), strict=True)
    for source in manifest:
        assert hashlib.sha256((root / source["file"]).read_bytes()).hexdigest() == source["sha256"], f"Source snapshot hash mismatch: {source['file']}"
    n = len(rows)
    observed = set(k for r in rows for k in r)
    fields = [c["fieldName"] for c in metadata["columns"] if c.get("fieldName") in observed]
    assert set(fields) == observed
    assert len(geometry["features"]) == 5
    metrics: list[QualityMetric] = []
    issues: list[dict[str, object]] = []
    profiles: list[FieldProfile] = []

    def metric(
        rule_id: str, dimension: str, rule: str, eligible: int | None, exceptions: int | None,
        unit: str = "records", interpretation: str = "conformance", note: str = "", book: str = "dimensions",
    ) -> None:
        conforming, conforming_pct = None, None
        if eligible is not None:
            assert exceptions is not None, rule_id
            assert 0 <= exceptions <= eligible, rule_id
            conforming = eligible - exceptions
            conforming_pct = ratio(conforming, eligible)
        metrics.append({
            "rule_id": rule_id, "dimension": dimension, "rule": rule,
            "unit": unit, "eligible": eligible, "conforming": conforming,
            "exceptions": exceptions,
            "conforming_pct": conforming_pct,
            "interpretation": interpretation, "note": note, "dmbok2_reference": BOOK_REFS[book],
        })

    def issue(
        rule_id: str, i: int, field: str, finding: str,
        classification: str = "analytical_gap", value: object = "",
    ) -> None:
        issues.append({"rule_id": rule_id, "collision_id": rows[i].get("collision_id", ""),
                       "field": field, "classification": classification, "observed_value": value, "finding": finding})

    core = {"collision_id", "crash_date", "crash_time", "number_of_persons_injured", "number_of_persons_killed"}
    for field in fields:
        vals = [r.get(field) for r in rows]
        present = [v for v in vals if populated(v)]
        profiles.append({
            "field": field, "published_type": next(c["dataTypeName"] for c in metadata["columns"] if c.get("fieldName") == field),
            "role_for_this_analysis": "core" if field in core else "required_for_borough_aggregation" if field == "borough" else "conditional_or_supporting",
            "records": n, "populated": len(present), "missing": n - len(present),
            "populated_pct": ratio(len(present), n),
            "distinct_present": len({canonical(v) for v in present}),
            "outer_whitespace_cells": sum(isinstance(v, str) and v != v.strip() for v in present),
            "json_types": ", ".join(f"{k}:{v}" for k, v in sorted(collections.Counter(type(v).__name__ for v in present).items())),
        })

    core_missing = [i for i, r in enumerate(rows) if any(not populated(r.get(f)) for f in core)]
    metric("C01", "Completeness", "Core ID, date, time and person totals are present", n, len(core_missing), note="Analytical requirements, not an assertion that the source requires every published field.")
    borough_missing = [i for i, r in enumerate(rows) if not populated(r.get("borough"))]
    metric("C02", "Completeness", "Borough is present for borough-level aggregation", n, len(borough_missing), interpretation="analytical_gap", note="Excluding unknown boroughs can bias geographic aggregates. This does not prove a source entry error.")
    for i in borough_missing:
        issue("C02", i, "borough", "Missing borough for the selected geographic analysis")
    pair_missing = [i for i, r in enumerate(rows) if not all(populated(r.get(f)) for f in ["latitude", "longitude"])]
    pair_missing_set = set(pair_missing)
    metric("C03", "Completeness", "Both coordinate components are populated", n, len(pair_missing), interpretation="coverage", note="Coordinates are needed for map-based analysis/recovery, but borough aggregates can use a supplied borough without coordinates.")
    metric("C04", "Completeness", "Population covers every real-world/reportable crash in scope", None, None, interpretation="not_verified", note="No reconciled source control total or independent event register is available. Date coverage and API row counts do not prove real-world completeness.")

    dates: list[dt.date] = []
    bad_dates: list[int] = []
    bad_times: list[int] = []
    for i, r in enumerate(rows):
        try:
            value = dt.datetime.fromisoformat(source_text(r, "crash_date"))
            if value.year != 2025:
                raise ValueError("outside scope")
            dates.append(value.date())
        except ValueError:
            bad_dates.append(i)
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", source_text(r, "crash_time"))
        if not match or not (0 <= int(match[1]) <= 23 and 0 <= int(match[2]) <= 59 and 0 <= int(match[3] or 0) <= 59):
            bad_times.append(i)
    metric("V01", "Validity", "Crash dates parse and lie in the selected 2025 period", n, len(bad_dates))
    metric("V02", "Validity", "Crash time parses as a local clock time", n, len(bad_times), note="One-digit hours are allowed. No timezone offset or UTC instant is inferred from the source's local date/time.")
    invalid_count_cells: list[tuple[int, str]] = []
    for i, r in enumerate(rows):
        for field in COUNT_FIELDS:
            v = numeric(r.get(field))
            if v is None or v < 0 or v != v.to_integral_value():
                invalid_count_cells.append((i, field))
                issue("V03", i, field, "Count must be present, finite, nonnegative and integral", "domain_violation", r.get(field, ""))
    metric("V03", "Validity", "Eight injury/fatality counts are nonnegative integers", n * len(COUNT_FIELDS), len(invalid_count_cells), unit="cells", note="Numeric strings are accepted after parsing. Socrata serializes Number values as strings; serialization itself is not a failure.")
    present_pairs = [i for i in range(n) if i not in pair_missing_set]
    invalid_global: list[int] = []
    zero_pairs: list[int] = []
    partial_pairs: list[int] = []
    for i, r in enumerate(rows):
        lat, lon = numeric(r.get("latitude")), numeric(r.get("longitude"))
        if populated(r.get("latitude")) != populated(r.get("longitude")):
            partial_pairs.append(i)
        if i in pair_missing_set:
            continue
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            invalid_global.append(i)
        elif lat == 0 and lon == 0:
            zero_pairs.append(i)
            issue("V05", i, "latitude,longitude", "(0,0) is not a usable NYC crash location; retain event and flag location as unknown", "local_geography_violation", "0,0")
    metric("V04", "Validity", "Populated coordinates are finite and within global ranges", len(present_pairs), len(invalid_global), note="Global plausibility does not establish NYC membership or positional accuracy.")
    metric("V05", "Validity", "Populated NYC coordinates do not use (0,0)", len(present_pairs), len(zero_pairs), note="A valid global coordinate can still fail the selected local-geography rule. Missingness must be reported separately.")
    metric("S01", "Consistency", "Latitude and longitude are populated together", n, len(partial_pairs))
    nonblank_boroughs = [i for i, r in enumerate(rows) if populated(r.get("borough"))]
    invalid_boroughs = [i for i in nonblank_boroughs if rows[i]["borough"] not in BOROUGHS]
    metric("V06", "Validity", "Populated borough belongs to the five-name domain", len(nonblank_boroughs), len(invalid_boroughs))
    zips = [i for i, r in enumerate(rows) if populated(r.get("zip_code"))]
    invalid_zips = [i for i in zips if not re.fullmatch(r"\d{5}", source_text(rows[i], "zip_code"))]
    metric("V07", "Validity", "Populated ZIP is a five-digit text code", len(zips), len(invalid_zips), note="Format check only; actual USPS assignment and association with a crash location are not verified. Missing ZIP is descriptive, not a required-field failure for this use.")
    domain = geographic_domain(geometry)
    outside_domain = [i for i, row in enumerate(rows)
                      if coordinate_value(row.get("latitude")) is not None
                      and coordinate_value(row.get("longitude")) is not None
                      and -90 <= float(coordinate_value(row.get("latitude")) or 0) <= 90
                      and -180 <= float(coordinate_value(row.get("longitude")) or 0) <= 180
                      and not usable_coordinates(row, domain)]
    domain_eligible = len(present_pairs) - len(invalid_global)
    metric("V08", "Validity", f"Coordinates lie within the NYC envelope plus a {domain.margin_km:g} km margin",
           domain_eligible, len(outside_domain), note="Official reference bounding box expanded by an approximate, configurable 5 km margin. Values outside the domain are preserved and alerted.")
    for i in outside_domain:
        issue("V08", i, "latitude,longitude", "Coordinates outside NYC plus the configured margin",
              "domain_violation", f"{rows[i].get('latitude')},{rows[i].get('longitude')}")


    comparable: list[int] = []
    pair_disagreements: list[int] = []
    nonempty_human: list[int] = []
    for i, r in enumerate(rows):
        location = r.get("location")
        if not isinstance(location, dict):
            continue
        if all(numeric(r.get(k)) is not None and numeric(location.get(k)) is not None for k in ["latitude", "longitude"]):
            comparable.append(i)
            if any(numeric(r[k]) != numeric(location[k]) for k in ["latitude", "longitude"]):
                pair_disagreements.append(i)
        address_json = location.get("human_address")
        if address_json:
            try:
                human = HUMAN_ADDRESS_ADAPTER.validate_json(address_json, strict=True)
            except ValidationError:
                # Malformed or unexpected address data also prevents treating it as empty.
                nonempty_human.append(i)
            else:
                if any(populated(v) for v in human.values()):
                    nonempty_human.append(i)
    metric("S02", "Consistency", "Scalar coordinates agree with the location object", len(comparable), len(pair_disagreements), note="Redundant representations currently agree. Dropping location is schema simplification, not repair of inconsistent coordinates.")

    groups: collections.defaultdict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for row in rows:
        for field in VEHICLE_FIELDS:
            v = source_text(row, field)
            if populated(v):
                groups[v.strip().casefold()][v] += 1
    preferred = {key: sorted(counts, key=lambda v: (-counts[v], v))[0] for key, counts in groups.items()}
    variant_cells = 0
    variant_rows: set[int] = set()
    for i, row in enumerate(rows):
        for field in VEHICLE_FIELDS:
            v = source_text(row, field)
            if populated(v) and v != preferred[v.strip().casefold()]:
                variant_cells += 1
                variant_rows.add(i)
                issue("S03", i, field, "Nonmodal spelling within an exact trim/casefold group; proposed representation standard", "standardization_candidate", v)
    vehicle_cells = sum(sum(c.values()) for c in groups.values())
    metric("S03", "Consistency", "Vehicle-type spellings follow the modal spelling within each trim/casefold group", vehicle_cells, variant_cells, unit="cells", interpretation="proposed_standardization", note="Explicit proposed representation rule, not an official taxonomy or proof of wrong vehicle classification. Ties use lexical order. Abbreviations and different words are not merged.")
    for suffix, total in [("injured", "number_of_persons_injured"), ("killed", "number_of_persons_killed")]:
        children = [f"number_of_{role}_{suffix}" for role in ["pedestrians", "cyclist", "motorist"]]
        comparable_counts = [i for i, r in enumerate(rows) if all(numeric(r.get(f)) is not None for f in [total, *children])]
        subset_bad: list[int] = []
        lower: list[int] = []
        higher: list[int] = []
        for i in comparable_counts:
            all_persons = numeric(rows[i][total])
            parts = [numeric(rows[i][f]) for f in children]
            assert all_persons is not None
            known_parts = [v for v in parts if v is not None]
            assert len(known_parts) == len(parts)
            if any(v > all_persons for v in known_parts):
                subset_bad.append(i)
            if sum(known_parts) < all_persons:
                lower.append(i)
            elif sum(known_parts) > all_persons:
                higher.append(i)
        metric(f"S04_{suffix}", "Consistency", f"No individual {suffix} role subtotal exceeds the all-person total", len(comparable_counts), len(subset_bad), note="Analytical subset relationship; equal sums are not assumed.")
        metric(f"R01_{suffix}", "Consistency", f"All-person {suffix} total equals pedestrian + cyclist + motorist subtotals", len(comparable_counts), len(lower) + len(higher), interpretation="unconfirmed_review_signal", note=f"Totals greater than subtotal sum: {len(lower)}; smaller: {len(higher)}. Source does not establish an exhaustive partition; these are not confirmed errors and must not be automatically corrected.")
        for i in [*lower, *higher]:
            issue(f"R01_{suffix}", i, total, "Person total differs from the three-role sum; exhaustiveness unconfirmed", "review_only", rows[i][total])

    ids = [source_text(r, "collision_id") for r in rows]
    id_counts = collections.Counter(v.strip() for v in ids if populated(v))
    metric("U01", "Uniqueness", "Nonblank collision IDs are unique within the snapshot", sum(id_counts.values()), sum(c - 1 for c in id_counts.values()), note="Key uniqueness, not proof of real-world event uniqueness. IDs remain text.")
    event_groups: collections.defaultdict[str, list[int]] = collections.defaultdict(list)
    for i, row in enumerate(rows):
        fingerprint = hashlib.sha256(canonical({f: row.get(f) for f in fields if f != "collision_id"}).encode()).hexdigest()
        event_groups[fingerprint].append(i)
    fingerprints = {key: len(indices) for key, indices in event_groups.items()}
    duplicate_groups = {key: indices for key, indices in event_groups.items() if len(indices) > 1}
    metric("U02", "Uniqueness", "Published values excluding the ID have no exact duplicate excess", n, sum(c - 1 for c in fingerprints.values()), interpretation="candidate_screen", note="Even exact non-ID matches would need event-level review; no deduplication is performed.")
    for fingerprint, indices in duplicate_groups.items():
        for i in indices:
            issue("U02", i, "all published fields except collision_id", f"All non-ID values match {len(indices)-1} other record(s), group {fingerprint[:16]}; review event identity", "duplicate_candidate")
    metric("A01", "Accuracy", "Reported events, counts and coordinates agree with independently verified reality", None, None, interpretation="not_verified", note="No original police-report review or independent crash-location/count audit was performed. Internal consistency and format conformance do not prove accuracy.")

    spatial, matches, disagreement_ids, recovery_ids, envelope_ids = spatial_assessment(rows, geometry)
    metric("S05", "Consistency", "Supplied borough agrees with official polygon membership for eligible coordinates", spatial["eligible_known_borough_records"], len(disagreement_ids), interpretation="reference_comparison", note=spatial["limits"])
    for i in disagreement_ids:
        issue("S05", i, "borough", f"Reported borough differs from polygon-derived {matches[i][0]}; coordinate accuracy unverified", "review_only", rows[i]["borough"])
    for i in envelope_ids:
        issue("R02", i, "latitude,longitude", "Nonzero coordinate is outside the reference polygon envelope; review location/reference applicability", "review_only")
    metric("R02", "Validity", "Nonzero globally valid coordinates lie inside the reference envelope", len(present_pairs) - len(invalid_global) - len(zero_pairs), len(envelope_ids), interpretation="geographic_review_signal", note="Bounding envelope is a diagnostic, not definitive proof of incorrect location. Water, bridges, reference versions and geocoding require review.")
    metric("T01", "Timeliness", "Crash-to-first-publication latency meets a business SLA", None, None, interpretation="not_measurable", note="No reliable first-publication timestamp or agreed latency SLA. Platform created/updated timestamps may reset during reloads.")
    metric("T02", "Timeliness", "Snapshot remains suitable for a fixed annual 2025 analysis", None, None, interpretation="historical_scope", note="Old event dates are expected for this purpose. Late amendments remain possible; no version-to-version stability claim is made.")

    meta_observation = next(x for x in manifest if x["file"].endswith("nyc_collisions_metadata.json"))
    observed_at = dt.datetime.fromisoformat(meta_observation["observed_at_utc"])
    source_update = dt.datetime.fromtimestamp(metadata["rowsUpdatedAt"], dt.timezone.utc)
    lineage: SourceFreshness = {
        "source_update_utc": source_update.isoformat(),
        "metadata_observed_utc": observed_at.isoformat(),
        "source_age_hours_at_metadata_observation": round((observed_at - source_update).total_seconds() / 3600, 3),
        "advertised_update_frequency": "Daily",
        "interpretation": "Source-level metadata freshness observed once; not first-publication latency or proof of daily SLA adherence.",
    }
    summary: AssessmentSummary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "purpose": "Annual 2025 borough-level road-safety analysis of police-reported crashes, with geographic and vehicle-category summaries.",
        "purpose_status": "Analyst-defined scope; no interviews or NYPD stakeholder approval claimed.",
        "records": n, "published_fields": len(fields), "raw_sha256": raw_hash,
        "date_min": min(dates).isoformat() if dates else "", "date_max": max(dates).isoformat() if dates else "",
        "observed_calendar_days": len(set(dates)), "expected_calendar_days": 365,
        "core_fields": sorted(core), "book_references": BOOK_REFS,
        "book": {"title": "DAMA-DMBOK2", "chapter": 13, "page_offset": "PDF page = printed page + 4", "six_dimensions": "DAMA UK six core dimensions discussed on printed p. 460, selected by the assignment. DMBOK2 also discusses broader frameworks."},
        "spatial": spatial, "source_freshness": lineage,
        "representation": {"numeric_api_strings": "Expected Socrata serialization; validated logically after parsing", "coordinate_copy_pairs": len(comparable), "coordinate_copy_disagreements": len(pair_disagreements), "location_objects_with_nonempty_human_address": len(nonempty_human), "vehicle_variant_cells": variant_cells, "vehicle_variant_records": len(variant_rows), "vehicle_populated_cells": vehicle_cells, "vehicle_multi_spelling_groups": sum(len(c) > 1 for c in groups.values())},
        "duplicate_event_candidates": {"groups": len(duplicate_groups), "records_in_groups": sum(len(v) for v in duplicate_groups.values()), "excess_records": sum(len(v)-1 for v in duplicate_groups.values()), "rule": "Exact equality on all 28 published non-ID fields; missing absent values normalized to null for comparison. No deletion or confirmed event-identity claim."},
        "metrics": metrics,
        "limitations": [
            "No blanket field-completeness score: optional/conditional vehicle slots, addresses, factors and ZIP are profiled without declaring every blank erroneous.",
            "No composite score or universal acceptability threshold. Structural domains are explicit; operational targets require stakeholder agreement.",
            "No original event/coordinate/count accuracy audit, no independently reconciled total event population, no operational latency measurement.",
            "Current land-only reference polygons are compared with 2025 events, with an approximate boundary exclusion. Reference disagreement is a review flag.",
            "Recovery candidates are conditional proposals; no cleaned dataset or claimed achieved after-score is produced.",
            "Issue counts overlap and mix records/cells; do not add them to estimate unique defective records.",
            "No root cause is established solely from profiling; source capture, coding, geocoding and extraction hypotheses need owner review.",
        ],
    }
    results, reports = root / "results", root / "reports"
    results.mkdir(exist_ok=True)
    reports.mkdir(exist_ok=True)
    _ = (results / "quality_metrics.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    write_csv(results / "field_profile.csv", profiles)
    write_csv(results / "quality_scorecard.csv", metrics)
    write_csv(results / "quality_issues.csv", issues)
    write_csv(results / "borough_recovery_candidates.csv", [{
        "collision_id": rows[i]["collision_id"], "proposed_borough": matches[i][0],
        "basis": "Unique official polygon interior, outside approximate boundary band",
        "status": "candidate_not_applied", "assumption": "Reported coordinates identify the true crash location",
    } for i in recovery_ids])
    write_csv(results / "vehicle_spelling_candidates.csv", [{
        "normalized_key": key, "proposed_spelling": preferred[key], "observed_spelling": value,
        "cells": count, "is_proposed_spelling": value == preferred[key],
    } for key, counts in sorted(groups.items()) if len(counts) > 1 for value, count in sorted(counts.items())])
    write_csv(results / "duplicate_event_candidates.csv", [{
        "group_fingerprint": fingerprint, "group_size": len(indices),
        "collision_id": rows[i]["collision_id"], "crash_date": rows[i]["crash_date"],
        "crash_time": rows[i]["crash_time"], "borough": rows[i].get("borough", ""),
        "status": "review_only_not_deleted",
    } for fingerprint, indices in duplicate_groups.items() for i in indices])
    render_report(summary, profiles, reports / "data_quality_assessment.html")
    assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == raw_hash, "Raw dataset changed"
    print(json.dumps({"records": n, "fields": len(fields), "rules": len(metrics), "issue_rows": len(issues), "vehicle_variant_cells": variant_cells, "spatial": spatial, "raw_unchanged": True, "report": str(reports / "data_quality_assessment.html")}, indent=2))


def render_report(s: AssessmentSummary, profiles: Sequence[FieldProfile], path: Path) -> None:
    def esc(value: object) -> str:
        return html.escape(str(value))

    def fmt(value: int | None) -> str:
        return "Not measured" if value is None else f"{value:,}"
    metric_rows = "".join(
        f"<tr><td>{esc(m['rule_id'])}</td><td>{esc(m['dimension'])}</td><td>{esc(m['rule'])}<small>{esc(m['note'])}</small></td><td>{fmt(m['exceptions'])} / {fmt(m['eligible'])}<small>{esc(m['unit'])}; {esc(m['interpretation'])}</small></td><td>{'—' if m['conforming_pct'] is None else str(round(m['conforming_pct'], 3)) + '%'}</td></tr>"
        for m in s["metrics"]
    )
    profile_rows = "".join(f"<tr><td>{esc(p['field'])}</td><td>{esc(p['published_type'])}</td><td>{p['populated']:,}</td><td>{p['missing']:,}</td><td>{p['populated_pct']:.2f}%</td><td>{esc(p['role_for_this_analysis'])}</td></tr>" for p in profiles)
    r = s["spatial"]
    by_id = {m["rule_id"]: m for m in s["metrics"]}
    missing = by_id["C02"]["exceptions"]
    missing_coords = by_id["C03"]["exceptions"]
    zero_coords = by_id["V05"]["exceptions"]
    duplicate_candidates = s["duplicate_event_candidates"]
    assert missing is not None
    pct = 100 * missing / s["records"]
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DMBOK2 data quality assessment — NYC collisions</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;color:#172635;background:#fff;margin:0}}main{{max-width:1200px;margin:40px auto;padding:0 24px 60px}}h1{{font-size:30px;line-height:1.2}}h2{{font-size:23px;margin-top:40px}}h3{{font-size:18px}}p,li{{max-width:1000px}}.muted,small{{color:#536579}}small{{display:block;font-size:12px;line-height:1.5;margin-top:5px}}.lead{{font-size:19px}}.box{{background:#edf4f8;border-left:4px solid #276281;padding:16px 22px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:18px 0}}th{{text-align:left;background:#edf2f6}}td,th{{padding:10px 12px;border-bottom:1px solid #d5dfe7;vertical-align:top}}td:nth-last-child(2),td:last-child{{font-variant-numeric:tabular-nums}}a{{color:#155b8b}}code{{font-size:13px}}.bar{{height:20px;background:#e5eaf0;width:100%;max-width:600px}}.fill{{height:100%;background:#b36b1b}}@media print{{main{{margin:0;padding:0}}.scroll{{overflow:visible}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}</style></head><body><main>
<h1>Data quality assessment against DAMA-DMBOK2</h1>
<p class="muted">NYC Motor Vehicle Collisions — Crashes · 2025 · {s['records']:,} records · {s['published_fields']} published fields</p>
<p class="lead">The snapshot supports a meaningful quality-improvement project. Its main demonstrated limitation for the selected use is incomplete geographic attribution. Strong structural conformance does not establish real-world accuracy.</p>
<div class="box"><strong>Assessment scope.</strong> {esc(s['purpose'])} This is an analyst-defined use case. Requiredness and acceptability targets have not been approved by NYPD or a business stakeholder. Raw records remain unchanged; proposed recoveries are not an achieved after-state.</div>

<h2>1. Method and DMBOK2 basis</h2>
<p>Chapter 13 defines quality relative to consumer needs and critical data (printed p. 457). It presents the DAMA UK six core dimensions on p. 460 while also discussing other frameworks. Table 29, pp. 461–462, distinguishes validity, accuracy, consistency, requiredness, uniqueness and timing. Rules are linked to purpose, scoped denominators and analytical impact (pp. 467–468, 478–479). Measurements use conforming or exception counts over applicable observations (pp. 482–483). A threshold in a book example is not a universal pass mark.</p>
<p>Book pagination in this attachment: PDF page = printed page + 4. Main references: printed pp. 457, 460–463, 467–468, 476–486. No combined quality score is calculated. Counts can overlap; denominators can be records, populated values or cells. Unknown/not measurable remains unknown.</p>

<h2>2. Findings across the six dimensions</h2>
<table><thead><tr><th>Dimension</th><th>Assessment</th><th>Practical meaning</th></tr></thead><tbody>
<tr><td>Completeness</td><td>{missing:,} boroughs missing ({pct:.2f}% of records). Coordinate pairs missing on {missing_coords:,} records.</td><td>Geographic aggregation needs an explicit unknown category or a validated recovery process. Blank ZIP, street alternatives and unused vehicle slots are not blanket failures. Whole-population completeness is not verified.</td></tr>
<tr><td>Consistency</td><td>Scalar/location coordinate pairs agree on all {s['representation']['coordinate_copy_pairs']:,} comparable records. {s['representation']['vehicle_variant_cells']:,} vehicle-type cells differ from their group's modal spelling. {r['disagreeing_records']} eligible borough labels disagree with polygon membership.</td><td>Separate proven representation agreement, proposed spelling standardization, and location conflicts requiring review.</td></tr>
<tr><td>Accuracy</td><td>Not independently established for events, injury/fatality counts or actual crash coordinates.</td><td>An official polygon is a reference for borough derivation conditional on coordinates; it cannot verify that those coordinates describe the crash correctly.</td></tr>
<tr><td>Uniqueness</td><td>Every collision ID is present and distinct. {duplicate_candidates['excess_records']} excess records across {duplicate_candidates['groups']} groups match on every non-ID field ({duplicate_candidates['records_in_groups']} records flagged).</td><td>Key uniqueness passes. The non-ID matches are duplicate-event candidates requiring review, not automatic deletion. Real-world crash uniqueness remains unverified.</td></tr>
<tr><td>Validity</td><td>See explicit date/time, count, coordinate and code-domain checks below. {zero_coords:,} populated coordinate pairs use (0,0).</td><td>These points are unusable for NYC mapping. Flag the location as unknown; preserve the crash and any independently usable borough/count fields.</td></tr>
<tr><td>Timeliness</td><td>Daily update cadence is advertised. Source update: {esc(s['source_freshness']['source_update_utc'])}; metadata observed: {esc(s['source_freshness']['metadata_observed_utc'])}.</td><td>Freshness was observed once; crash-to-first-publication latency and daily SLA adherence cannot be established. Historical 2025 dates fit the selected annual analysis.</td></tr>
</tbody></table>

<h2>3. Geographic recovery pilot</h2>
<p>Of {missing:,} missing boroughs, {r['missing_borough_recovery_candidates']:,} have a unique polygon-interior candidate outside the approximate boundary band. If accepted after validation, {r['missing_borough_remaining_if_candidates_accepted']:,} would remain unresolved. These are candidate derivations, not corrections already applied.</p>
<p>On {r['eligible_known_borough_records']:,} eligible records that already supply a borough, the reference lookup agrees on {r['agreeing_records']:,} ({r['agreement_pct']:.3f}%). The {r['disagreeing_records']} disagreements require review. This is observed agreement, not a ground-truth accuracy estimate. The land-only polygon version may differ from the 2025 shoreline; water/bridge points and near-boundary points need special handling. The exclusion band is 0.0003 degrees, not a guaranteed metric buffer.</p>
<p>Source borough missingness: {pct:.2f}%.</p><div class="bar" aria-label="Borough missingness {pct:.2f}%"><div class="fill" style="width:{pct:.6f}%"></div></div>

<h2>4. Rule-level baseline scorecard</h2>
<p>“Exceptions” can mean a confirmed domain violation, an analytical gap, a proposed representation change or an unconfirmed review signal, as explicitly labeled. A conformance percentage for an unconfirmed rule is descriptive and is not a pass/fail verdict. No before/after improvement is claimed.</p>
<div class="scroll"><table><thead><tr><th>ID</th><th>Dimension</th><th>Rule and limits</th><th>Exceptions / applicable observations</th><th>Conformance</th></tr></thead><tbody>{metric_rows}</tbody></table></div>

<h2>5. Interpretation of the issues raised during inspection</h2>
<ul><li><strong>Numeric strings:</strong> source metadata declares numeric columns as Number. Socrata serializes these values as JSON strings to preserve precision. Validate their logical domains and parse at ingestion; do not count every string as erroneous data.</li>
<li><strong>Repeated location:</strong> the nested location repeats coordinates and its human_address is empty throughout. Agreement is a passing consistency check. Removing redundant location from a curated analytical table is schema simplification.</li>
<li><strong>Different vehicle spellings:</strong> the explicit candidate rule chooses the most frequent exact spelling across all five vehicle slots within each strip/casefold group. There are {s['representation']['vehicle_populated_cells']:,} populated vehicle cells and {s['representation']['vehicle_variant_cells']:,} nonmodal cells on {s['representation']['vehicle_variant_records']:,} records, across {s['representation']['vehicle_multi_spelling_groups']} multi-spelling groups. This is not an official vehicle taxonomy; “PK”, “AMB” and other different words are left alone.</li>
<li><strong>Person/subtype totals:</strong> equality with pedestrian+cyclist+motorist is not established by the source definition. Differences require semantic review; do not overwrite totals or infer that source totals are necessarily wrong.</li></ul>

<h2>6. Prioritized recommendations and monitoring</h2>
<table><thead><tr><th>Priority</th><th>Action and business value</th><th>Proposed control / owner</th></tr></thead><tbody>
<tr><td>1</td><td>Validate conditional borough recovery with versioned official boundaries. Retain original and derived values, derivation rule and source version. Show an unknown category until recovery is accepted.</td><td>Each new snapshot: report borough population, candidate/ambiguous/unmatched counts and changes by period. Analyst proposes; a geographic/domain steward approves rules and target coverage.</td></tr>
<tr><td>1</td><td>Represent (0,0) and missing coordinates as unknown for mapping. Retain crash events for nonspatial and borough-level analysis where borough is known.</td><td>Each load: check pair completeness, numeric/global domains and local-location exceptions. Stop invalid transformations; route source-location exceptions to review.</td></tr>
<tr><td>2</td><td>Use an explicit logical schema: counts as nullable integers, coordinates numeric, ZIP and IDs text, local dates/times parsed without invented timezone offsets. Drop redundant location only in a curated output after checking agreement.</td><td>Each load: numeric/domain checks and duplicated-coordinate agreement. Ingestion maintainer owns the schema; required fields and allowed exceptions need an agreed data contract.</td></tr>
<tr><td>2</td><td>Approve a spelling map for vehicle labels. Keep source codes and do not expand ambiguous abbreviations automatically.</td><td>Each load: detect new spellings and unmapped values. Analyst maintains a versioned map; domain steward approves semantic merges.</td></tr>
<tr><td>3</td><td>Review spatial disagreements, potential duplicate event signatures and person-total differences with source definitions. Do not manufacture corrections.</td><td>Track exception ID, hypothesis, evidence, decision, owner and resolution date. Root causes remain hypotheses until confirmed.</td></tr>
<tr><td>3</td><td>Record acquisition time, source update time, hashes, schema version and revisions across subsequent downloads.</td><td>Monitor freshness against a future agreed SLA; compare snapshots to detect amendments. Portal timestamps alone cannot establish first-publication delay.</td></tr>
</tbody></table>
<p>These are proposed controls and roles for the academic case, not existing agreements with NYC. Operational thresholds must be defined with the data consumer; no institutional policy or interview is claimed.</p>

<h2>7. Full field profile</h2><p>Population rates are descriptive. Supporting/conditional fields are not assumed universally mandatory.</p>
<div class="scroll"><table><thead><tr><th>Field</th><th>Source logical type</th><th>Populated</th><th>Missing</th><th>Population</th><th>Use in assessment</th></tr></thead><tbody>{profile_rows}</tbody></table></div>

<h2>8. Evidence, provenance and reproducibility</h2>
<p>Run <code>uv run analysis/analyze_quality.py</code> from the project, or use the script's absolute path. The script uses local source snapshots, NumPy and matplotlib, makes no network calls and checks that the raw JSON hash remains unchanged. Outputs include the JSON assessment, field profile, rule scorecard, issue register and proposed borough/spelling mappings. The raw data is never overwritten.</p>
<p><a href="../results/quality_metrics.json">Detailed metrics</a> · <a href="../results/quality_scorecard.csv">Rule scorecard</a> · <a href="../results/field_profile.csv">Field profile</a> · <a href="../results/quality_issues.csv">Issue register</a> · <a href="../results/borough_recovery_candidates.csv">Borough recovery candidates</a> · <a href="../results/duplicate_event_candidates.csv">Duplicate-event candidates</a> · <a href="../data/reference/assessment_sources.json">Source manifest</a></p>
<p>Raw JSON SHA-256: <code>{esc(s['raw_sha256'])}</code>. Generated: {esc(s['generated_at_utc'])}. Observed date range: {esc(s['date_min'])} through {esc(s['date_max'])}; all {s['observed_calendar_days']} calendar dates appear. This does not prove every reportable event is represented.</p>
<ul><li>DAMA International, <em>DAMA-DMBOK2</em>, supplied English PDF, Chapter 13. Printed page and PDF page references are distinguished above.</li>
<li><a href="https://data.cityofnewyork.us/d/h9gi-nx95">NYPD crash source and schema</a>. Source describes data as preliminary and subject to amendment.</li>
<li><a href="https://data.cityofnewyork.us/City-Government/Borough-Boundaries/gthc-hcne/data">NYC Department of City Planning borough boundaries</a>.</li>
<li><a href="https://dev.socrata.com/docs/datatypes/number.html">Socrata Number serialization</a> and <a href="https://dev.socrata.com/docs/system-fields.html">system timestamp limitations</a>.</li>
<li><a href="https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-data-collision.page">NYPD collision statistics and source context</a>.</li></ul>
<h3>Limits</h3><ul>{''.join('<li>'+esc(v)+'</li>' for v in s['limitations'])}</ul>
</main></body></html>"""
    _ = path.write_text(document, encoding="utf-8")


if __name__ == "__main__":
    assess()

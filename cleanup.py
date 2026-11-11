#!/usr/bin/env python3
"""Clean the assessed NYC snapshot: uv run cleanup.py

The raw files stay untouched. All crash records are retained.
Types/formats are counted in the summary; substantive changes are logged by ID.
The spatial lookup is shared with the assessment and assumes correct source GPS.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
import csv
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from analysis.analyze_quality import (
    spatial_assessment,
)
from analysis.data_types import (
    HUMAN_ADDRESS_ADAPTER, LOCATION_ADAPTER, BOUNDARIES_ADAPTER, MANIFEST_ADAPTER, METADATA_ADAPTER,
    RAW_ROWS_ADAPTER, STRING_LIST_ADAPTER, CleanRow, RawRow,
)


from analysis.domain import (
    COUNT_FIELDS, VEHICLE_FIELDS, FLAGS, coordinate_value, count_value, domain_report, geographic_domain, local_time,
)


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data/raw/nyc_collisions_2025.json"
REFERENCE = ROOT / "data/reference/nyc_borough_boundaries.geojson"
METADATA = ROOT / "data/reference/nyc_collisions_metadata.json"
MANIFEST = ROOT / "data/reference/assessment_sources.json"
CLEAN = ROOT / "data/cleaned"
RESULTS = ROOT / "results"


def read_json[T](path: Path, adapter: TypeAdapter[T]) -> T:
    """Parse and validate a source file without silently coercing its values."""
    return adapter.validate_json(path.read_bytes(), strict=True)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source_snapshots(root: Path = ROOT) -> None:
    """Use the exact raw data and references that were assessed."""
    for source in read_json(root / "data/reference/assessment_sources.json", MANIFEST_ADAPTER):
        if file_hash(root / source["file"]) != source["sha256"]:
            raise ValueError(f"Source changed; reassess before cleaning: {source['file']}")


def nullable_text(value: object) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def check_location_is_redundant(row: RawRow) -> None:
    """Refuse to drop location if it contains conflicting or extra information."""
    location = row.get("location")
    if location is None:
        return
    if not isinstance(location, dict):
        raise ValueError(f"Expected a location object for {row['collision_id']}")
    if set(location) - {"latitude", "longitude", "human_address"}:
        raise ValueError(f"Unexpected location information for {row['collision_id']}")
    values = LOCATION_ADAPTER.validate_python(location, strict=True)
    for field in ("latitude", "longitude"):
        if row.get(field) is None or values.get(field) is None:
            raise ValueError(f"Location is not redundant for {row['collision_id']}")
        if Decimal(str(row[field])) != Decimal(str(values[field])):
            raise ValueError(f"Coordinate disagreement for {row['collision_id']}")
    address_json = values.get("human_address")
    if address_json:
        address = HUMAN_ADDRESS_ADAPTER.validate_json(address_json, strict=True)
        if any(nullable_text(value) for value in address.values()):
            raise ValueError(f"Location contains an address for {row['collision_id']}")


def preferred_vehicle_spellings(rows: Sequence[RawRow]) -> dict[str, str]:
    """Merge case variants only. Do not guess what abbreviations mean."""
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for field in VEHICLE_FIELDS:
            value = nullable_text(row.get(field))
            if value:
                counts[value.casefold()][value] += 1
    return {
        key: sorted(spellings, key=lambda value: (-spellings[value], value))[0]
        for key, spellings in counts.items()
    }


def duplicate_groups(
    rows: Sequence[Mapping[str, object]], fields: Sequence[str],
) -> dict[str, list[int]]:
    """Exact non-ID matches are review candidates, not grounds for deletion."""
    groups: defaultdict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        values = {field: row.get(field) for field in fields if field != "collision_id"}
        encoded = json.dumps(values, sort_keys=True, ensure_ascii=False).encode()
        groups[hashlib.sha256(encoded).hexdigest()].append(index)
    return {key: indices for key, indices in groups.items() if len(indices) > 1}


def write_csv(
    path: Path, rows: Iterable[Mapping[str, object]], fields: Sequence[str],
) -> None:
    """CSV is for browsing; JSON retains actual numbers, nulls and flag lists."""
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            values: dict[str, object] = {}
            for field in fields:
                value = row.get(field)
                # Only flag lists are written as lists; stringify for CSV browsing.
                values[field] = "; ".join(STRING_LIST_ADAPTER.validate_python(value, strict=True)) if isinstance(value, list) else value
            writer.writerow(values)


def quality_flags(row: CleanRow) -> list[str]:
    flags = row["quality_flags"]
    if not isinstance(flags, list):
        raise ValueError("Expected a quality flag list")
    return flags


def clean(root: Path = ROOT) -> None:
    raw_path = root / "data/raw/nyc_collisions_2025.json"
    metadata_path = root / "data/reference/nyc_collisions_metadata.json"
    reference_path = root / "data/reference/nyc_borough_boundaries.geojson"
    clean_dir = root / "data/cleaned"
    results_dir = root / "results"
    verify_source_snapshots(root)
    original_hash = file_hash(raw_path)
    raw_rows = read_json(raw_path, RAW_ROWS_ADAPTER)
    source_ids = [row.get("collision_id") for row in raw_rows]
    metadata = read_json(metadata_path, METADATA_ADAPTER)
    raw_fields = [column["fieldName"] for column in metadata["columns"]
                  if not column["fieldName"].startswith(":")]
    observed_fields = {field for row in raw_rows for field in row}
    raw_fields = [field for field in raw_fields if field in observed_fields]
    if set(raw_fields) != observed_fields:
        raise ValueError("The source schema changed; reassess the field definitions")
    remove_location = True
    for original in raw_rows:
        try:
            check_location_is_redundant(original)
        except (ValueError, DecimalException):
            remove_location = False
            break
    data_fields = list(dict.fromkeys([field for field in raw_fields if field != "location" or not remove_location]
                                    + ["collision_id", "crash_date", "crash_time", *COUNT_FIELDS,
                                       "borough", "latitude", "longitude", "zip_code"]))

    # Prepare the two explicit lookup rules from the untouched source snapshot.
    spellings = preferred_vehicle_spellings(raw_rows)
    geometry = read_json(reference_path, BOUNDARIES_ADAPTER)
    domain = geographic_domain(geometry)
    spatial, matches, conflict_indices, recoverable, outside_indices = spatial_assessment(raw_rows, geometry)
    borough_lookup = {index: matches[index][0] for index in recoverable}
    spatial_conflicts, outside = set(conflict_indices), set(outside_indices)
    original_duplicates = duplicate_groups(raw_rows, raw_fields)
    changes: list[dict[str, object]] = []
    cleaned_rows: list[CleanRow] = []
    operations: Counter[str] = Counter()

    def log_change(
        source_row: int, row_id: object, rule: str, field: str, before: object, after: object, reason: str,
    ) -> None:
        changes.append({
            "source_row": source_row, "collision_id": row_id, "rule": rule, "field": field,
            "before_json": json.dumps(before, ensure_ascii=False),
            "after_json": json.dumps(after, ensure_ascii=False), "reason": reason,
        })
        operations[rule] += 1

    for index, original in enumerate(raw_rows):
        row_id = original.get("collision_id")
        flags: set[str] = set()

        # 1. Remove location only after checking that its coordinates agree
        #    and its human_address contains no additional information.
        operations["redundant_location_objects_removed"] += remove_location and original.get("location") is not None
        row: CleanRow = {}
        for field in data_fields:
            value = original.get(field)
            row[field] = value if field in ("location", "collision_id") else nullable_text(value)
            operations["missing_fields_made_explicit_null"] += field not in original
            operations["blank_strings_to_null"] += isinstance(value, str) and not value.strip()
            operations["text_values_trimmed"] += field != "collision_id" and isinstance(value, str) and value != value.strip()

        # 2. Convert valid counts and finite coordinates. Keep invalid source values.
        counts = {field: count_value(original.get(field)) for field in COUNT_FIELDS}
        for field, value in counts.items():
            if value is not None or nullable_text(original.get(field)) is None:
                row[field] = value
            operations["count_values_typed"] += value is not None
        coordinates = {field: coordinate_value(original.get(field)) for field in ("latitude", "longitude")}
        for field, coordinate in coordinates.items():
            if coordinate is not None or nullable_text(original.get(field)) is None:
                row[field] = coordinate
            operations["coordinate_values_typed"] += coordinate is not None

        # 3. Normalize parseable local dates/times; invalid strings remain available.
        date = nullable_text(original.get("crash_date"))
        if date is not None:
            try:
                row["crash_date"] = datetime.fromisoformat(date).date().isoformat()
            except ValueError:
                pass
        clock = nullable_text(original.get("crash_time"))
        if clock is not None:
            try:
                parsed_clock = local_time(clock)
                row["crash_time"] = parsed_clock.isoformat(timespec="seconds" if parsed_clock.second else "minutes")
            except ValueError:
                pass
        operations["date_formats_normalized"] += row["crash_date"] != original.get("crash_date")
        operations["time_formats_normalized"] += row["crash_time"] != original.get("crash_time")

        # 4. Keep supplied coordinates and label their relation to the domain.
        lat, lon = coordinates["latitude"], coordinates["longitude"]
        if lat is None and lon is None:
            row["coordinate_status"] = "invalid" if any(original.get(f) for f in coordinates) else "unknown_in_source"
        elif lat is None or lon is None:
            row["coordinate_status"] = "partial_or_invalid"
        elif not (-90 <= lat <= 90 and -180 <= lon <= 180):
            row["coordinate_status"] = "invalid"
        elif not domain.contains(lat, lon):
            row["coordinate_status"] = "outside_nyc_domain"
        else:
            row["coordinate_status"] = "within_nyc_domain"

        # 5. Use the most frequent spelling within each exact casefold group.
        #    This changes presentation, not the meaning of a vehicle category.
        for field in VEHICLE_FIELDS:
            value = nullable_text(original.get(field))
            if value and value != spellings[value.casefold()]:
                row[field] = spellings[value.casefold()]
                log_change(index, row_id, "vehicle_case_normalized", field, value, row[field],
                           "Most frequent spelling within the same casefold group")

        # 6. Fill missing boroughs only for the checked polygon-interior
        #    candidates. Never overwrite a supplied borough, even on conflict.
        row["borough_origin"] = "source" if row["borough"] else "unknown"
        if row["borough"] is None and index in borough_lookup:
            row["borough"] = borough_lookup[index]
            row["borough_origin"] = "derived_from_coordinates"
            log_change(index, row_id, "borough_derived", "borough", None, row["borough"],
                       "Unique official polygon interior, away from the approximate boundary band; assumes correct source coordinates")
        if row["borough"] is None:
            flags.add("borough_unknown")
        if index in spatial_conflicts:
            flags.add("borough_coordinate_conflict")
        if index in outside:
            flags.add("coordinate_reference_envelope_conflict")

        # 7. Preserve person totals. Differences from the three role subtotals
        #    need source interpretation, not an automatic arithmetic correction.
        for outcome in ("injured", "killed"):
            total = counts[f"number_of_persons_{outcome}"]
            parts = [counts[f"number_of_{role}_{outcome}"]
                     for role in ("pedestrians", "cyclist", "motorist")]
            known_parts = [value for value in parts if value is not None]
            if total is not None and len(known_parts) == len(parts) and total != sum(known_parts):
                flags.add(f"person_role_total_difference_{outcome}")
        row["quality_flags"] = sorted(flags)
        cleaned_rows.append(row)

    # 8. Flag non-ID matches again after normalization. Keep every record.
    #    Exclude our added provenance/status columns from the matching rule.
    final_duplicates = duplicate_groups(cleaned_rows, data_fields)
    duplicate_candidates: list[dict[str, object]] = []
    for group, indices in final_duplicates.items():
        for index in indices:
            row = cleaned_rows[index]
            row["quality_flags"] = sorted(set(quality_flags(row)) | {"possible_duplicate_event"})
            duplicate_candidates.append({"group": group, "collision_id": row["collision_id"],
                                         "group_size": len(indices), "status": "retained_for_review"})

    domain_checks = domain_report(raw_rows, cleaned_rows, domain)
    for alert in domain_checks["alerts"]:
        if alert["stage"] == "cleaned":
            row = cleaned_rows[alert["source_row"]]
            row["quality_flags"] = sorted(set(quality_flags(row)) | {FLAGS[alert["rule_id"]]})

    if [row["collision_id"] for row in cleaned_rows] != source_ids:
        raise ValueError("Cleanup changed the record identities or their order")
    if file_hash(raw_path) != original_hash:
        raise ValueError("The raw source changed during cleanup")

    clean_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(exist_ok=True)
    output_fields = data_fields + ["borough_origin", "coordinate_status", "quality_flags"]
    json_path = clean_dir / "nyc_collisions_2025.json"
    _ = json_path.write_text(json.dumps(cleaned_rows, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    write_csv(clean_dir / "nyc_collisions_2025.csv", cleaned_rows, output_fields)
    write_csv(results_dir / "cleanup_changes.csv", changes,
              ["source_row", "collision_id", "rule", "field", "before_json", "after_json", "reason"])
    write_csv(results_dir / "cleanup_review.csv",
              [{"collision_id": row["collision_id"], "flag": flag}
               for row in cleaned_rows for flag in quality_flags(row)], ["collision_id", "flag"])
    write_csv(results_dir / "cleanup_duplicate_candidates.csv", duplicate_candidates,
              ["group", "collision_id", "group_size", "status"])
    write_csv(results_dir / "domain_alerts.csv", domain_checks["alerts"],
              ["stage", "source_row", "collision_id", "rule_id", "field", "severity", "observed_json", "expected"])
    _ = (results_dir / "domain_validation.json").write_text(json.dumps(domain_checks, ensure_ascii=False, indent=2) + "\n")
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": original_hash, "cleaned_json_sha256": file_hash(json_path),
        "records_before": len(raw_rows), "records_after": len(cleaned_rows),
        "records_deleted": 0, "output_fields": len(output_fields),
        "operations": dict(operations),
        "borough_missing_before": sum(not row.get("borough") for row in raw_rows),
        "borough_missing_after": sum(row["borough"] is None for row in cleaned_rows),
        "coordinate_pairs_missing_before": sum(not row.get("latitude") or not row.get("longitude") for row in raw_rows),
        "coordinate_pairs_missing_after": sum(row["latitude"] is None or row["longitude"] is None for row in cleaned_rows),
        "duplicate_groups_before": len(original_duplicates),
        "duplicate_groups_after_normalization": len(final_duplicates),
        "duplicate_candidate_records_retained": len(duplicate_candidates),
        "review_flags": dict(Counter(flag for row in cleaned_rows for flag in quality_flags(row))),
        "reference_sha256": file_hash(reference_path),
        "spatial_rule": spatial["limits"].replace("No recovery was applied.", "Eligible missing boroughs were derived and labeled; supplied boroughs were preserved."),
        "audit_scope": "Substantive borough/coordinate/category changes are logged by ID. Type conversion, null representation, date/time formatting and redundant-column removal are counted above. Invalid domain values are preserved and reported in domain_alerts.csv with their source row and rule.",
    }
    _ = (results_dir / "cleanup_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=4))


if __name__ == "__main__":
    clean()

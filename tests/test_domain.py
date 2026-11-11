"""Domain boundaries and preservation of invalid records through cleanup."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
from typing import final
import unittest

from analysis.data_types import BOUNDARIES_ADAPTER, CLEAN_ROWS_ADAPTER, RawRow
from analysis.domain import (
    COUNT_FIELDS, DOMAIN_REPORT_ADAPTER, geographic_domain, usable_coordinates, validate_domain,
)
from cleanup import clean, file_hash, quality_flags


def boundaries() -> bytes:
    return json.dumps({"features": [{"properties": {"boroname": "BROOKLYN"},
        "geometry": {"type": "Polygon", "coordinates": [[[-74.25, 40.5], [-73.7, 40.5],
            [-73.7, 40.9], [-74.25, 40.9], [-74.25, 40.5]]]}}]}).encode()


def valid_row(row_id: str = "1") -> RawRow:
    return {"collision_id": row_id, "crash_date": "2025-02-03T00:00:00.000", "crash_time": "9:20",
            "latitude": "40.7", "longitude": "-73.9", "borough": "BROOKLYN", "zip_code": "11201",
            **{field: "0" for field in COUNT_FIELDS}}


@final
class DomainTests(unittest.TestCase):
    def test_geographic_margin_and_inclusive_boundaries(self) -> None:
        geometry = BOUNDARIES_ADAPTER.validate_json(boundaries(), strict=True)
        bare = geographic_domain(geometry, margin_km=0)
        domain = geographic_domain(geometry)
        self.assertTrue(domain.contains(40.7, -74.27))  # Outside bare envelope, inside margin.
        self.assertFalse(bare.contains(40.7, -74.27))
        for lat, lon in ((domain.south, domain.west), (domain.north, domain.east)):
            self.assertTrue(domain.contains(lat, lon))
        self.assertFalse(domain.contains(domain.north + 0.000001, domain.east))
        self.assertFalse(domain.contains(domain.north, domain.east + 0.000001))
        for margin in (-1.0, float("nan"), float("inf")):
            with self.subTest(margin=margin), self.assertRaises(ValueError):
                _ = geographic_domain(geometry, margin_km=margin)

    def test_nonzero_remote_coordinates_generate_alerts_without_mutation(self) -> None:
        domain = geographic_domain(BOUNDARIES_ADAPTER.validate_json(boundaries(), strict=True))
        for lat, lon in (("0", "0"), ("41.5", "-73.9"), ("40.7", "73.9")):
            row = valid_row()
            row.update(latitude=lat, longitude=lon)
            original = deepcopy(row)
            alerts = validate_domain([row], domain, "raw")
            self.assertEqual([a["rule_id"] for a in alerts], ["V08"])
            self.assertFalse(usable_coordinates(row, domain))
            self.assertEqual(row, original)

    def test_all_field_violations_are_reported_together(self) -> None:
        domain = geographic_domain(BOUNDARIES_ADAPTER.validate_json(boundaries(), strict=True))
        row = valid_row()
        row.update(crash_date="2024-12-31", crash_time="25:00", borough="Boston", zip_code="abc",
                   latitude="Infinity", number_of_persons_killed="-1", number_of_persons_injured="1.5")
        alerts = validate_domain([row, valid_row()], domain, "raw")
        self.assertEqual({a["rule_id"] for a in alerts}, {"V01", "V02", "V03", "V04", "V06", "V07", "U01"})
        self.assertEqual(len([a for a in alerts if a["rule_id"] == "V03"]), 2)
        self.assertEqual({a["source_row"] for a in alerts if a["rule_id"] == "U01"}, {0, 1})

    def test_partial_coordinates_missing_id_and_excess_role_subtotal(self) -> None:
        domain = geographic_domain(BOUNDARIES_ADAPTER.validate_json(boundaries(), strict=True))
        row = valid_row("")
        row.update(longitude=None, number_of_pedestrians_injured="1")
        self.assertEqual({a["rule_id"] for a in validate_domain([row], domain, "raw")}, {"C01", "S01", "S04_injured"})

    def test_cleanup_preserves_bad_values_duplicate_ids_and_all_rows(self) -> None:
        rows = [valid_row(), valid_row(), valid_row("3"), valid_row("4"), valid_row("5")]
        rows[0]["location"] = {"latitude": "40.7", "longitude": "-73.9", "human_address": '{"city":"New York"}'}
        rows[0].update(latitude="41.5", number_of_persons_killed="-2", crash_date="bad-date", zip_code="bad")
        rows[1].update(latitude="0", longitude="0", crash_time="25:00", borough="Boston")
        rows[2].update(latitude="Infinity", number_of_persons_injured="1.5")
        rows[3].update(longitude=None)
        _ = rows[4].pop("collision_id")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data/raw").mkdir(parents=True)
            (root / "data/reference").mkdir()
            _ = (root / "data/raw/nyc_collisions_2025.json").write_text(json.dumps(rows))
            _ = (root / "data/reference/nyc_borough_boundaries.geojson").write_bytes(boundaries())
            metadata = {"rowsUpdatedAt": 123, "columns": [
                {"fieldName": key, "dataTypeName": "Text"} for key in dict.fromkeys(key for row in rows for key in row)]}
            _ = (root / "data/reference/nyc_collisions_metadata.json").write_text(json.dumps(metadata))
            files = ["data/raw/nyc_collisions_2025.json", "data/reference/nyc_collisions_metadata.json",
                     "data/reference/nyc_borough_boundaries.geojson"]
            manifest = [{"file": file, "sha256": file_hash(root / file), "url": "https://example.org",
                         "observed_at_utc": "2026-11-11T00:00:00+00:00"} for file in files]
            _ = (root / "data/reference/assessment_sources.json").write_text(json.dumps(manifest))
            with redirect_stdout(StringIO()):
                clean(root)
            cleaned = CLEAN_ROWS_ADAPTER.validate_json((root / "data/cleaned/nyc_collisions_2025.json").read_bytes(), strict=True)
            report = DOMAIN_REPORT_ADAPTER.validate_json((root / "results/domain_validation.json").read_bytes(), strict=True)
            self.assertEqual(len(cleaned), len(rows))
            self.assertEqual([r.get("collision_id") for r in rows], [r["collision_id"] for r in cleaned])
            self.assertEqual(cleaned[0]["latitude"], 41.5)
            self.assertEqual(cleaned[0]["location"], rows[0]["location"])
            self.assertIn("location_coordinate_conflict", quality_flags(cleaned[0]))
            self.assertEqual(cleaned[0]["number_of_persons_killed"], "-2")
            self.assertEqual(cleaned[0]["crash_date"], "bad-date")
            self.assertEqual(cleaned[1]["latitude"], 0.0)
            self.assertEqual(cleaned[1]["longitude"], 0.0)
            self.assertEqual(cleaned[2]["latitude"], "Infinity")
            self.assertEqual(cleaned[2]["number_of_persons_injured"], "1.5")
            self.assertEqual(cleaned[3]["longitude"], None)
            self.assertIn("coordinates_outside_nyc", quality_flags(cleaned[0]))
            self.assertIn("id_duplicate", quality_flags(cleaned[0]))
            self.assertEqual(report["cleaned_affected_records"], 5)
            self.assertEqual(file_hash(root / files[0]), manifest[0]["sha256"])


if __name__ == "__main__":
    _ = unittest.main()

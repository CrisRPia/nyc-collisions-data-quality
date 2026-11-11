"""Acquisition controls tested without accessing the public API."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from typing import final, override

import httpx

from analysis.acquire_data import prepare_workspace
from analysis.data_types import RAW_ROWS_ADAPTER


@final
class AcquisitionTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.count_requests = 0
        self.change_count = False
        self.duplicate_id = False

    @override
    def setUp(self) -> None:
        (self.root / "reports").mkdir()
        _ = (self.root / "reports/monitoring_policy.txt").write_text("Proposed policy")

    @override
    def tearDown(self) -> None:
        self.temporary.cleanup()

    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/views/"):
            return httpx.Response(200, json={"columns": [], "rowsUpdatedAt": 123})
        if request.url.path.endswith(".geojson"):
            feature = {
                "properties": {"boroname": "BRONX"},
                "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]]},
            }
            return httpx.Response(200, json={"features": [feature] * 5})
        if "$select" in request.url.params:
            self.count_requests += 1
            count = "4" if self.change_count and self.count_requests % 2 == 0 else "3"
            return httpx.Response(200, json=[{"count": count}])
        offset = int(request.url.params["$offset"])
        rows = [{"collision_id": str(index), "number_of_persons_killed": "0"}
                for index in range(1, 4)]
        if self.duplicate_id:
            rows[2]["collision_id"] = "1"
        return httpx.Response(200, json=rows[offset:offset + 2])

    def prepare(self, *, refresh: bool = False) -> Path:
        client = httpx.Client(transport=httpx.MockTransport(self.respond))
        try:
            with patch("analysis.acquire_data.httpx.Client", return_value=client), patch("analysis.acquire_data.PAGE_SIZE", 2):
                return prepare_workspace(self.root, refresh=refresh)
        finally:
            client.close()

    def test_pagination_preserves_values_and_reuses_verified_cache(self) -> None:
        workspace = self.prepare()
        rows = RAW_ROWS_ADAPTER.validate_json((workspace / "data/raw/nyc_collisions_2025.json").read_bytes(), strict=True)
        self.assertEqual([row["collision_id"] for row in rows], ["1", "2", "3"])
        self.assertTrue(all(row["number_of_persons_killed"] == "0" for row in rows))
        self.assertEqual(len(list((workspace / "data/raw/pages").glob("*.json"))), 2)
        # The real HTTP client must never be opened when a valid cache exists.
        with patch("analysis.acquire_data.httpx.Client", side_effect=AssertionError("Unexpected network access")):
            self.assertEqual(prepare_workspace(self.root), workspace)

    def test_changed_control_count_does_not_publish_partial_workspace(self) -> None:
        self.change_count = True
        with self.assertRaisesRegex(ValueError, "changed during acquisition"):
            _ = self.prepare()
        self.assertFalse((self.root / "runs/notebook-download-2025").exists())
        self.assertEqual(list((self.root / "runs").iterdir()), [])

    def test_duplicate_ids_are_preserved_for_domain_alerts(self) -> None:
        self.duplicate_id = True
        workspace = self.prepare()
        rows = RAW_ROWS_ADAPTER.validate_json((workspace / "data/raw/nyc_collisions_2025.json").read_bytes(), strict=True)
        self.assertEqual([row["collision_id"] for row in rows], ["1", "2", "1"])

    def test_refresh_preserves_old_snapshot_and_tampering_fails(self) -> None:
        first = self.prepare()
        path = first / "data/raw/nyc_collisions_2025.json"
        original = path.read_bytes()
        second = self.prepare(refresh=True)
        self.assertNotEqual(first, second)
        self.assertEqual(path.read_bytes(), original)
        _ = path.write_text("[]")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            _ = prepare_workspace(self.root)


if __name__ == "__main__":
    _ = unittest.main()

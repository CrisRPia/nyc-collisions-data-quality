"""Regression checks for payload contracts, including valid JSON with bad shapes."""

import json
from typing import final
import unittest

from pydantic import ValidationError

from analysis.data_types import (
    ACQUISITION_ADAPTER, HUMAN_ADDRESS_ADAPTER, LOCATION_ADAPTER, SCORECARD_ADAPTER,
    DownloadAcquisition, ScorecardSummary,
)
from cleanup import check_location_is_redundant


def download_payload() -> DownloadAcquisition:
    return {
        "mode": "download", "scope": "year = 2025", "records": 1,
        "api_count_before": 1, "api_count_after": 1, "source_rows_updated_at": 123,
        "page_size": 10_000,
        "requests": [{"url": "https://example.org/data", "observed_at_utc": "2026-11-11T00:00:00+00:00",
                      "sha256": "a" * 64, "bytes": 100}],
        "raw_representation": "Original source values", "consistency_limit": "Not an atomic transaction",
    }


def scorecard_payload() -> ScorecardSummary:
    return {
        "raw_sha256": "a" * 64, "cleaned_sha256": "b" * 64, "reference_sha256": "c" * 64,
        "records_before": 1, "records_after": 1, "before": [], "after": [], "comparison": [],
        "method": "Explicit denominators",
    }


@final
class PayloadContractTests(unittest.TestCase):
    def test_acquisition_accepts_both_declared_modes(self) -> None:
        downloaded = download_payload()
        self.assertEqual(ACQUISITION_ADAPTER.validate_json(json.dumps(downloaded), strict=True), downloaded)
        original = {"mode": "original", "note": "Pinned inputs copied"}
        self.assertEqual(ACQUISITION_ADAPTER.validate_json(json.dumps(original), strict=True), original)

    def test_acquisition_rejects_missing_fields_wrong_types_and_unknown_modes(self) -> None:
        for changes in ({"records": "1"}, {"records": -1}, {"mode": "surprise"}, {"page_size": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                _ = ACQUISITION_ADAPTER.validate_json(json.dumps({**download_payload(), **changes}), strict=True)
        missing: dict[str, object] = {**download_payload()}
        del missing["requests"]
        with self.assertRaises(ValidationError):
            _ = ACQUISITION_ADAPTER.validate_json(json.dumps(missing), strict=True)
        with self.assertRaises(ValidationError):
            _ = ACQUISITION_ADAPTER.validate_json('{"mode":"original","note":"ok","records":1}', strict=True)

    def test_acquisition_validates_nested_request_fields(self) -> None:
        for changes in ({"bytes": "100"}, {"bytes": -1}, {"sha256": "not-a-hash"}):
            request: dict[str, object] = {**download_payload()["requests"][0], **changes}
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                _ = ACQUISITION_ADAPTER.validate_json(
                    json.dumps({**download_payload(), "requests": [request]}), strict=True)

    def test_scorecard_validates_wrapper_and_nested_comparisons(self) -> None:
        payload = scorecard_payload()
        self.assertEqual(SCORECARD_ADAPTER.validate_json(json.dumps(payload), strict=True), payload)
        empty_row: dict[str, object] = {}
        invalid_payloads: tuple[dict[str, object], ...] = (
            {"records_after": "1"}, {"comparison": [empty_row]}, {"before": [empty_row]}, {"raw_sha256": "bad"},
        )
        for changes in invalid_payloads:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                _ = SCORECARD_ADAPTER.validate_json(json.dumps({**payload, **changes}), strict=True)
        missing: dict[str, object] = {**payload}
        del missing["comparison"]
        with self.assertRaises(ValidationError):
            _ = SCORECARD_ADAPTER.validate_json(json.dumps(missing), strict=True)

    def test_location_and_address_are_distinct_contracts(self) -> None:
        location = {"latitude": "40.7", "longitude": "-73.9", "human_address": "{}"}
        self.assertEqual(LOCATION_ADAPTER.validate_json(json.dumps(location), strict=True), location)
        self.assertEqual(HUMAN_ADDRESS_ADAPTER.validate_json('{"city":""}', strict=True), {"city": ""})
        for malformed in ({"latitude": "40.7"}, {"latitude": 40.7, "longitude": "-73.9"}):
            with self.subTest(malformed=malformed), self.assertRaises(ValidationError):
                _ = LOCATION_ADAPTER.validate_json(json.dumps(malformed), strict=True)
        empty_values: list[str] = []
        invalid_addresses: tuple[object, ...] = (empty_values, {"city": empty_values}, {"unexpected_field": ""})
        for malformed in invalid_addresses:
            with self.subTest(malformed=malformed), self.assertRaises(ValidationError):
                _ = HUMAN_ADDRESS_ADAPTER.validate_json(json.dumps(malformed), strict=True)

    def test_cleanup_refuses_to_drop_malformed_or_populated_addresses(self) -> None:
        for address in ('[]', '{"city":[]}', '{"unexpected_field":""}', '{"city":"New York"}'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                check_location_is_redundant({
                    "collision_id": "1", "latitude": "40.7", "longitude": "-73.9",
                    "location": {"latitude": "40.7", "longitude": "-73.9", "human_address": address},
                })


if __name__ == "__main__":
    _ = unittest.main()

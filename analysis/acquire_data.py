"""Acquire a validated 2025 snapshot without replacing the original project data.

Socrata pages are ordered by collision_id and retained byte-for-byte. Their
aggregate JSON preserves source values, including numeric strings and nulls.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Literal, TypedDict

import httpx
from pydantic import TypeAdapter

from analysis.data_types import (
    BOUNDARIES_ADAPTER, MANIFEST_ADAPTER, METADATA_ADAPTER, RAW_ROWS_ADAPTER,
    DownloadAcquisition, OriginalAcquisition, RawRow, RequestRecord, SourceSnapshot,
)
from cleanup import verify_source_snapshots

API = "https://data.cityofnewyork.us/resource/h9gi-nx95.json"
METADATA_URL = "https://data.cityofnewyork.us/api/views/h9gi-nx95.json"
BOUNDARIES_URL = "https://data.cityofnewyork.us/resource/gthc-hcne.geojson?$limit=5"
SCOPE = "crash_date >= '2025-01-01T00:00:00' AND crash_date < '2026-01-01T00:00:00'"
PAGE_SIZE = 10_000


class CountRow(TypedDict):
    count: str


COUNT_ADAPTER = TypeAdapter(list[CountRow])


def fetch(client: httpx.Client, url: str, log: list[RequestRecord]) -> bytes:
    """Retry transient HTTP failures, recording the exact successful response."""
    for attempt in range(3):
        try:
            response = client.get(url)
            _ = response.raise_for_status()
            content = response.content
            log.append({
                "url": str(response.url),
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            })
            return content
        except httpx.HTTPError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("HTTP retries exhausted")


def query_url(**params: str | int) -> str:
    return str(httpx.URL(API, params=params))


def download_inputs(destination: Path, client: httpx.Client) -> None:
    """Populate a staging directory; callers publish it only after all checks."""
    requests: list[RequestRecord] = []
    manifest: list[SourceSnapshot] = []
    metadata_bytes = fetch(client, METADATA_URL, requests)
    metadata_before = METADATA_ADAPTER.validate_json(metadata_bytes, strict=True)
    boundary_bytes = fetch(client, BOUNDARIES_URL, requests)
    boundaries = BOUNDARIES_ADAPTER.validate_json(boundary_bytes, strict=True)
    if len(boundaries["features"]) != 5:
        raise ValueError("Expected the five official borough features")

    count_url = query_url(**{"$where": SCOPE, "$select": "count(*)"})
    count_rows = COUNT_ADAPTER.validate_json(fetch(client, count_url, requests), strict=True)
    if len(count_rows) != 1:
        raise ValueError("Expected one API control count")
    expected = int(count_rows[0]["count"])
    if expected <= 0:
        raise ValueError("The selected annual dataset is unexpectedly empty")

    pages = destination / "data/raw/pages"
    pages.mkdir(parents=True)
    rows: list[RawRow] = []
    offset = 0
    while offset < expected:
        url = query_url(**{"$where": SCOPE, "$order": "collision_id ASC", "$limit": PAGE_SIZE, "$offset": offset})
        page_bytes = fetch(client, url, requests)
        page = RAW_ROWS_ADAPTER.validate_json(page_bytes, strict=True)
        if not page or len(page) > PAGE_SIZE:
            raise ValueError("Unexpected page size; no partial snapshot will be published")
        _ = (pages / f"{offset:07d}.json").write_bytes(page_bytes)
        rows.extend(page)
        offset += len(page)
        print(f"Downloaded {len(rows):,} / {expected:,} records")

    count_after = COUNT_ADAPTER.validate_json(fetch(client, count_url, requests), strict=True)
    metadata_after = METADATA_ADAPTER.validate_json(fetch(client, METADATA_URL, requests), strict=True)
    if count_after != count_rows or metadata_after["rowsUpdatedAt"] != metadata_before["rowsUpdatedAt"]:
        raise ValueError("The live source changed during acquisition; retry for a consistent snapshot")
    if len(rows) != expected:
        raise ValueError("Downloaded rows do not match the API control count")
    # ID/domain violations remain in the snapshot for row-level validation.

    raw_bytes = (json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    files = (
        ("data/raw/nyc_collisions_2025.json", raw_bytes, query_url(**{"$where": SCOPE, "$order": "collision_id ASC"})),
        ("data/reference/nyc_collisions_metadata.json", metadata_bytes, METADATA_URL),
        ("data/reference/nyc_borough_boundaries.geojson", boundary_bytes, BOUNDARIES_URL),
    )
    for relative, content, url in files:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(content)
        manifest.append({
            "file": relative, "url": url,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    _ = (destination / "data/reference/assessment_sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    acquisition: DownloadAcquisition = {
        "mode": "download", "scope": SCOPE, "records": len(rows),
        "api_count_before": expected, "api_count_after": int(count_after[0]["count"]),
        "source_rows_updated_at": metadata_before["rowsUpdatedAt"],
        "page_size": PAGE_SIZE, "requests": requests,
        "raw_representation": "JSON aggregate of ordered, validated pages. Exact page response bytes are retained in data/raw/pages; source values have not been cleaned.",
        "consistency_limit": "Stable control counts and update metadata reduce acquisition risk but do not prove an atomic API transaction or real-world event completeness.",
    }
    _ = (destination / "data/reference/acquisition.json").write_text(json.dumps(acquisition, indent=2) + "\n", encoding="utf-8")


def prepare_workspace(
    project_root: Path, *, mode: Literal["download", "original"] = "download", refresh: bool = False,
) -> Path:
    """Reuse a verified cache, or stage a new snapshot in a separate run folder.

    A refresh creates a new timestamped folder. Interrupted downloads never
    replace an existing snapshot. Original project sources remain untouched.
    """
    runs = project_root / "runs"
    runs.mkdir(exist_ok=True)
    destination = runs / f"notebook-{mode}-2025"
    if refresh:
        destination = runs / f"notebook-{mode}-2025-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
    if destination.exists():
        verify_source_snapshots(destination)
        print(f"Reusing verified cached snapshot: {destination.relative_to(project_root)}")
        return destination

    with tempfile.TemporaryDirectory(prefix=".acquiring-", dir=runs) as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir()
        if mode == "original":
            verify_source_snapshots(project_root)
            sources = MANIFEST_ADAPTER.validate_json((project_root / "data/reference/assessment_sources.json").read_bytes(), strict=True)
            for relative in [source["file"] for source in sources] + ["data/reference/assessment_sources.json"]:
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(project_root / relative, path)
            acquisition: OriginalAcquisition = {
                "mode": "original", "note": "Copied the original project's pinned inputs without changing their bytes.",
            }
            _ = (staging / "data/reference/acquisition.json").write_text(json.dumps(acquisition) + "\n", encoding="utf-8")
        else:
            with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "DMBOK2-academic-data-quality/0.1"}) as client:
                download_inputs(staging, client)
        for directory in ("reports", "results"):
            (staging / directory).mkdir()
        _ = shutil.copy2(project_root / "reports/monitoring_policy.txt", staging / "reports/monitoring_policy.txt")
        verify_source_snapshots(staging)
        _ = staging.rename(destination)
    print(f"Prepared snapshot: {destination.relative_to(project_root)}")
    return destination

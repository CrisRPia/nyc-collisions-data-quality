"""Check the map's hover layer and its empty-filter result."""

from html import unescape
from pathlib import Path
import unittest

from analysis.data_types import BOUNDARIES_ADAPTER
from analysis.notebook_map import borough_map


ROOT = Path(__file__).resolve().parents[1]


class NotebookMapTests(unittest.TestCase):
    def test_borough_tooltips_remain_accessible_under_points(self) -> None:
        geometry = BOUNDARIES_ADAPTER.validate_json(
            (ROOT / "data/reference/nyc_borough_boundaries.geojson").read_bytes(), strict=True,
        )
        markup = unescape(borough_map(
            geometry, [(40.75, -73.98)], state="Limpio", omitted=2,
        ).data)
        for feature in geometry["features"]:
            self.assertIn(feature["properties"]["boroname"], markup)
        self.assertIn("bindTooltip", markup)
        self.assertIn('"interactive": false', markup)
        self.assertIn("2 registros fuera del mapa", markup)

    def test_empty_selection_keeps_the_borough_reference(self) -> None:
        geometry = BOUNDARIES_ADAPTER.validate_json(
            (ROOT / "data/reference/nyc_borough_boundaries.geojson").read_bytes(), strict=True,
        )
        markup = borough_map(geometry, [], state="Original", omitted=0).data
        self.assertIn("Original · 0 ubicaciones muestreadas", markup)
        self.assertIn("bindTooltip", markup)
        self.assertNotIn("circleMarkerOptions", markup)


if __name__ == "__main__":
    _ = unittest.main()

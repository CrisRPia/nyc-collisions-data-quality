"""Shared domain rules and record-level alerts for the 2025 NYC dataset."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
from math import cos, isfinite, radians
import re
from typing import Annotated, Literal, TypedDict

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError

from analysis.data_types import LOCATION_ADAPTER, BoroughBoundaries

COUNT_FIELDS = [
    "number_of_persons_injured", "number_of_persons_killed",
    "number_of_pedestrians_injured", "number_of_pedestrians_killed",
    "number_of_cyclist_injured", "number_of_cyclist_killed",
    "number_of_motorist_injured", "number_of_motorist_killed",
]
VEHICLE_FIELDS = [
    "vehicle_type_code1", "vehicle_type_code2", "vehicle_type_code_3",
    "vehicle_type_code_4", "vehicle_type_code_5",
]
type Borough = Literal["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"}
GEOGRAPHIC_MARGIN_KM = 5.0

COUNT_ADAPTER = TypeAdapter(Annotated[int, Field(ge=0)])
BOROUGH_ADAPTER = TypeAdapter(Borough)
ZIP_ADAPTER = TypeAdapter(Annotated[str, StringConstraints(pattern=r"^[0-9]{5}$")])
DATE_ADAPTER = TypeAdapter(Annotated[date, Field(ge=date(2025, 1, 1), le=date(2025, 12, 31))])


@dataclass(frozen=True)
class GeographicDomain:
    south: float
    north: float
    west: float
    east: float
    margin_km: float

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.south <= latitude <= self.north and self.west <= longitude <= self.east


def geographic_domain(geometry: BoroughBoundaries, margin_km: float = GEOGRAPHIC_MARGIN_KM) -> GeographicDomain:
    """Expand the official envelope by approximate latitude/longitude distances."""
    if not isfinite(margin_km) or margin_km < 0:
        raise ValueError("The geographic margin must be finite and nonnegative")
    points: list[list[float]] = []
    for feature in geometry["features"]:
        shape = feature["geometry"]
        polygons = shape["coordinates"] if shape["type"] == "MultiPolygon" else [shape["coordinates"]]
        points.extend(point for polygon in polygons for ring in polygon for point in ring)
    south, north = min(p[1] for p in points), max(p[1] for p in points)
    west, east = min(p[0] for p in points), max(p[0] for p in points)
    latitude_margin = margin_km / 111.32
    longitude_margin = margin_km / (111.32 * cos(radians((south + north) / 2)))
    return GeographicDomain(south - latitude_margin, north + latitude_margin,
                            west - longitude_margin, east + longitude_margin, margin_km)


def decimal_value(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def count_value(value: object) -> int | None:
    number = decimal_value(value)
    if number is None or number != number.to_integral_value():
        return None
    try:
        return COUNT_ADAPTER.validate_python(int(number), strict=True)
    except ValidationError:
        return None


def coordinate_value(value: object) -> float | None:
    number = decimal_value(value)
    if number is None:
        return None
    result = float(number)
    return result if isfinite(result) else None


def usable_coordinates(row: Mapping[str, object], domain: GeographicDomain) -> bool:
    lat, lon = coordinate_value(row.get("latitude")), coordinate_value(row.get("longitude"))
    return lat is not None and lon is not None and domain.contains(lat, lon)


def local_date(value: object) -> date:
    return datetime.fromisoformat(str(value)).date()


def local_time(value: object) -> time:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(value))
    if match is None:
        raise ValueError("Expected a local clock time")
    return time(int(match[1]), int(match[2]), int(match[3] or 0))


class DomainAlert(TypedDict):
    stage: Literal["raw", "cleaned"]
    source_row: int
    collision_id: str
    rule_id: str
    field: str
    severity: Literal["error", "review"]
    observed_json: str
    expected: str


class GeographicBounds(TypedDict):
    south: float
    north: float
    west: float
    east: float
    margin_km: float


class DomainReport(TypedDict):
    version: str
    geographic_domain: GeographicBounds
    records_before: int
    records_after: int
    raw_alerts: int
    cleaned_alerts: int
    raw_affected_records: int
    cleaned_affected_records: int
    alerts: list[DomainAlert]


DOMAIN_REPORT_ADAPTER = TypeAdapter(DomainReport)
FLAGS = {
    "C01": "id_missing", "C02": "borough_unknown", "C03": "coordinates_unknown", "S01": "coordinates_partial",
    "S02": "location_coordinate_conflict",
    "V01": "crash_date_invalid", "V02": "crash_time_invalid", "V03": "count_invalid",
    "V04": "coordinates_invalid", "V06": "borough_invalid", "V07": "zip_invalid",
    "V08": "coordinates_outside_nyc", "U01": "id_duplicate",
    "S04_injured": "role_subtotal_exceeds_total_injured",
    "S04_killed": "role_subtotal_exceeds_total_killed",
}


def validate_domain(
    rows: Sequence[Mapping[str, object]], domain: GeographicDomain, stage: Literal["raw", "cleaned"],
) -> list[DomainAlert]:
    """Collect all domain violations; rows and supplied values are never modified."""
    alerts: list[DomainAlert] = []
    ids = Counter(str(row.get("collision_id") or "").strip() for row in rows)
    for index, row in enumerate(rows):
        row_id = str(row.get("collision_id") or "").strip()

        def alert(rule: str, field: str, expected: str, severity: Literal["error", "review"] = "error") -> None:
            observed = {key: row.get(key) for key in field.split(",")}
            # JSON has no NaN/Infinity literals; retain their spelling in the alert.
            for key, value in observed.items():
                if isinstance(value, float) and not isfinite(value):
                    observed[key] = str(value)
            alerts.append({"stage": stage, "source_row": index, "collision_id": row_id,
                           "rule_id": rule, "field": field, "severity": severity,
                           "observed_json": json.dumps(observed, ensure_ascii=False, allow_nan=False),
                           "expected": expected})

        if not row_id:
            alert("C01", "collision_id", "ID presente y no vacío")
        elif ids[row_id] > 1:
            alert("U01", "collision_id", "ID único dentro de la copia")
        try:
            _ = DATE_ADAPTER.validate_python(local_date(row.get("crash_date")), strict=True)
        except ValueError:
            alert("V01", "crash_date", "Fecha válida entre 2025-01-01 y 2025-12-31")
        try:
            _ = local_time(row.get("crash_time"))
        except ValueError:
            alert("V02", "crash_time", "Hora local entre 00:00:00 y 23:59:59")
        counts = {field: count_value(row.get(field)) for field in COUNT_FIELDS}
        for field, value in counts.items():
            if value is None:
                alert("V03", field, "Conteo presente, entero, finito y mayor o igual a cero")
        for field, adapter in (("borough", BOROUGH_ADAPTER), ("zip_code", ZIP_ADAPTER)):
            value = row.get(field)
            if value is not None and str(value).strip():
                try:
                    _ = adapter.validate_python(value, strict=True)
                except ValidationError:
                    alert("V06" if field == "borough" else "V07", field,
                          "Uno de los cinco distritos" if field == "borough" else "Texto de cinco dígitos")
            elif field == "borough":
                alert("C02", "borough", "Distrito disponible para la agrupación geográfica", "review")
        present = [row.get(field) is not None and bool(str(row.get(field)).strip())
                   for field in ("latitude", "longitude")]
        lat, lon = coordinate_value(row.get("latitude")), coordinate_value(row.get("longitude"))
        if not any(present):
            alert("C03", "latitude,longitude", "Par de coordenadas para ubicar el siniestro", "review")
        elif not all(present):
            alert("S01", "latitude,longitude", "Latitud y longitud informadas conjuntamente")
        elif lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            alert("V04", "latitude,longitude", "Par numérico finito dentro de los rangos globales")
        elif not domain.contains(lat, lon):
            alert("V08", "latitude,longitude",
                  f"NYC + {domain.margin_km:g} km: latitud [{domain.south:.6f}, {domain.north:.6f}], "
                  + f"longitud [{domain.west:.6f}, {domain.east:.6f}]")
        if row.get("location") is not None:
            try:
                location = LOCATION_ADAPTER.validate_python(row["location"], strict=True)
            except ValidationError:
                alert("S02", "location", "Objeto de ubicación con coordenadas interpretables", "review")
            else:
                copied_lat, copied_lon = coordinate_value(location["latitude"]), coordinate_value(location["longitude"])
                if copied_lat != lat or copied_lon != lon:
                    alert("S02", "location,latitude,longitude", "Coordenadas coincidentes entre ambas representaciones")
        for outcome in ("injured", "killed"):
            total = counts[f"number_of_persons_{outcome}"]
            for role in ("pedestrians", "cyclist", "motorist"):
                field = f"number_of_{role}_{outcome}"
                part = counts[field]
                if total is not None and part is not None and part > total:
                    alert(f"S04_{outcome}", field, "Subtotal por rol menor o igual al total de personas")
    return alerts


def domain_report(raw: Sequence[Mapping[str, object]], cleaned: Sequence[Mapping[str, object]],
                  domain: GeographicDomain) -> DomainReport:
    before, after = validate_domain(raw, domain, "raw"), validate_domain(cleaned, domain, "cleaned")
    bounds: GeographicBounds = {"south": domain.south, "north": domain.north,
                                "west": domain.west, "east": domain.east, "margin_km": domain.margin_km}
    return {"version": "nyc-2025-v1", "geographic_domain": bounds,
            "records_before": len(raw), "records_after": len(cleaned),
            "raw_alerts": len(before), "cleaned_alerts": len(after),
            "raw_affected_records": len({a["source_row"] for a in before}),
            "cleaned_affected_records": len({a["source_row"] for a in after}), "alerts": before + after}

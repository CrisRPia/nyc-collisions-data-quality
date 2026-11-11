"""Schemas for the pinned source snapshots and generated assessment tables."""

from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field, StringConstraints, TypeAdapter, with_config


type RawRow = dict[str, str | dict[str, str] | None]
type CleanRow = dict[str, str | int | float | list[str] | dict[str, str] | None]
type NonnegativeInt = Annotated[int, Field(ge=0)]
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


@with_config(ConfigDict(extra="forbid"))
class RequestRecord(TypedDict):
    url: str
    observed_at_utc: str
    sha256: Sha256
    bytes: NonnegativeInt


@with_config(ConfigDict(extra="forbid"))
class DownloadAcquisition(TypedDict):
    mode: Literal["download"]
    scope: str
    records: NonnegativeInt
    api_count_before: NonnegativeInt
    api_count_after: NonnegativeInt
    source_rows_updated_at: NonnegativeInt
    page_size: Annotated[int, Field(gt=0)]
    requests: list[RequestRecord]
    raw_representation: str
    consistency_limit: str


@with_config(ConfigDict(extra="forbid"))
class OriginalAcquisition(TypedDict):
    mode: Literal["original"]
    note: str


type Acquisition = Annotated[DownloadAcquisition | OriginalAcquisition, Field(discriminator="mode")]


@with_config(ConfigDict(extra="forbid"))
class SourceLocation(TypedDict):
    latitude: str
    longitude: str
    human_address: NotRequired[str]


@with_config(ConfigDict(extra="forbid"))
class HumanAddress(TypedDict, total=False):
    address: str
    city: str
    state: str
    zip: str


class LocationCoordinates(TypedDict, total=False):
    latitude: str
    longitude: str


class MetadataColumn(TypedDict):
    fieldName: str
    dataTypeName: str


class Metadata(TypedDict):
    columns: list[MetadataColumn]
    rowsUpdatedAt: int


class SourceSnapshot(TypedDict):
    file: str
    url: str
    observed_at_utc: str
    sha256: str


class PolygonGeometry(TypedDict):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]


class MultiPolygonGeometry(TypedDict):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[list[float]]]]


class BoroughProperties(TypedDict):
    boroname: str


class BoroughFeature(TypedDict):
    properties: BoroughProperties
    geometry: PolygonGeometry | MultiPolygonGeometry


class BoroughBoundaries(TypedDict):
    features: list[BoroughFeature]


class SpatialStats(TypedDict):
    reference_features: int
    boundary_exclusion_degrees: float
    eligible_known_borough_records: int
    agreeing_records: int
    disagreeing_records: int
    agreement_pct: float | None
    missing_borough_recovery_candidates: int
    missing_borough_remaining_if_candidates_accepted: int
    outside_reference_envelope_flags: int
    excluded_or_unmatched_records: int
    limits: str


class QualityMetric(TypedDict):
    rule_id: str
    dimension: str
    rule: str
    unit: str
    eligible: int | None
    conforming: int | None
    exceptions: int | None
    conforming_pct: float | None
    interpretation: str
    note: str
    dmbok2_reference: str


@with_config(ConfigDict(extra="forbid"))
class Comparison(TypedDict):
    rule_id: str
    dimension: str
    rule: str
    unit: str
    before_eligible: NonnegativeInt | None
    before_exceptions: NonnegativeInt | None
    before_conforming_pct: float | None
    after_eligible: NonnegativeInt | None
    after_exceptions: NonnegativeInt | None
    after_conforming_pct: float | None
    change_percentage_points: float | None
    interpretation: str
    note: str
    dmbok2_reference: str


@with_config(ConfigDict(extra="forbid"))
class ScorecardSummary(TypedDict):
    raw_sha256: Sha256
    cleaned_sha256: Sha256
    reference_sha256: Sha256
    records_before: NonnegativeInt
    records_after: NonnegativeInt
    before: list[QualityMetric]
    after: list[QualityMetric]
    comparison: list[Comparison]
    method: str


class FieldProfile(TypedDict):
    field: str
    published_type: str
    role_for_this_analysis: str
    records: int
    populated: int
    missing: int
    populated_pct: float | None
    distinct_present: int
    outer_whitespace_cells: int
    json_types: str


class SourceFreshness(TypedDict):
    source_update_utc: str
    metadata_observed_utc: str
    source_age_hours_at_metadata_observation: float
    advertised_update_frequency: str
    interpretation: str


class AssessmentSummary(TypedDict):
    generated_at_utc: str
    purpose: str
    purpose_status: str
    records: int
    published_fields: int
    raw_sha256: str
    date_min: str
    date_max: str
    observed_calendar_days: int
    expected_calendar_days: int
    core_fields: list[str]
    book_references: dict[str, str]
    book: dict[str, str | int]
    spatial: SpatialStats
    source_freshness: SourceFreshness
    representation: dict[str, str | int]
    duplicate_event_candidates: dict[str, str | int]
    metrics: list[QualityMetric]
    limitations: list[str]


class CleanupSummary(TypedDict):
    generated_at_utc: str
    raw_sha256: str
    cleaned_json_sha256: str
    records_before: int
    records_after: int
    records_deleted: int
    output_fields: int
    operations: dict[str, int]
    borough_missing_before: int
    borough_missing_after: int
    coordinate_pairs_missing_before: int
    coordinate_pairs_missing_after: int
    duplicate_groups_before: int
    duplicate_groups_after_normalization: int
    duplicate_candidate_records_retained: int
    review_flags: dict[str, int]
    reference_sha256: str
    spatial_rule: str
    audit_scope: str


# Validate source representation strictly; cleaning conversions happen explicitly
# in cleanup.py. Create adapters once, rather than for each of the 85,559 rows.
RAW_ROWS_ADAPTER = TypeAdapter(list[RawRow])
CLEAN_ROWS_ADAPTER = TypeAdapter(list[CleanRow])
ASSESSMENT_ADAPTER = TypeAdapter(AssessmentSummary)
CLEANUP_SUMMARY_ADAPTER = TypeAdapter(CleanupSummary)
METADATA_ADAPTER = TypeAdapter(Metadata)
BOUNDARIES_ADAPTER = TypeAdapter(BoroughBoundaries)
MANIFEST_ADAPTER = TypeAdapter(list[SourceSnapshot])
ACQUISITION_ADAPTER = TypeAdapter(Acquisition)
SCORECARD_ADAPTER = TypeAdapter(ScorecardSummary)
LOCATION_ADAPTER = TypeAdapter(SourceLocation)
LOCATION_COORDINATES_ADAPTER = TypeAdapter(LocationCoordinates)
HUMAN_ADDRESS_ADAPTER = TypeAdapter(HumanAddress)
STRING_LIST_ADAPTER = TypeAdapter(list[str])

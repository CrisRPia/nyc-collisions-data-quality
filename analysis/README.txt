Reproducing the DMBOK2 quality assessment

From the project root:
    uv run analysis/analyze_quality.py

The project declares its Python requirement, NumPy, matplotlib and Pydantic dependencies in pyproject.toml. uv.lock pins the resolved versions, and uv creates the project environment automatically. Run uv sync --locked to install runtime and development dependencies; uv run basedpyright checks the project. Source JSON is validated with Pydantic TypeAdapters in strict mode; cleaning conversions remain explicit. It reads only the raw 2025 JSON and the versioned metadata/boundary snapshots under data/reference. It makes no network calls and never changes the raw JSON or CSV. Inputs are checked against SHA-256 values in data/reference/assessment_sources.json.

Outputs:
- reports/data_quality_assessment.html: full explanation, DMBOK2 page references, rule results and recommendations.
- results/quality_metrics.json: detailed machine-readable findings, formulas/denominators, assumptions and limits.
- results/quality_scorecard.csv: baseline rule-level conformance; review signals and unmeasured dimensions are labeled separately.
- results/field_profile.csv: descriptive population/type/cardinality profile for all 29 fields.
- results/quality_issues.csv: overlapping per-rule exceptions and review candidates, keyed by collision_id. Do not sum issue rows to estimate unique faulty crashes.
- results/borough_recovery_candidates.csv: conditional proposed borough derivations; no values have been applied to a cleaned dataset.
- results/vehicle_spelling_candidates.csv: explicit modal-spelling proposal within strip/casefold groups across all five vehicle fields.
- results/duplicate_event_candidates.csv: all records in groups identical on the 28 non-ID fields; candidates for source review, not confirmed duplicate crashes.

Assessment framing:
Use case: annual 2025 borough-level road-safety analysis. The six dimensions follow the DAMA UK six discussed in DMBOK2 Chapter 13, printed p. 460, and required by the assignment. DMBOK2 discusses other dimensions as well. PDF page numbering in the supplied English book is printed page + 4.

No composite score, universal business threshold, independent real-world accuracy claim, achieved after-score, or operational latency SLA is inferred. Optional fields are not all required. Source-level freshness is observed at metadata acquisition; repeated downloads are necessary to assess cadence and revisions.

The full report links every important distinction to Chapter 13, especially printed pp. 457, 460-463, 467-468, 476-486.

Phase 3: cleanup

Run from the project root:
    uv run cleanup.py

The cleanup script has eight numbered steps. The spatial lookup is reused from the assessment; the other rules are direct transformations. It checks the recorded input hashes. After uv has installed the project dependencies, the analysis and cleanup themselves run without network access.

Outputs:
- data/cleaned/nyc_collisions_2025.json: valid counts are JSON integers and finite coordinates are numbers; invalid scalar values retain their source representation, missing values are explicit nulls, and dates/times use ISO strings.
- data/cleaned/nyc_collisions_2025.csv: the same table for browsing. CSV does not store native data types; its quality_flags list is joined with semicolons.
- results/cleanup_changes.csv: substantive coordinate, vehicle-spelling and borough changes by collision_id. Type/format conversions and redundant-column removal have aggregate counters in the summary.
- results/cleanup_review.csv: unresolved locations, spatial conflicts, potential duplicate events and person/role total differences.
- results/cleanup_duplicate_candidates.csv: candidate groups after normalization. Every record is retained.
- results/cleanup_summary.json: counts, provenance hashes, derivation assumptions and a small before/after summary.

The cleaned table adds borough_origin, coordinate_status and quality_flags. Boroughs labeled derived_from_coordinates assume the source GPS is correct; supplied boroughs are never overwritten. The inherited lookup excludes an approximate boundary band in degrees, not a guaranteed metric buffer.

The script keeps every crash, preserves person totals, leaves ambiguous abbreviations unchanged, and does not automatically delete possible duplicates. Coordinates outside the NYC domain, including zero pairs, remain in the output with flags. Invalid scalar values are also retained with domain alerts. The original data/raw files and the phase-1/2 assessment outputs remain unchanged.

Repeated runs read the same raw snapshot and overwrite only the cleanup outputs. Cleaned JSON values are deterministic for the recorded inputs.

Phase 4: before/after scorecard and proposed monitoring

After running the assessment and cleanup:
    uv run --locked scorecard.py

Outputs:
- reports/quality_scorecard_before_after.html: dashboard covering all six dimensions.
- results/quality_scorecard_before_after.csv: before/after eligible counts, exception counts, conformance percentages and percentage-point changes.
- results/quality_scorecard_before_after.json: the two measurements, comparison and input hashes.
- reports/monitoring_policy.txt: proposed acquisition frequency, controls, roles, thresholds, review process and evidence gaps. This is a maintained proposal, not generated by the script or deployed automation.

The scorecard recomputes the baseline rules, including the geographic domain on both datasets and adds a fixed-denominator usable-coordinate coverage rule. It checks that the recomputed before measurements match results/quality_metrics.json. The original results/quality_scorecard.csv remains the baseline-only output.

The spelling map is frozen from the source. Only supplied boroughs are eligible for the polygon-agreement check; derived labels do not validate themselves. The removed location column makes its after-state pairwise agreement check inapplicable. Accuracy and first-publication latency remain unmeasured. No combined quality score is calculated.

Domain validation uses the official NYC envelope plus an approximate configurable 5 km margin. V08 measures this domain and C05 its whole-dataset coverage. Coordinates are retained, so their domain errors remain visible after normalization. Potential duplicate events and person/role-total differences are retained as review signals. The before/after comparison makes denominators explicit rather than counting schema simplification or honest nulls as new real-world information.

Domain alerts:
- results/domain_alerts.csv: stage, source-row index, collision ID, rule, severity, observed JSON and expected domain.
- results/domain_validation.json: rule version, geographic bounds and the full before/after alert report.

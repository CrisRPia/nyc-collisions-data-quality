# DMBOK2 data quality project

Assess and clean the NYC Motor Vehicle Collisions 2025 snapshot using the local
source data and reference files. The raw data stays unchanged.

The main entry point is [data_quality_project.ipynb](data_quality_project.ipynb),
which contains the technical report, full pipeline and interactive exploration.
Start it with:

```sh
uv run --locked jupyter lab data_quality_project.ipynb
```

Choose **Kernel → Restart Kernel and Run All Cells**. The first run downloads
the complete 2025 dataset and reference inputs; later runs reuse a verified cache.
No separate data preparation or CLI run is required. Widgets need a live kernel;
the notebook also saves static tables and figures for readers without one.
An executed HTML copy is available as `data_quality_project.html`; open the
notebook in JupyterLab to use the live filters and record comparison controls.
In the exploration section, each chart, the map and the table have their own
**Usar datos limpios** checkbox. They switch independently between original and
cleaned data while sharing the borough, month, review-flag and search filters.
Month, borough and vehicle charts use fixed scales. Review flags select the same
source rows in both versions.

Notebook settings:

- `SOURCE_MODE = "download"`: acquire current official data, allowing for late amendments.
- `SOURCE_MODE = "original"`: reproduce the project's original archived snapshot.
- `REFRESH_SOURCE = True`: acquire a new timestamped snapshot rather than reusing the default cache.

Notebook inputs, audit files, cleaned datasets and reports are isolated under
`runs/notebook-…/`, which is ignored by Git. They do not overwrite the original
project's `data/`, `results/` or `reports/`. Keep the whole project alongside the
notebook: it imports the shared rules rather than duplicating them.

The downloader retains exact API page responses, checks row counts and source
update metadata before/after pagination, and records request URLs and hashes.
A failed acquisition never publishes a partial workspace. Public API outages or
schema changes surface as errors; original mode is available when archived inputs
are present. The current daily source can amend historical records, so a new
download is not guaranteed to reproduce an old snapshot's hash or measurements.

Install the locked dependencies, including the type checker:

```sh
uv sync --locked
```

Run from this directory:

```sh
uv run --locked analysis/analyze_quality.py
uv run --locked cleanup.py
uv run --locked scorecard.py
uv run --locked basedpyright
uv run --locked python -m unittest discover -s tests -v
```

The cleaned dataset is written to `data/cleaned/nyc_collisions_2025.csv` and
`data/cleaned/nyc_collisions_2025.json`. Audit files and metrics are in `results/`;
the assessment report is in `reports/data_quality_assessment.html`.

The before/after scorecard is `reports/quality_scorecard_before_after.html`,
with CSV and JSON versions in `results/`. The proposed ongoing monitoring process
is documented in `reports/monitoring_policy.txt` (Markdown-formatted text, also
rendered inside the notebook). Run the scorecard after cleanup.

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`.
`.python-version` selects Python 3.14; uv manages the local `.venv`.
See [analysis/README.txt](analysis/README.txt) for the rules, outputs and limits.

To execute the notebook without opening JupyterLab:

```sh
uv run --locked jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=600 data_quality_project.ipynb
```

To export a static HTML copy beside the notebook:

```sh
uv run --locked jupyter nbconvert --to html --HTMLExporter.language_code=es data_quality_project.ipynb
```

Development dependencies include pandas/matplotlib stubs. The small `typings/`
directory describes the ipywidgets and IPython display APIs used by the notebook;
it does not change runtime behavior. The pipeline and notebook are both checked
by basedpyright.

Domain validation uses the official NYC reference envelope with an approximate
5 km margin (`GEOGRAPHIC_MARGIN_KM` in `analysis/domain.py`). Cleanup preserves
out-of-domain coordinates and other invalid scalar values, attaching flags.
`results/domain_alerts.csv` and `results/domain_validation.json` contain the
before/after alerts, observed values and applied bounds. Structural/provenance
failures still stop the run.

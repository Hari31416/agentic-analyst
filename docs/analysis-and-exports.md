# Analysis and portable outputs

Phase 06 adds `analyze_data` and `generate_report` to prompt `analyst-v4`.
API, worker and sandbox run on the host. Calculations execute in the existing
networkless microVM; report assembly reads retained results on the host and
never executes their code.

## Analyst operations

Start with `list_sources`, `inspect_schema` and `dataset_profile`. Flat JSON
arrays and scalar Parquet columns use the CSV profiling and working-copy path.
Nested values, duplicate JSON keys, nonfinite numbers, unsupported binary
columns and excessive decoded dimensions are rejected. Identifiers such as
`001`, decimal text and dates retain their values. Source originals remain
immutable.

`analyze_data` accepts named input frames. Each input has exactly one selected
`dataset_id` or accessible CSV `artifact_id`. Fetch database rows with `run_sql`
first, then supply the returned CSV artifact. Database connections and credentials
stay outside the guest. Each staged file dataset receives a retained canonical
CSV snapshot, its hash, and a mapping to the original guest path.

Operations execute in order on a frame:

| Operation | Required fields and behavior |
| --- | --- |
| `convert` | `columns`, `data_type` of text/number/integer/date; explicit `date_format` for dates |
| `filter` | One column, explicit `comparison` eq/ne/gt/ge/lt/le and `value`; missing values fail the comparison |
| `missing` | `columns`, policy reject/drop/fill; filling requires an explicit value |
| `duplicates` | Key columns and reject/drop; dropping keeps the first observation |
| `join` | Left key `columns`, `right_frame`, `right_on`, left/inner `how`, declared `relationship` |
| `aggregate` | Group columns, possibly empty; metrics have column/function/output; sum/mean/min/max/count |
| `derive` | Arithmetic add/subtract/multiply/divide, year/month/fiscal_year; output name and operand columns |
| `melt` | Identifier columns and value_columns; units must agree |
| `pivot` | Identifier columns and two value_columns naming category/value; duplicate cells reject |
| `statistics` | describe/correlation/regression/outliers and columns; explicit cleaning before missing observations |

Fiscal years default to April and are labelled by their start year. Joins default
to `many_to_one`; one-to-one/one-to-many/many-to-many are explicit alternatives.
Null keys require cleaning. Leading-zero identifiers remain text. Key type or unit
mismatches require a mapping or conversion. Diagnostics record input/output rows,
key uniqueness, unmatched keys, and multiplicity. An explicit many-to-many join
retains a warning. Neither a matching key name nor a successful merge establishes
that two entities have the same meaning.

Decimal inputs have at most 60 significant digits; exact results have at most
120. Rational verification rejects precision loss, and repeating decimal
division such as 1/3 requires a different stated analysis rather than rounding.
Statistics use NumPy floating-point estimates, record sample size and missing
policies, and support descriptive moments/quartiles, Pearson correlation,
Tukey 1.5-IQR outliers and OLS with an intercept. Rank-deficient regression
rejects. These estimates describe associations; they do not establish causation.
Known units cannot be silently relabelled. Explicit unit declarations require
recorded assumptions.

A frame is limited to 100,000 rows and 1,000,000 cells. At most 16 inputs and 20
operations execute per call. Output retention is capped at 10,000 rows; the
default is 5,000. Calculations read all staged observations. A truncated database
snapshot remains truncated in downstream analysis and is labelled accordingly.
Generated programs are retained as `code.py` before guest execution. There is
no host fallback for model-authored code.

`result.csv` keeps exact scalar strings. `analysis.json` records operations,
units, assumptions, sampling, precision limits, row limits and previews. A
requested chart produces strict `chart.json` and `chart.png`; chart coordinates
use floating-point approximations and at most 1,000 retained points. Numeric
results retain dataset/source versions, code IDs, snapshot hashes and result hashes
in structured evidence. Later analysis can use the retained CSV or register it
as a derived dataset with its lineage.

## Artifacts, reports and replay

The Outputs view lists retained tables/charts/reports, offers format downloads,
and registers a CSV with **Use as dataset**. Select that derived source in a
later run; the current run selection is fixed. Registration verifies workspace,
source versions/states, dataset/artifact lineage and hashes. It is idempotent.
Deleted or changed original lineage rejects registration.

For artifact API bounds, the chart allowlist, formula protection, report fonts,
notebook snapshots and replay paths, read [reports and analytical artifacts](reports-and-artifacts.md).
The frontend renders validated chart specs as React SVG and Markdown as plain
text. PDF preview uses an iframe with an empty sandbox policy. Arbitrary HTML
remains a download. Full browser QA is deferred by the user.

## Portable archives

`GET /api/workspaces/{id}/export` downloads a ZIP. `POST /api/portability/import`
accepts multipart `file` and creates a new workspace. The response contains a
flat old-to-new `id_map`, `reconnection_required` and `reindex_required` lists.
Supported content includes originals, extraction blocks, versioned chunks,
extractive summary caches, datasets, artifacts, messages, runs and evidence.
Embeddings and active jobs are not replayed. Database descriptors contain no
passwords or encrypted credentials, and imported database sources are disconnected.

Compatibility is explicit: format `agentic-rag-analyst-workspace`, schema 1.
Other format/schema versions reject. Import validates UUIDs, ownership and
references, member hashes/sizes, duplicate names/JSON keys, traversal, symlinks,
special files, encryption and unsupported compression. Archives are bounded at
256 MiB compressed, 512 MiB expanded, 20,000 entries, 128 MiB per member and
compression ratio 200. Metadata has a 64 MiB bound. No archive path is extracted
onto the host filesystem. An unavailable original or artifact is represented
explicitly; saved evidence locations and ID remapping do not point at unrelated
replacement content. Import preserves exact code/notebook bytes, whose embedded
historical IDs are interpreted using the returned ID map.

## Verification and commands

Saved microVM evidence is in `evals/results/phase06-analysis-live.json` and
`phase06-mixed-live.json`. The pinned guest manifest remains
`sha256:693c157fccec858ec5f603b3a590adb9fc9f72b2223abde8f3526f847dbfe609`.
The milestone observed Python 3.12.13, pandas 2.3.0, NumPy 2.4.4, SciPy 1.17.1,
Matplotlib 3.10.8, DuckDB 1.4.4 and PyArrow 23.0.1. These versions are supplied
by the pinned OCI image; host/report dependencies use `backend/uv.lock`.

Run `make live-analysis` for an optional synthetic DB/file/document agent diagnostic.
It writes a timestamped report and does not overwrite the retained milestone.
Broad configured-model trials failed on tool arguments, source routing, or context
limits. A guided follow-up initially omitted an inclusive comparison and returned
the wrong count; filter comparisons are now mandatory. The corrected guided run
produced count 2 and INR 25,000.00. These recorded trials are not a claim of
reliable autonomous task success. Per user instruction, reasoning misses remain
recorded rather than triggering repeated tuning; phase 09 evaluates task success.
An explicit `PHASE06_WORKSPACE_ID` reuses an existing evaluation workspace.
Run the two new microVM tests with `LIVE_SANDBOX_ENABLED=1`; PostgreSQL integration
also requires `TEST_DATABASE_URL`. Reuse saved live results until a material
change or unresolved failure requires another run. Trial failures stay in the
phase report history. Representative exported PDFs require visual inspection.

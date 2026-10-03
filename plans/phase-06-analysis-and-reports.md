# Phase 06: full analysis, reports, and portable outputs

Prerequisite: phase 05. Read codeagent serialization/export and advanced-rag import/export.
Outcome: flexible data analysis produces useful, inspectable, reproducible outputs.

## Build in order

1. Expand data tools for filtering, grouped aggregation, joins, reshaping, missing
   values, duplicates, date/time/fiscal periods, and new derived columns. Retain
   automatic profiles and optional descriptions; avoid mandatory catalog setup.
   Add JSON/Parquet dataset adapters where they improve the same file workflow.
2. Support pandas/numpy and appropriate statistical libraries in the sandbox
   image. Enable descriptive statistics, distribution/outlier analysis, correlation,
   and regression or other requested analyses supported by those libraries.
   Record sampling, missing-value handling, units, assumptions, and limitations.
   Distinguish calculations from interpretations and causal claims.
3. Support multi-file and mixed database/file analysis. Fetch bounded database
   results through connectors, stage them as datasets, and join in the sandbox.
   Check key uniqueness, grain, join multiplicity, row loss, and entity mappings.
   Show unresolved joins/definitions to the user rather than silently guessing.
4. Formalize chart/table artifacts: typed Plotly specs, static images, paginated
   data, downloadable CSV/XLSX/Parquet, artifact manifest, and lineage. Validate
   chart labels/units and communicate sampled/truncated data. Handle nonfinite
   values, decimals, large integers, and dates consistently.
5. Add report generation using calculated results and document evidence. Export
   Markdown, PDF, and a replayable notebook with code, inputs, outputs, and version
   information. Render and visually inspect representative PDF exports. Preserve
   citations and source locations; remove credentials and unsafe executable content.
6. Add safe artifact rendering. Treat generated HTML as untrusted, isolate any
   preview, and prefer validated chart specs over arbitrary JavaScript. Guard
   spreadsheet/CSV exports against formula injection in untrusted text cells.
7. Implement portable workspace/source exports and imports: original files,
   extracted content, versioned custom chunks, supported cached summaries,
   datasets, artifact manifests, and conversation/evidence references. Validate
   archives against traversal, symlinks, expansion limits, schema/version mismatch,
   and hash failures. Exclude secrets and require explicit reconnection for DBs.
8. Add contextual question suggestions from available source metadata/summaries
   only when useful. Build an artifact browser, report preview/export, and simple
   derived-dataset reuse in later conversations. Avoid extra administrative UI.

## Acceptance and validation

- [x] The agent completes representative cleaning, joins, statistics, and chart
  requests with correct results and recorded assumptions.
- [x] Many-to-many joins, missing values, unit mismatch, and duplicate identifiers
  have tests with expected behavior and visible warnings/clarification where needed.
- [x] Derived datasets can be selected in a later run with complete lineage.
- [x] Uploaded files and original DB rows remain unchanged throughout analysis.
- [x] A mixed-source report preserves accurate numeric results and resolvable citations.
- [x] Markdown, notebook, PDF, data downloads, and chart previews work end to end.
- [x] Representative PDF pages are visually checked; notebook inputs/replay
  limitations are explicit for live sources without retained snapshots.
- [x] Malicious HTML/formula cells and archive paths do not execute in the host app.
- [x] Export/import round-trip retains supported content and evidence IDs or an
  explicit remapping; unsupported version fields do not silently corrupt data.
- [x] Source deletion/expiry leaves an explicit unavailable reference, not a
  citation pointing at unrelated replacement content.

Use deterministic numerical cases, artifact contract checks, browser checks,
and real sandbox analysis. Live task success is evaluated again in phase 09.

## Handoff

Document analyst operations, guest image libraries, output schemas, export formats,
import compatibility, chart preview controls, and report/replay limitations.

## 3 October 2026 verification notes

- Deterministic operations and real mixed PostgreSQL/file microVM execution pass.
  A later run selects only the registered derived dataset and retains its ancestry.
- The configured Gemma model failed three broad trials on tool arguments, routing,
  or context limits. A guided follow-up returned an incorrect boundary count when
  it omitted a comparator. Filter comparisons now require an explicit value.
  The corrected guided run completed cleaning, joins, statistics and chart/report
  generation with count 2 and INR 25,000.00. Its final prose reversed the labels
  of two evidence IDs; the retained report and evidence references remain correct.
  This verifies a representative guided workflow, not reliable autonomous success.
- Per user instruction, recorded reasoning misses do not trigger further tuning.
  Phase 09 evaluates autonomous task success. Full browser QA remains deferred;
  frontend build/type checks and backend artifact contracts passed.
- The live one-page report and a bilingual sample were visually inspected. Saved
  downloads, exact input/code snapshots, original hashes and DB rows, derived
  reuse, archive import and explicit evidence-ID remapping were checked.
- Details and retained trials are in `plans/status.md`, `docs/analysis-and-exports.md`,
  `docs/reports-and-artifacts.md`, `evals/reports/phase06-*.json` and `evals/results/`.

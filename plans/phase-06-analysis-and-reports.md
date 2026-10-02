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

- [ ] The agent completes representative cleaning, joins, statistics, and chart
  requests with correct results and recorded assumptions.
- [ ] Many-to-many joins, missing values, unit mismatch, and duplicate identifiers
  have tests with expected behavior and visible warnings/clarification where needed.
- [ ] Derived datasets can be selected in a later run with complete lineage.
- [ ] Uploaded files and original DB rows remain unchanged throughout analysis.
- [ ] A mixed-source report preserves accurate numeric results and resolvable citations.
- [ ] Markdown, notebook, PDF, data downloads, and chart previews work end to end.
- [ ] Representative PDF pages are visually checked; notebook inputs/replay
  limitations are explicit for live sources without retained snapshots.
- [ ] Malicious HTML/formula cells and archive paths do not execute in the host app.
- [ ] Export/import round-trip retains supported content and evidence IDs or an
  explicit remapping; unsupported version fields do not silently corrupt data.
- [ ] Source deletion/expiry leaves an explicit unavailable reference, not a
  citation pointing at unrelated replacement content.

Use deterministic numerical cases, artifact contract checks, browser checks,
and real sandbox analysis. Live task success is evaluated again in phase 09.

## Handoff

Document analyst operations, guest image libraries, output schemas, export formats,
import compatibility, chart preview controls, and report/replay limitations.

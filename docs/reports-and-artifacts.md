# Reports and analytical artifacts

Analytical outputs are immutable artifacts with SHA256 hashes, producing run and
tool references, source/dataset lineage, and report-specific artifact/evidence
lineage. Workspace browsing is bounded to the latest 500 artifacts. The API
provides a manifest, bounded text preview, paginated table rows, a safe chart
specification endpoint, and format-selectable downloads:

- `GET /api/workspaces/{workspace_id}/artifacts`
- `GET /api/artifacts/{artifact_id}`
- `GET /api/artifacts/{artifact_id}/rows?offset=0&limit=100`
- `GET /api/artifacts/{artifact_id}/chart`
- `GET /api/artifacts/{artifact_id}/download?format=original|csv|xlsx|parquet`
- `GET /api/artifacts/{artifact_id}/content` for the existing content endpoint

Table pagination returns cell values as strings so JavaScript clients retain
large integers, decimal values, and dates. Date and time objects use ISO 8601.
Pages contain at most 500 rows and 1 MB; parsing is bounded to 250,000 rows,
256 columns, and 64,000 characters per cell. Whole-file exports above the row
bound fail explicitly. JSON decimal values are parsed without binary float
rounding. Non-finite JSON numbers are rejected; non-finite spreadsheet numbers
become empty cells.

CSV and XLSX downloads escape formula-leading text while preserving negative
numeric values, leading-zero identifiers, decimal strings, and integers too
large for Excel's 15 digit precision. This transformation leaves the stored
artifact unchanged. Responses include the original `X-Artifact-SHA256`, the
delivered-byte `X-Export-SHA256`, and `X-Export-Sanitized` when values were
escaped.

Chart previews accept the version 1 Plotly subset only: bar, scatter, and pie
traces; bounded arrays of finite values; short plain-text labels; simple axes;
and two boolean display settings. Arbitrary keys, HTML-bearing labels,
JavaScript, transforms, frames, and event handlers are rejected. Generated
chart coordinates use float approximations; the retained result table keeps
exact values.

`generate_report` produces Markdown, PDF, and an `.ipynb` from accessible,
same-thread calculated artifacts and evidence tied to the selected source
versions. Result values are copied from retained result artifacts. Reports
include calculation artifact hashes, evidence IDs, citations, source locations,
and source versions. The notebook keeps exact retained sandbox code in code
cells and input artifact hashes and lineage in metadata. Snapshots up to 1 MB
are embedded as base64; larger snapshots remain downloadable artifacts with
their hashes and sandbox guest paths recorded. The original code can refer to
`/workspace/inputs/`; local replay must map those paths to the extracted input
files. The notebook does not run code during generation. Review code before
manual replay in an isolated environment. Live database inputs require a
retained result snapshot or reconnection. Environment credentials are never
copied into reports.

PDF output uses ReportLab and HarfBuzz shaping through `uharfbuzz`. Hindi PDFs
embed Noto Sans Devanagari and use Noto Sans for Latin text. The local macOS
renderer looks for `~/Library/Fonts/NotoSans-Regular.ttf` and
`~/Library/Fonts/NotoSansDevanagari-Regular.ttf`. The backend container installs
`fonts-noto-core` and reads the corresponding files under
`/usr/share/fonts/truetype/noto/`. Without those files, the renderer falls back
to Helvetica, which does not support Hindi glyphs.

## Viewers and answer mentions

Chat output previews and the workspace artifact browser share image and HTML
viewers. Supported images are PNG, JPEG, GIF, WebP, AVIF, BMP and SVG. Images
load through authenticated API requests and temporary object URLs are revoked
when the viewer closes. Image and HTML previews are limited to 5 MB; larger
artifacts remain downloadable.

HTML is fetched as text and displayed in an opaque-origin iframe with
`sandbox="allow-scripts"`. A prepended content security policy blocks external
scripts, styles, images, fonts, fetches, child frames, objects and form submissions.
Inline JavaScript and styles support self-contained interactive plots. CDN-based
Plotly exports must include Plotly locally to work. The sandbox prevents access
to the parent app's cookies/storage and top navigation. It does not constitute a
separate network-isolated runtime for arbitrary JavaScript.

CSV, XLSX and Parquet table outputs have a ten-row chat preview. Open launches
the full artifact viewer with existing bounded table pagination, format downloads,
hash and lineage. Artifact previews in the output list are collapsed initially.
Unsupported formats retain text preview, download and full-viewer access.

Prompt `analyst-v7` gives the model these optional Markdown forms:

```markdown
[Result table](artifact:EXACT-ARTIFACT-UUID)

![Revenue by district](artifact:EXACT-ARTIFACT-UUID)
```

Links open the full viewer; embeds display an inline preview. Each ID must also
appear in `finish_answer.artifact_ids`. Backend validation applies the existing
thread, selected-source and source-version checks to these references. Unknown
or undeclared Markdown references are rejected. The UI only resolves declared
IDs, leaves code examples as code, and supports existing `[artifact:UUID]`
mentions. Unlike Nexus's filename-based previews, these IDs remain unambiguous
when different runs produce files with identical names.

Run `pnpm --dir frontend run test:artifacts` for Markdown rendering contract
checks. Full browser interaction QA and a live model run remain deferred.

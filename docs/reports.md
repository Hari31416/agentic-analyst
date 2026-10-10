# Versioned reports

Open **Reports** in a workspace and select **New report**. Choose saved pins in the order you want them used, add instructions, and choose English or Hindi. The title is optional; a blank title becomes “Report: <first selected pin title>” in the chosen language. You can rename it later. An artifact pin can be the only input. A thread pin expands to its messages; an answer pin includes its question and answer. Overlapping selections deduplicate messages and artifacts.

The API freezes those selections and copies verified artifact bytes before queuing a report job. The host worker asks the configured model for a validated JSON document, then renders its paragraphs, images, charts, and tables with ReportLab. Reports synthesize the selected work; they do not run new searches or analyses. Each generation or revision uses one model call. Report generation uses the same configured model provider and context limits as the chat agent.

The model receives the selected questions and final answers, compact citation excerpts and locations, and an artifact catalog with short references. It does not receive tool calls, retrieval traces, the full reference-alias dictionary, binary images, or base64 payloads. Table previews contain only a few rows; the renderer reads the exact retained table. Chart summaries contain labels and bounded samples rather than full series data. Images are explicitly marked as not visually inspected, so their filenames cannot support claims about their contents. Full retained inputs remain separate from the compact model prompt.

## Versions and references

Each version retains its canonical document, source snapshot, asset hashes, and PDF. A figure or table block identifies an artifact by its original ID; the renderer resolves that ID through the version's frozen asset manifest. Tables read the original values, including decimal strings, rather than asking the model to reproduce them.

**Wording** regeneration sends editable text and accepts patches keyed by existing section and block IDs. The backend applies only title, heading, paragraph, and caption edits to a copy of the saved document. Protected references and table settings never need to be reproduced by the model. **Restructure** sends a compact block outline and accepts an ordering plan; the backend moves the original blocks without rewriting their contents. Every existing block must appear exactly once. Revisions do not resend source evidence or artifact previews. Both modes create a new version. An invalid model response fails visibly and leaves earlier versions available. Initial generation must include every explicitly selected artifact that supports embedding.

Rename changes the library title. It does not rewrite existing PDFs. Delete removes the report and all versions, cancels active generation leases, and queues retained files for cleanup. Removing a pin, chat, or original artifact does not remove report copies. Deleting the workspace removes its reports too.

Reports are personal to the authenticated user within the workspace. Generated report PDFs have explicit report ownership and appear in the report library.

## Languages and layout

The backend bundles Noto Sans and Noto Sans Devanagari regular/bold fonts under the included OFL license. HarfBuzz shaping is enabled. English and Hindi are exposed by `/api/report-languages`; both can contain mixed Latin and Devanagari text. Missing glyphs fail generation rather than silently producing empty boxes. Other languages and RTL scripts are not enabled yet.

PDFs use bounded A4 layouts, page numbers, fitted PNG/JPEG images, and native tables with repeated headers and wrapped cells. Tables show at most 100 rows and 12 columns, with a truncation note and a retained original download. Charts render on the server from a validated Plotly subset: grouped bars and scatter charts with at most 16 categories or points per trace, and single-series pies with at most eight slices. Stacked, overlay, mixed-type, and incompatible-coordinate charts fail instead of silently changing their meaning. The browser preview offers the retained original for charts; the PDF contains the rendered chart.

A report accepts at most 30 pins, 200 messages, 100,000 characters of message text, 200 cited evidence records with at most 100,000 evidence characters, and 30 retained artifacts. Evidence includes the selected answers' declared citations; uncited search results and retrieval traces are excluded. Older answers without citation metadata use their turn's evidence with retrieval traces excluded. Each artifact is limited to 20 MB, with a 50 MB total. Provider context limits may require a smaller selection.

## API and operation

- `GET/POST /api/workspaces/{workspace_id}/reports`
- `GET/PATCH/DELETE /api/reports/{report_id}`
- `POST /api/reports/{report_id}/regenerate`
- `GET /api/reports/{report_id}/versions/{version_id}/download`
- `GET /api/reports/{report_id}/versions/{version_id}/document`
- `GET /api/reports/{report_id}/versions/{version_id}/events`
- `GET /api/reports/{report_id}/versions/{version_id}/assets/{artifact_id}`
- `GET /api/reports/{report_id}/versions/{version_id}/assets/{artifact_id}/table`

List endpoints support bounded `limit` and `offset`. Versions move through `queued`, `generating`, `ready`, or `failed`. Existing durable jobs provide leases, restart recovery, and attempt exhaustion handling. Successful versions are never overwritten. Error codes remain stable; API responses provide readable explanations.

Progress snapshots are persisted with each version and streamed as named SSE `progress` events with IDs. Stages cover input preparation, model composition, validation, PDF rendering, and completion. Reconnecting clients receive the current snapshot rather than a replay of every intermediate stage; `Last-Event-ID` suppresses duplicates. The frontend subscribes to the pending version, closes streams when switching reports, and falls back to polling after stream errors. Progress describes completed stages, not a predicted duration or token count.

Apply migrations through `a74e013cd215` with `make migrate`, then restart the host API and worker. The Vite frontend reloads normally. No new external rendering service or browser printing step is required.

Focused verification covers ownership, CRUD, ordered selection/deduplication, artifact-only inputs, retained bytes after chat deletion, exact table previews, model/reference failures, lease loss, immutable ready versions, multilingual shaping, images, and charts. Tests use fake model responses rather than live provider requests.

# Phase 04: OCR, hierarchy, and robust ingestion

Prerequisite: phase 03. Read advanced-rag extraction, hierarchy, and chunking references.
Outcome: scanned and structurally complex documents become usable, traceable evidence.

## Build in order

1. Add extractor adapters and per-page routing for digital, scanned, and mixed
   PDF content. Use a lightweight digital path and a local OCR path with Hindi
   and English language assets. Add a Docling-style layout/table adapter as an
   optional heavier profile; install model assets explicitly and record versions.
2. Preserve reading order, tables/cells, headings, page/block locations, and OCR
   warnings/confidence where supplied. Separate confidence from verified accuracy.
   Handle rotated/noisy scans, repeated headers/footers, and password/encryption
   errors. A partially processed document must identify missing/uncertain pages.
3. Reconstruct document hierarchy using headings, numbering, available TOC, and
   layout signals. Make chunking strategies selectable by document/profile:
   recursive baseline, structure-aware, parent/child, and optional semantic
   chunking. Store parameters and bounded table representations. Split oversized
   tables with repeated headers and preserved row locations instead of truncating.
4. Extract table candidates as datasets with typed cells and page/cell provenance.
   Provide preview, warnings, and an explicit accept-for-analysis action. Agent
   arithmetic uses accepted tables, not silently trusted OCR text. Retain the
   original document and extraction lineage of every accepted dataset.
5. Add text/Markdown, HTML, and PPTX ingestion. Process embedded images where
   supported, sanitize HTML, preserve useful slide locations, and report format
   limitations. Add content-type checks and bounded parsing for all extractors.
6. Implement bulk upload/job progress, retry of failed stages, extraction reuse,
   deduplication, deletion, and explicit reindexing. Publish a new index generation
   atomically only when ready; preserve references from previous runs according
   to documented source-version lifecycle.
7. Add extraction viewer and accepted-table controls to the UI. Extend synthetic
   fixtures with rendered Hindi/English scans, mixed PDFs, columns, wide tables,
   malformed files, and low-quality pages with reviewed ground truth.
8. Add optional approved website/sitemap ingestion with bounded crawl depth,
   page/byte limits, robots handling, rate limits, URL normalization, deduplication,
   and per-page progress. Validate destinations and redirects against configured
   network policy, including SSRF controls. Ingest HTML through the same versioned
   extraction path. Crawling remains disabled until configured; internet access
   does not authorize arbitrary model-selected destinations.

## Acceptance and validation

- [x] Local OCR retrieves Hindi/English content from scanned and mixed PDFs.
- [x] Page routing avoids OCR where digital extraction is adequate and records
  the extractor actually used for each relevant page.
- [x] OCR CER/WER, name/numeral errors, and table-cell accuracy are measured against
  ground truth; thresholds and any unsupported cases are documented.
- [x] Headings, reading order, parents, and table row references survive chunking.
- [x] Accepted extracted tables support a correct calculation with cell provenance.
- [x] Interrupted/retried ingestion creates no duplicate active chunks or artifacts.
- [x] A failed generation is not advertised as a successfully indexed document.
- [x] Bulk ingestion exposes useful per-file progress and failures.
- [x] Approved crawling respects bounds/robots policy and rejects forbidden hosts,
  redirects, and duplicate URL loops in controlled test fixtures.
- [x] Deletion/reindexing does not leak stale chunks; historical references show
  archived evidence or an explicit removed-source state, not another version.
- [x] Feature-local tests, Compose ingestion checks, and UI/build checks pass.

No hosted OCR provider is required. Keep heavy profiles optional so baseline
ingestion remains usable. Record observed quality; do not claim perfect OCR.

## Handoff

Document format/extractor support, required assets, profile resource use,
chunking configuration, OCR/table review behavior, and version lifecycle.


Verified 2 October 2026. Local extraction/OCR measurements and the accepted-table
microVM result are saved in `evals/reports/phase04-2026-10-02.json`. Recognition
accuracy thresholds were waived by the user; code logic, routing, bounds,
provenance and failure handling are the acceptance focus. Geometry-based local
PDF layout extraction implements the optional table profile without neural model
assets. Scanned table cells and embedded HTML/PPTX image OCR remain explicit
unsupported cases. Full browser QA remains deferred.

The wide digital table retained 15 rows including its header and six columns;
explicit acceptance produced a selectable CSV dataset and a real microVM
calculation returned count 14 and INR 525,000.00. Controlled crawler tests cover
public-address pinning, redirects, robots exclusions/rate delay, URL loops,
sitemaps and configured limits. No live arbitrary website was crawled.

See `docs/ingestion.md` for profile configuration, source lifecycle and current
limitations. Phase 05 remains unstarted and requires a later user request.

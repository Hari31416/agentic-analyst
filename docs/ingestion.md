# Document ingestion

Uploads preserve the original bytes in immutable storage. The worker extracts
blocks, produces bounded chunks, and publishes an index generation under its job
lease. Phase 04 keeps the existing document, chunk, dataset, and evidence tables;
new provenance fields live in their versioned JSON metadata.

## Formats and local processing

| Format | Baseline behavior | Locations and limitations |
| --- | --- | --- |
| PDF | Digital text per page; weak or empty pages use local Tesseract | Page, character range, actual extractor, OCR orientation/confidence and bounded word boxes. Missing pages and unavailable assets are explicit warnings. |
| DOCX | Paragraphs, headings and table rows | Paragraph/table/row/cell anchors. External links remain unfetched. |
| TXT / Markdown | UTF-8 text; Markdown headings | Original line and character ranges, heading path. |
| HTML | Text, headings and table cells | Block/table/row locations. Scripts, styles and embedded active content are omitted; image OCR is unavailable. |
| PPTX | Slide text, titles and table rows | Slide/shape/paragraph/table/cell anchors. Embedded image OCR is unavailable. |

Archive member counts, expanded bytes, compression ratios, paths, active content,
XML declarations and upload types are checked before Office parsing. HTML nodes,
PDF pages, OCR page count, rendered pixel dimensions, extracted text and chunks
have separate limits. Oversized tables fail explicitly instead of silently
publishing truncated cells. Extraction inspection counts serialized metadata
against its response limit as well as text.

`DOCUMENT_MAX_CHUNKS` defaults to 4096 and accepts at most 16384. Documents
exceeding the configured limit fail rather than publishing a truncated index.
Embedding inference uses `EMBEDDING_BATCH_SIZE` batches and checks the job lease
between batches before publishing a generation.

`OCR_ENABLED`, `OCR_LANGUAGES`, and `OCR_TIMEOUT_SECONDS` configure local OCR.
PDF extraction uses ordinary text rather than fixed-width layout padding. Known
legacy Indic font families, including Chanakya, require OCR even when PDF text
extraction returns printable Latin characters. More than 100 required OCR pages
fails with an explicit page-budget error; split those reports into chapters.
Unavailable or failed legacy-font OCR fails ingestion instead of indexing the
encoded character stream as evidence. OCR confidence still requires review.
Install Tesseract and its `eng`, `hin`, and `osd` assets separately. The baseline
requires both English and Hindi assets; it never substitutes a hosted provider.
The renderer is pinned by `backend/uv.lock`. OCR subprocesses have a deadline,
and renderer pages, bitmaps and images close even on failures. Tesseract OSD
supplies orientation correction when available. Mean word confidence is a
recognizer signal, not verified accuracy. Low confidence and incomplete pages
require review. See the [Tesseract command reference](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)
and [PDFium rendering API](https://pypdfium2.readthedocs.io/en/stable/python_api.html).

`INGESTION_PROFILE=layout` adds local pdfplumber geometry/table extraction to the
same PDF page routing. It provides digital table cells and bounding boxes without
model assets. This is the optional layout adapter for this phase; a neural
Docling adapter is not installed. The default profile is `baseline`.
Scanned table cells are not reconstructed automatically. Their OCR text remains
readable evidence, with a warning that cells are unverified.

## Chunking and table review

Uploads accept `chunk_strategy` as `structure`, `recursive`, or `parent_child`.
Structure groups narrative blocks under headings and retains table row anchors.
Recursive keeps block boundaries before bounded token windows. Parent-child
uses 192-token children, or a 240-byte fallback, with section links to the first
child chunk. It avoids duplicate full-section parent text in the index.
The effective bounds and tokenizer checksum are recorded per document. Semantic
chunking remains explicitly unavailable. PDF outlines and numbered lines supply
best-effort hierarchy; DOCX, Markdown, HTML and PPTX preserve native headings.

The extraction viewer lists table candidates and their typed preview, warnings
and cell provenance. Accepting a table creates a separate CSV source and derived
dataset; it preserves the original document, block references, raw values,
page/slide/cell locations and extraction warnings. Acceptance is idempotent.
Decimal values retain exact strings; identifier columns and leading zeros remain
text. Ambiguous number grouping remains text with a warning. Accepted datasets
are selected and queried through the existing structured-analysis tools. The
agent must request acceptance before arithmetic over extracted document cells.

## Jobs and source versions

Multiple uploads expose independent byte progress, ingestion status and errors.
Identical content with the same extractor, chunker and strategy reuses a source
within its workspace. Failed or OCR-needed jobs can be retried; an active retry
returns the existing job. Once extraction and chunks have been published, retry
reuses their IDs and resumes indexing. Reindexing uses retained extraction,
atomically switches the active generation on publication, and reports degraded
embedding availability instead of invented vectors.

Removing a document uses the [source lifecycle policy](resource-lifecycle.md).
Unused sources are purged and their stored bytes are queued for deletion.
Sources referenced by saved runs or derived datasets are archived, retaining
original bytes, blocks, generations and evidence for historical citations.
The citation API and viewer report `archived` for those retained sources.
Queued document jobs are cancelled; running jobs or runs using the source block
deletion with 409. Both forms remove the source from new selections and retrieval.
Uploading the same bytes after removal creates a new source and never retargets
an old citation. Old ready documents retain their previous extractor and chunker
versions until a separate new upload is processed.

## Approved website imports

Crawling is disabled by default. Set `CRAWL_ENABLED=true` and configure exact
`CRAWL_APPROVED_HOSTS` before using the optional website form. Imports have page,
byte, depth, redirect, queue and overall-time bounds, a fixed rate delay, robots
checks, normalized URL deduplication and per-page progress. A sitemap import
accepts a bounded XML URL set; recursive sitemap indexes are unsupported.
Robots fetch failures fail closed.

Each destination and redirect must be approved and resolve only to public
addresses. Connections use a vetted IP with the original Host header and TLS
SNI; proxy environment variables are ignored. Robots rules also apply to
redirect destinations. Credentials and nonstandard ports are rejected. HTML
bytes enter the same immutable upload/extraction pipeline, with the original URL
stored as lineage. Controlled tests cover forbidden DNS answers, redirects,
robots exclusions and loops. No arbitrary live website was crawled during
verification.

## Verification

`make ocr-fixtures` rebuilds the synthetic v2 scans, mixed/rotated/noisy PDFs and
wide digital table. `make live-ingestion` records local extraction/OCR metrics
without a chat-model or sandbox call. Saved measurements are in
`evals/reports/phase04-2026-10-02.json`. Recognition quality is descriptive, per
user instruction; validation prioritizes routing, bounds, provenance and failure
handling. The noisy Hindi fixture retains recognition misses for later model
comparisons. Full browser QA remains deferred.

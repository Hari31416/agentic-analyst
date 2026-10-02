# Phase 03: document RAG and the first mixed-source milestone

Prerequisite: phase 02. Read advanced-rag query/chunking and Nexus retrieval references.
Outcome: cited Hindi/English answers using PDF/DOCX and structured sources together.

## Build in order

1. Implement PDF/DOCX upload, extraction jobs, document states, and progress.
   Extract digital PDF text with page references and DOCX headings/paragraphs/
   tables with stable anchors. Preserve originals, extracted blocks, versions,
   Unicode, language/script metadata, and extraction warnings. Until phase 04,
   scanned/unsupported content must report OCR needed rather than index empty text.
2. Implement structure-aware baseline chunking with token bounds, overlap,
   heading context, parent/neighbor IDs, and table preservation. Keep extracted
   blocks and chunks separate so later chunking changes can reuse extraction.
3. Add document/chunk/index migrations. Implement a configurable local multilingual
   embedding adapter, batches, dimension/model validation, and versioned index
   generations. Select and record a viable model using Hindi/English fixture
   retrieval; exact dimensions must match its actual output. Missing embeddings
   produce an explicit unavailable/degraded mode rather than fake vectors.
4. Add PostgreSQL lexical search using language-aware configuration. Test English
   stemming and Hindi tokenization/normalization, including combining characters.
   Keep raw text and normalized indexed text separate. Use exact vector search
   as a correctness baseline, optional HNSW, and reciprocal rank fusion over
   separate candidate lists. Scope queries to selected documents/generations.
5. Add document search and source-passage tools returning evidence IDs, original
   excerpts, location, score/rank metadata, and bounded context. Expand neighbors
   without repeated content or overflowing the context budget. Search should
   allow the agent to reformulate a query and inspect a full selected passage.
6. Integrate evidence into final answers and history. Validate references, render
   citations in a source viewer, and preserve locations through exports. An
   unsupported question should return uncertainty or seek clarification.
7. Build document list/status, upload progress, extracted-text inspection, source
   selection, citation previews, and a basic per-stage retrieval trace.
8. Run the mixed-source fixture: retrieve eligibility criteria from English/Hindi
   PDF/DOCX, apply them to CSV/Excel or either database, produce count 2 and grant
   total INR 25,000, and explain exclusions with document and calculation evidence.

## Acceptance and validation

- [x] PDF and DOCX are indexed with useful, accurate locations and original text.
- [x] Hindi/English query-document combinations retrieve the labeled supporting
  passages; record recall@k and misses on the fixture set.
- [x] Dense, lexical, and hybrid modes work independently and expose actual mode.
- [x] Source selection applies to every candidate and context expansion path.
- [x] A live mixed-source run produces the correct numeric results, citations,
  result table/chart, stored tool trace, and audit references.
- [x] Citation lookup and reopened history resolve the same document version.
  Citation UI is implemented and build-checked; manual clicks/full UI QA are
  deferred at the user's request.
- [x] Repeated ingestion is idempotent; changed model/dimensions create a compatible
  generation rather than mixing incompatible vectors.
- [x] Scanned/empty/failed extraction, unavailable embeddings, and no-evidence
  questions produce explicit outcomes.
- [x] Original uploads and database rows are unchanged after the complete workflow.

Use deterministic retrieval/evidence tests plus a live model/sandbox mixed-source
case. Save the phase's baseline measurements for phase 05 comparison. This is
the first combined product milestone, not completion of the entire application.

## Handoff

Record extractor/chunker/model/index versions, supported retrieval modes,
baseline measurements, source-viewer behavior, and known Hindi tokenization gaps.

Verified 2 October 2026. Reports: `evals/reports/phase03-2026-10-02.json` and
`evals/reports/phase03-retrieval-2026-10-02.json`. Retrieval misses are retained
as the baseline for phase 05. Current scope ends after this phase is committed.

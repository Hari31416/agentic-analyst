# Implementation status

Phase 00 is verified, including Compose startup, RustFS, and restart persistence.
Phase 01 backend gates are verified against the configured model and real microVM.
API, worker, frontend, and sandbox now run directly on the host; Docker is used
only for infrastructure. Full UI verification is deferred at the user's request.

Allowed statuses: not started, in progress, implemented / integration pending,
verified. A verified phase has evidence for every required acceptance gate.

| Phase | Status      | Verification evidence | Remaining gate   |
| ----- | ----------- | --------------------- | ---------------- |
| 00    | Verified | 20 deterministic tests, 6 PostgreSQL/RustFS integration tests, 5 Linux fixture tests, healthy Compose, migration/schema and restart checks | None |
| 01    | Verified | 57 deterministic tests, 14 PostgreSQL/RustFS tests, 2 live provider cases, 3 live microVM cases, bilingual full-agent/follow-up/cancellation and app restart checks | Full UI QA deferred by user |
| 02    | Verified | Five live source cases; real DuckDB, PostgreSQL/MySQL safety/deadline/cancellation and RustFS checks | Full UI QA deferred by user |
| 03    | Verified | PDF/DOCX ingestion, measured bilingual retrieval, two live mixed-source cases, citation history, CSV/PNG and source integrity checks | Full UI QA deferred by user; ranking improvements in phase 05 |
| 04    | Verified | Local Hindi/English scanned/mixed/rotated extraction; accepted PDF table calculated in real microVM; lifecycle/crawler/API tests, PostgreSQL/RustFS and frontend checks | OCR accuracy descriptive; full UI QA deferred; explicit format limitations recorded |
| 05    | Verified | 179 deterministic tests; 34 PostgreSQL/RustFS integration tests; 32 local stage trials; four bilingual live dependent-hop/summary cases; historical artifact follow-up; frontend and schema checks | Full UI QA deferred; one follow-up language miss recorded |
| 06    | Verified | 218 deterministic tests; 42 PostgreSQL/RustFS tests; two real microVM analysis checks; guided mixed-source report, downloads, derived reuse and archive roundtrip; PDF visual and frontend/schema checks | Broad LLM reasoning misses recorded; autonomous task success in phase 09; full UI QA deferred |
| 07    | Verified | 240 deterministic tests; 42 PostgreSQL/RustFS checks; eight local retrieval comparisons; real Hindi/English STT and no-execution HTTP checks; three bilingual document/SQL/Python runs; frontend/schema checks | First-pass Hindi STT and glossary quality limits recorded; translation/TTS unavailable; native-speaker/full UI QA deferred |
| 08    | Verified | 268 deterministic tests; 56 PostgreSQL/MySQL/RustFS checks; real sandbox negative probes and cleanup retest; bounded injection trial; retained-run audit/HTTP export; frontend/schema checks | Effective resource stress/workspace quota enforcement unverified; full UI QA deferred |
| 09    | Implemented / integration pending | Core schema/API runner/checkpoints/metrics/reports/review; 287 deterministic and 10 focused integration checks; 12 live trials retained and rescored | Structured unit review and two clarification-contract misses; optional judges, calibration, broad ablations/model comparisons and production thresholds deferred |
| 10    | Not started | Not run                     | All phase checks |

## Inputs for live verification

- Main model: user-provided `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`.
- Sandbox: existing Mac-compatible Nexus-style service, pinned revision/image,
  configured URL/token, and a successful execution readiness probe.
- Optional voice/translation providers: explicit model assets or approved
  provider configuration. Record per-capability availability.

These inputs do not prevent building adapters, fixtures, UI, or deterministic
tests. Record missing live checks precisely rather than marking them passed.

## Phase handoff entries

When implementing a phase, append a dated entry containing:

1. Completed behaviors and relevant files.
2. Commands executed, pass/fail results, and report/artifact references.
3. Live services/model/revisions used, or reasons checks were skipped.
4. Migrations and configuration added.
5. Remaining failures or decisions and the next safe implementation step.

Keep secrets and raw client data out of this file.

## 2 October 2026: phase 00 foundation

- Added FastAPI/Pydantic contracts, SQLAlchemy records, migration
  `f5078b82e3e3`, immutable filesystem and S3 adapters, redacted audit writes,
  atomic per-run event sequencing, and PostgreSQL leased jobs with a real
  artifact verification task. No Python executes in the API process.
- Added the React/Vite workspace shell, workspace/thread APIs, component
  readiness, uv/pnpm lockfiles, Compose services and development commands.
- User selected RustFS as default blob storage. The Compose stack uses a pinned
  RustFS image, an initialized S3 bucket, and separate original/derived prefixes.
  This brings the S3 adapter forward from phase 10. Filesystem remains an
  explicitly selected development option. PostgreSQL holds metadata only.
- Added deterministic CSV/XLSX, MySQL/PostgreSQL seeds, Hindi/English PDF/DOCX,
  expected answers, fixture hashes, and bundled Noto fonts with OFL licenses.
  Repeated fixture generation matches the recorded bytes on macOS.
- Verification so far: Black and mypy pass; pytest deterministic tests 20 passed;
  real PostgreSQL queue tests 5 passed. Those tests cover simultaneous claims,
  expired-owner rejection, retries, concurrent events, and real stored-byte
  maintenance dispatch. Frontend typecheck, build, and formatting pass.
- PostgreSQL uses host port 55432 because an existing host server occupies 5432.
  Compose initial startup exposed an invalid quoted language environment value;
  corrected that configuration. Fresh migration applies successfully.
- Remaining phase 00 gates: final RustFS write protection/read/hash test, full
  stack health and restart persistence, portable Linux fixture regeneration.
  Model credentials are absent and sandbox execution has not yet been probed.


## 2 October 2026: phase 00 verification complete

- Real PostgreSQL/RustFS integration suite: 6 passed. Conditional S3 writes
  reject overwriting original keys; identical writes remain idempotent. Bounded
  reads, byte sizes, and SHA-256 checks pass against the running RustFS service.
- Full Compose build/start succeeds. Database, RustFS, API, and Vite frontend
  health checks pass, and the worker executes a real `verify_storage` job.
  Its retained result reports `verified: true`.
- Applied migration inside the API container; `alembic check` reports no pending
  upgrade operations. Restarted db, RustFS, API, and worker. The UI-created
  workspace, artifact record, completed job, and exact RustFS object hash remain.
- Ran the fixture suite in the Linux API container with read-only mounted evals:
  5 passed, including byte-for-byte regeneration against the macOS-produced pack.
- User supplied the model configuration and authorized live calls. English/Hindi
  tool round trips passed for `gpt-oss-120b` at `cloud.olakrutrim.com`, using the
  configured Chat Completions endpoint and correlated tool IDs. This is a model
  adapter capability check; the complete live agent/sandbox gate remains phase 01.
- Resolved the sandbox analysis image to digest
  `sha256:73520043dc5aa0a30475b8b8a11bf8cc9fea1236540c54b83055d9e876d32fec`.
  The standalone service revision remains
  `b3f032b6a0ce1fab7cebc75250073f58b5b6c63c`.
- Foundation committed as `c694a57`. Phase 01 adds worker-driven agent runs,
  persisted history, SSE replay, microVM execution, and output inspection.


## 2 October 2026: phase 01 agent and sandbox verification

- Added the OpenAI-compatible Chat Completions adapter with bounded responses,
  optional streaming assembly, correlated tool calls, usage, and typed failures.
  The supplied `gpt-oss-120b` endpoint at `cloud.olakrutrim.com` passed real
  English/Hindi tool-call round trips. Compatible endpoints may return the final
  object in message content; that object now receives the same schema and
  reference validation as `finish_answer`. Unknown/malformed calls never execute.
- Added one bounded agent loop, versioned prompt `analyst-v1`, worker-owned runs,
  persisted messages/tool/audit records, idempotent submission, SSE sequence
  replay, cancellation, clarification, and typed partial/failure outcomes.
  The loop bounds model/tool calls, context, elapsed time, and result bytes.
- Added the HTTP client for sandbox revision
  `b3f032b6a0ce1fab7cebc75250073f58b5b6c63c` and analysis image digest
  `sha256:73520043dc5aa0a30475b8b8a11bf8cc9fea1236540c54b83055d9e876d32fec`.
  Python is staged as a file and executed by a fixed launcher in a network-disabled
  microVM. No host execution fallback exists. A synchronous execution POST is
  never blindly replayed after an ambiguous response.
- Exact code and collected outputs are retained with immutable keys, hashes, and
  tool/source lineage. Output transfer uses bounded file byte reads, rather than
  treating the sandbox's export URI as durable downloadable storage. Prior
  artifacts can be staged into a fresh guest on a follow-up turn.
- Session IDs persist before execution. Ownership checks guard durable writes
  and cleanup. Interrupted tools become explicit partial/failure records, and
  abandoned/exhausted jobs terminate their linked runs. Cleanup is recorded
  separately, with failed/unknown cleanup visible instead of an endless spinner.
- Real end-to-end English and Hindi runs each produced `proof.csv` containing
  count 2 and INR 25,000. Both referenced their actual output IDs; bytes and
  hashes remained accessible after guest cleanup. A follow-up in a new guest
  imported the retained CSV and produced average INR 12,500. Active cancellation
  preserved `partial.csv`, stopped/deleted the guest, and confirmed session 404.
  Saved history and hashes survived a host API/worker restart.
- Commands/checks: Black and mypy pass; deterministic tests 57 passed; real
  PostgreSQL/RustFS integration tests 14 passed; live provider tests 2 passed;
  live microVM tests 3 passed, including concurrent isolated sessions and
  cancellation salvage. Guest environment checks confirm model, database, S3,
  and sandbox credentials are absent. Frontend typecheck/build/format pass.
- Synthetic report: `evals/reports/phase01-2026-10-02.json`. Repeat via
  `make live-model`, `make live-sandbox`, and `make live-agent`. Default tests
  exclude live requests unless explicitly enabled. No new migration was needed.
- Per user direction, Compose now contains PostgreSQL/RustFS infrastructure only.
  `make dev` launches host API, worker, and Vite; `make sandbox` starts the pinned
  host service. Root `.env` supplies host S3/model settings. The development
  workflow change is committed as `49e9d83`. Full UI QA is deferred.
- Next phase adds immutable file uploads/profiles, encrypted database connectors,
  dialect-aware read-only SQL, and source tools to this same agent loop.

## 2 October 2026: phase 02 structured analysis

- Added immutable CSV/XLSX/XLS ingestion and bounded profiles with encoding,
  delimiter, type uncertainty, formula-cache warnings, missingness, and explicit
  currency hints. Canonical guest copies preserve leading-zero identifiers.
  Workbook archive/macro checks reject active content; originals never change.
- Added encrypted MySQL/PostgreSQL connections, TLS settings, introspection,
  schema refresh/versioning, and dataset selection. Migration `504d7bc74621`
  stores datasets and encrypted connection metadata. PyMySQL is pinned to 1.1.2
  after a fresh-authentication failure in 1.2.3 against MySQL 8.4.
- Added selected-source/schema/profile/sample/SQL/derived-registration tools.
  File SQL runs DuckDB 1.4.4 in the real microVM with extension auto-loading and
  external access disabled. Database SQL uses an AST policy, selected tables,
  read-only transactions, bounded fetches, and server deadlines. Unknown unsafe
  functions, commands, writes/CTEs, custom casts, and sinks are denied.
- Queries retain exact SQL, code for file SQL, result CSV, input/schema versions,
  hashes and calculation evidence. Output limits apply after full aggregation.
  Python stages only selected canonical files or retained artifacts. Derived
  CSV registration is idempotent and keeps originals and current selection fixed.
- All five source types passed a real configured-model case with eligible count
  2 and INR 25,000. The XLSX answer used Hindi. Exact output references/hash and
  input hashes were checked. Report `evals/reports/phase02-2026-10-02.json`; repeat
  with `python -m app.structured_probes` from the backend directory.
- Backend deterministic, integration, formatting/type checks and frontend
  typecheck/build pass. Real DuckDB tests cover full-input aggregate correctness,
  row truncation, zero rows and external-reader denial. Real PG/MySQL tests cover
  permissive-credential write denial, driver read-only enforcement, serialization,
  duplicate columns, output bounds, deadlines and cancellation.
- Host app workflow remains in place. `make analysis-image` builds/imports a
  pinned guest image; `make eval-infra` starts optional MySQL infrastructure only.
  The local arm64 guest manifest is `sha256:693c157fccec858ec5f603b3a590adb9fc9f72b2223abde8f3526f847dbfe609`.
  Docker was restarted at the user's request; infrastructure volumes persist.
- Full UI QA remains deferred. Next step is phase 03 document extraction, local
  multilingual embeddings, lexical/dense/hybrid retrieval and mixed-source citations.

## 2 October 2026: phase 03 document RAG

- Added leased digital PDF/DOCX ingestion, extraction states/progress, warnings,
  paginated block inspection, upload idempotency, and immutable originals in
  RustFS. Extractor `pdf-docx-text-v1` preserves Unicode and page, paragraph,
  heading, and table locations. Scanned/empty PDFs report `ocr_needed`; OCR
  remains phase 04. Unsupported/oversized extraction produces a terminal error.
- Added `structure-token-v2` chunks with grouped narrative/table blocks, bounded
  headings, actual-tokenizer windows of 384 body tokens and 48-token overlap,
  and a conservative 480-byte fallback. Chunk block IDs and locations describe
  their actual slices. Existing ready document versions remain unchanged.
- Selected local multilingual E5 small, 384 dimensions, quantized ONNX from
  `Xenova/multilingual-e5-small`, revision
  `761b726dd34fb83930e26aab4e9ac3899aa1fa78`. `make embedding-model` downloads
  pinned assets and writes a checksum manifest. FastEmbed 0.7.4 adds the E5
  query/passage prefixes, mean pooling and normalization. Inference uses host
  CPU only, verified local files, and no remote embedding fallback.
- Migrations `6d92b49bc139` and `98657a061bfe` add document/block/chunk/index
  records, pgvector, and partial English/simple lexical GIN indexes. Index
  generations fingerprint source/chunk/model/revision/dimensions. Changed
  revisions/dimensions retain separate generations and prior chunks/vectors.
  Missing assets explicitly degrade to lexical search. Local inference finishes
  before the worker locks its lease for publication; lease loss rejects writes.
- Added lexical, exact cosine, and hybrid reciprocal-rank retrieval, selected
  source-version filtering, bounded neighbor expansion with overlap removal,
  evidence tools, citation resolution and saved history. UI supports document
  upload/status, extraction inspection, source selection, citation previews and
  retrieval traces. Citation lookup/reopened history are HTTP-tested; complete
  browser verification remains deferred by the user.
- Per-document baseline covers both query languages against all four fixtures.
  Dense/hybrid supporting-passage recall at 3/5/10 is 1.00; lexical is 0.25 for
  the recorded long keyword queries. The previous byte-window chunker missed
  both Hindi DOCX queries at rank 3, with the rule at rank 7.
- With all four documents selected, the longer bilingual diagnostic queries
  produce dense/hybrid supporting recall at 3 of 0.50 in each language. English
  recall at 5/10 is 1.00/1.00; Hindi is 0.75/1.00. Lexical AND queries return
  zero candidates when some query words are absent; shorter `income` and `आय`
  queries return candidates. These misses and query strings are saved for
  phase 05. Hindi uses PostgreSQL's simple configuration and Unicode-aware
  normalization, so native Hindi stemming and cross-language lexical matches
  remain limited. The user accepted an imperfect baseline at this stage.
- Two live configured-model runs passed: Hindi PDF plus CSV answered in English,
  and English DOCX plus PostgreSQL answered in Hindi. Both retained eligible
  count 2, INR 25,000, five application-level exclusion rows, a PNG chart,
  document/calculation evidence, tool/audit references, and complete guest
  cleanup. Citation excerpts/locations reopen unchanged. Original file hashes
  and all source database rows remain unchanged. A third question for absent
  scheme Z9 correctly asked for the missing document.
- Reports: `evals/reports/phase03-2026-10-02.json` and
  `evals/reports/phase03-retrieval-2026-10-02.json`. Repeat only when needed via
  `make live-documents`; routine edits use saved live results. No live model
  cases were repeated after the user's request to limit live testing.
- Final checks: 128 deterministic tests, 35 infrastructure integration tests,
  one pinned local-embedding fixture test, Black/mypy, frontend typecheck/build/
  formatting, and `alembic check` pass. Integration fixtures now force their
  tables into isolated schemas while exposing public pgvector types.
- Saved the user's development/testing/commit instructions in `AGENTS.md` before
  this phase's commit. Host API/worker/frontend and infrastructure are running.
  Stop after committing phase 03. Phase 04 and later remain unstarted.


## 2 October 2026: phase 04 ingestion and OCR

- Added local per-page PDF OCR routing with English/Hindi Tesseract assets,
  orientation correction, bounded rendering/subprocesses, actual extractor
  metadata, recognizer confidence/word boxes, and missing/uncertain-page warnings.
  Originals remain immutable. OCR quality is recorded without an accuracy gate,
  per the user's instruction. The noisy Hindi fixture retains numeral/name
  misses instead of prompting recognition-model tuning.
- Added TXT/Markdown/HTML/PPTX ingestion, native heading/slide/table anchors,
  PDF outline/numbering hierarchy, safe archive/XML checks, HTML sanitization,
  and explicit embedded-image limitations. Extractor `document-extract-v2` and
  chunker `structure-token-v3` apply to new uploads; older ready versions remain
  unchanged. Selectable structure/recursive/parent-child strategies record their
  effective token/byte bounds and keep row/parent/neighbor references.
- Added the optional local `layout` profile using pdfplumber 0.11.9, with typed
  digital table candidates and cell bounding boxes. Tesseract 5.5.1, pypdfium2
  5.13.0 and traineddata checksums are recorded in the saved report. This profile
  uses geometry rather than Docling neural assets. Scanned cells and embedded
  HTML/PPTX image OCR are explicitly unavailable; OCR text supports reading.
- Table preview/accept creates an idempotent derived CSV dataset with document,
  block and cell lineage. Exact decimals, leading-zero identifiers, currency
  hints and parsing warnings survive acceptance. Accepted sources are selectable
  through the existing dataset APIs. The agent requests acceptance before using
  extracted cells for arithmetic. A real microVM SQL calculation over the
  accepted wide-table fixture returned count 14 and INR 525,000.00, exported its
  CSV result, and cleaned up the guest session.
- Added multi-file upload progress/failures, failed-stage retry, extraction reuse,
  explicit embedding reindex controls, and archival removal. Retried publication
  keeps block/chunk IDs; removed sources disappear from selection/retrieval while
  saved citations retain their original versions and show an archived notice.
  Job/document lock ordering matches worker publication. No schema migration was
  needed because phase metadata uses the existing versioned JSON contracts.
- Added opt-in approved website/sitemap jobs, per-page status, robots checks and
  rate delay, URL normalization/deduplication, page/depth/byte/time bounds, redirect
  revalidation, and vetted-IP connections with Host/TLS SNI. Public-address-only
  network policy rejects private/mixed DNS answers; environment proxies are
  disabled. Crawling remains disabled until exact hosts are configured. Sitemap
  indexes and unavailable robots policies fail explicitly.
- Verification: 158 deterministic tests, 26 PostgreSQL/RustFS integration tests,
  one real accepted-table microVM test, Black/mypy and frontend formatting/
  typecheck/build pass. Eleven integration checks were skipped: ten unchanged
  database-connector fixtures lacked explicit source test URLs, and one pinned
  embedding fixture was not rerun. Saved phase 02/03 live results remain valid;
  no chat-model calls were repeated. Controlled crawler fixtures use mocked
  transports, without requesting arbitrary public sites. Full browser QA remains
  deferred by the user.
- Saved report `evals/reports/phase04-2026-10-02.json` includes CER/WER, identifier/
  numeral misses, mixed-page routing and table-cell accuracy. The clean/rotated
  English CER is about 0.01, clean Hindi about 0.09, and noisy Hindi about 0.49;
  these are observations, not release thresholds. Wide-table cell accuracy was
  1.00 for this synthetic fixture. Instructions and local configuration are in
  `docs/ingestion.md` and `.env.example`; repeat only when needed with
  `make live-ingestion` and the explicit microVM table test.
- `alembic check` reports no upgrade operations. Host API/worker/frontend were
  refreshed after confirming zero running jobs; infrastructure remains healthy.
- The authorized implementation stops after the phase 04 commits. Phase 05 and
  later remain unstarted.


## 3 October 2026: phase 05 advanced retrieval

- Added `advanced-retrieval-v1` behind the existing document tools, with additive
  conversational rewriting, literal keyword variants, deployment-owned aliases,
  bounded multi-query/independent subquestions, reciprocal-rank fusion, optional
  consensus, document coverage, and parent/neighbor expansion. Original queries
  always execute; invalid variant transformations retain the original. The total
  lexical/dense candidate quota is 60 over at most three queries. Each expanded
  original passage receives its own versioned evidence. Extractive compression
  retains original excerpts and counts both texts against context bounds.
- Added real local FastEmbed cross-encoder inference. The optional int8 Jina
  multilingual assets are pinned to `9cfeff2df7d40d1b78e75e5e9cebec92a99813c9`
  with file hashes. English/Hindi pair scoring ran locally. Missing assets,
  checksum/load failures and invalid scores are explicit unavailable stages,
  with retained fused evidence. Runtime downloads are disabled, and ONNX
  telemetry is explicitly disabled. The public Jina model has CC-BY-NC-4.0 terms;
  production deployments must select suitable licensed assets.
- Added evidence-dependent hops requiring exact discovered terms and earlier
  document evidence from this thread and selected source versions. A synthetic
  Scheme S7 policy identifies NIRVAAN, then a second search finds the bilingual
  definition handbook. Superseded S0 material is excluded from selection.
  Independent questions are labelled separately. The main agent owns the hops;
  no additional production supervisor was introduced.
- Added cached document/section/overview extractive summaries and optional
  heading-based thematic summaries. Migration `a8c204d39f51` creates `summary_cache`.
  Fingerprints include source/version/hash, extraction/chunking/chunk hashes,
  algorithm/prompt/model versions, scope and bounds. Duplicate concurrent inserts
  reuse the winning cache row. Summary evidence contains original sentences,
  including thematic support, and never treats summary prose as primary evidence.
  Default summary cost is zero model calls/tokens. Neural clustering and
  generative map-reduce remain optional alternatives, not enabled implementations.
- Added a separate model-context view preserving all user corrections, selected
  sources/datasets and versions, detected definitions/assumptions/units, and
  evidence/artifact IDs. Durable messages remain unchanged. Compaction emits
  counts and explicitly exhausts the budget if protected content cannot fit.
  Fact detection is heuristic; it cannot identify every possible interpretation.
  Prompt `analyst-v3` explains summaries, independent questions and dependent hops.
- Chat offers Basic/Advanced profiles and collapsed retrieval details. Each run
  captures its profile, pipeline/model settings, aliases and bounds. Citation
  traces remain inspectable, and `/api/runs/{id}/retrieval` exposes empty/failed
  stages even when there are no citation IDs. Expert controls remain tool inputs.
- The same four-document phase 03 corpus ran two local trials for each of eight
  stage settings in both query languages, 32 cases. Basic recall at 3 stays 0.50
  in each language. Document coverage raises English recall at 3 to 0.75. Hindi
  reranking raises recall at 5 from 0.75 to 1.00; English reranking recall at 3 is
  0.75. Multi-query alone leaves recall at 3 unchanged and lowers English recall
  at 5 to 0.75. Warm reranking is about 300 ms versus about 8–11 ms for Basic.
  Basic remains the default; reranking/consensus/compression are opt-in. Detailed
  ranks, misses, timings, text sizes, model versions and process memory are saved
  in `evals/reports/phase05-2026-10-03.json`. These measures do not prove semantic
  support on arbitrary client documents.
- Four live `gpt-oss-120b` cases, two English and two Hindi, completed with real
  dependent hops, reranking, compression, summaries and saved original citations.
  All answered pension included and housing aid excluded. They used 3–6 tools,
  4–7 model calls and 16,108–34,343 total tokens. A fifth live follow-up inspected
  a retained phase 01 `proof.csv` after its original guest expired, returned count
  2 and total 25,000, and cited the durable artifact without creating a sandbox.
  That English-requested follow-up answered in Hindi; this is a recorded language
  adherence miss for phase 07, not a passed language gate. No additional microVM
  executions were necessary; earlier execution/staging evidence remains valid.
- Final validation: deterministic pytest 179 passed; PostgreSQL/RustFS integration
  34 passed, 11 skipped. Ten unchanged seeded connector tests lacked explicit
  source URLs; the old embedding fixture was skipped because the real E5 model
  ran in the local stage comparisons. Black/mypy, frontend typecheck/build/format,
  and `alembic check` pass. New integration checks cover independent/dependent
  hops, cross-document parent rejection, saved citations, historical artifacts,
  section ordinals, and summary content/chunk/prompt/model/extractor invalidation.
  Full browser QA remains deferred. Preflight API startup and test-fixture errors
  were corrected; all live model trials are retained, with no timed-out trials.
- Configuration and commands are in `docs/retrieval.md`, `.env.example`,
  `make reranker-model`, and `make live-retrieval`. Local-only retrieval measurements
  can refresh with `python -m app.advanced_probes --retrieval-only` while preserving
  prior live results. The authorized scope ends at phase 05. Phase 06 awaits the
  user's next request.


## 3 October 2026: phase 06 analysis and portable outputs

- Added typed `analyze_data`: cleaning, filtering, joins with key/grain/multiplicity
  checks, aggregation, reshaping, exact decimal arithmetic, dates/fiscal periods,
  descriptive statistics, correlation, OLS and outliers. Units, assumptions,
  missing policies, sampling and limitations accompany structured evidence.
  Calculations execute only in the networkless microVM. Intermediate expansion,
  empty schemas, decimal precision, null keys and nonfinite values have guards.
  Flat JSON/Parquet use immutable originals and canonical working CSVs. Filter
  comparisons must be explicit; validation feedback exposes field diagnostics
  without submitted values or secrets.
- Added paginated table previews, strict typed chart specs and PNGs, a workspace
  Outputs view, formula-safe CSV/XLSX/Parquet downloads, and idempotent CSV dataset
  registration. Original artifact bytes remain canonical and export hashes identify
  transformed bytes. Both historical `@v1` and current `@1` source lineage markers
  are accepted with strict version checks. Later runs preserve dataset ancestry.
- Reports export Markdown, PDF and notebooks using retained calculations and
  original document evidence. Notebooks retain exact code, snapshots, guest paths,
  hashes and versions, with no automatic execution. PDF exports use shaping-capable
  fonts for Indic text; missing fonts fail explicitly. Validated charts render as
  React SVG; PDF previews use a sandboxed iframe, Markdown is plain text, and HTML
  is download-only. A bilingual sample and the final one-page live report were
  visually inspected with no clipping, overlap or missing glyphs.
- Portable schema-1 ZIP archives retain originals, extracted blocks, versioned
  chunks, supported summary caches, datasets, artifacts, conversation and evidence.
  Bounds, traversal/symlink/encryption/schema/hash and ownership checks reject
  invalid input. Imports remap IDs, including UUID-keyed version dictionaries,
  preserve historical code/notebook bytes, report unavailable objects explicitly,
  and require database reconnection and document reindexing. Credentials, connection
  secrets, embeddings and running jobs are excluded.
- Final validation: deterministic pytest 218 passed (213 before the five new
  version-parser cases); PostgreSQL/RustFS suite 42 passed, 11 skipped. Ten old
  seeded connector checks lack explicit fixture URLs; one old embedding check is
  skipped, with earlier live evidence retained. Black/mypy, frontend typecheck,
  build/format and `alembic check` pass. The two new real microVM checks passed,
  including read-only PostgreSQL/file joins, exact totals, chart artifacts and
  derived reuse in a later run. No schema migration was required.
- The existing guest remains pinned to
  `sha256:693c157fccec858ec5f603b3a590adb9fc9f72b2223abde8f3526f847dbfe609`.
  Observed Python 3.12.13, pandas 2.3.0, NumPy 2.4.4, SciPy 1.17.1,
  Matplotlib 3.10.8, DuckDB 1.4.4 and PyArrow 23.0.1. API, worker, frontend and
  sandbox run on the host; PostgreSQL/RustFS infrastructure runs in Docker.
- Configured `gemma-4-26B-A4B-it` failed three broad agent trials on malformed tool
  arguments, source routing or context limits. A fourth guided run omitted `le`
  and returned count 1 / INR 15,000 rather than count 2 / INR 25,000; the weak
  comparator default was removed. The corrected guided run completed with count
  2 / INR 25,000.00 and retained reports/charts. Final prose reversed the policy
  and calculation labels of two evidence IDs; the report citations themselves
  resolve correctly. This is guided workflow evidence, not a claim of reliable
  autonomous reasoning. Per user instruction, no more model calls were made to
  tune these misses. Autonomous task-success evaluation remains phase 09.
- Saved-run HTTP checks verified original/CSV/XLSX/Parquet download hashes, paged
  rows, chart spec, citations, dataset registration, archive export/import and
  remapped evidence/version references. Source hashes and synthetic database rows
  remain unchanged. Evidence is retained in `evals/results/phase06-analysis-live.json`,
  `phase06-mixed-live.json`, `evals/reports/phase06-2026-10-03.json`, four trial
  reports and the Markdown/PDF/notebook samples in `evals/results/`.
- Operations and limits are documented in `docs/analysis-and-exports.md` and
  `docs/reports-and-artifacts.md`. `make live-analysis` is an optional diagnostic
  that writes a timestamped report without overwriting retained results. Full
  browser QA remains deferred. Phase 06 is complete; stop before phase 07 until
  the user requests it.


## 3 October 2026: phase 07 first-pass language and voice

- Uses the pinned `indic-language-utils` registry and its actual local
  `FasterWhisperSTTProvider`/configuration, including capability declarations.
  Canonical language tags, script/mixed segments, uncertainty, original text
  and requested answer language accompany messages/runs. Script and Romanized
  detection are small app-owned heuristics, without calibrated confidence claims.
  A configured Bengali contract test adds a language without main-loop branches.
- Prompt `analyst-v5` asks for direct answers in the explicit preference, preserving
  numbers, identifiers, names and citations. Advanced retrieval adds a bounded
  deployment-owned Hindi/English/Romanized glossary and Devanagari numeral search
  forms. Quoted text, code, URLs and identifiers are excluded from glossary matching;
  original queries and source text remain unchanged. Ambiguous `kal` is unresolved.
- Added capability and transient multipart transcription APIs. The local model is
  Systran multilingual Whisper tiny, revision
  `d90ca5fe260221311c53c58e660288d3deb8d356`, with verified local file hashes.
  `make speech-model` provisions assets and selects the ignored local path;
  inference does not download or route to remote speech providers. CPU int8 uses
  two threads, one worker and beam1. Translation/TTS are explicitly unavailable;
  preparatory translation-literal protection is not implemented translation inference.
- Audio defaults: 10 MiB, 60 seconds, mono/stereo 8–96 kHz decoded to mono 16 kHz;
  one active and one waiting request, then 429. Empty/invalid/unsupported inputs,
  duration/size limits, provider/dependency failures, timeout, cancellation and
  decoder cleanup have tests. Native-worker timeout/cancellation closes admission
  until API restart, preventing orphaned work from accumulating. Audio is not
  persisted or content-cached; uploaded spools and decoder containers are closed.
- UI adds recording/upload, editable transcripts, explicit Send, microphone and
  abort cleanup, thread-safe drafts and advertised limits. EN/HI UI preference is
  independent of answer language and applies to core chat, voice, upload and
  connection labels. Technical names/code stay unchanged. Bundled OFL Noto Sans
  Devanagari makes the selected font available offline. Coding-agent catalog review
  is recorded; native-speaker review and full browser QA remain deferred.
- A live preflight exposed Faster-Whisper 1.2.1 / PyAV 19 incompatibility; pinned
  PyAV 16.1.0 corrected `metadata_errors` decoding. Both synthetic audio fixtures
  then transcribed through the library and HTTP API. English preserves 25000/001
  but mistranscribes the scheme; Hindi produces Romanized text, false 5K and garbled
  identifiers. These are editable draft limitations, not passed accuracy gates.
  Fixture provenance, expected transcripts, voices, durations and hashes are in
  `evals/fixtures/voice-v1/manifest.json`. macOS say generation is not product TTS.
- Eight retained retrieval comparisons reuse the four-document bilingual corpus.
  Recall@3 stays 0.50 for English/Hindi; Romanized Hindi falls from 0.50 Basic to 0.25
  Advanced, with non-supporting top-three passages rising from 1 to 2. All reach
  recall@10=1.00. Advanced also includes keyword/fusion stages, not a glossary-only
  ablation. No improvement is claimed; Basic remains default and variants optional.
  Peak process RSS and local STT timing are recorded in
  `evals/reports/phase07-20261003T074004.json`. Quality was not tuned after misses.
- Three configured `gemma-4-26B-A4B-it` runs complete: English file SQL returns
  count 2 / INR 25000.00 with evidence; Hindi Python returns count 2 / INR 25000.0 and retains
  column/dataset IDs; Hindi document retrieval returns S1's inclusive INR200000
  boundary and APP-002 with original evidence. The Python amount has a formatting
  difference; document prose omits the active-status qualifier while its quoted
  source includes it. These small baseline limits remain recorded. No reasoning
  retry was made. Actual microVM execution remains the pinned phase 06 image.
- HTTP transcription leaves run/job/message/artifact counts unchanged, and invalid
  audio returns 422. Results are in `phase07-agent-and-http-2026-10-03.json`; initial
  dependency failure and separate local speech measurements are retained. Final
  validation: 240 deterministic tests pass; integration 42 pass/11 skipped with
  unchanged old connector/embedding fixture skips. Black/mypy, frontend typecheck,
  build/format and `alembic check` pass. No migration is needed.
- User explicitly requested a simple working language baseline, so language/ASR
  misses remain descriptive. Provider and privacy contracts are verified. Read
  `docs/language-and-voice.md` for setup, availability and recovery; `make live-language`
  writes fresh local diagnostic measurements without overwriting retained evidence.
  Phase 07 is complete. Stop before phase 08 until the user requests it.

## 3 October 2026: phase 08 guardrails and audit

- Added typed `execution-policy-v1` allow/reject/clarify snapshots at dispatch,
  ingestion and audit boundaries, independent of model text. Fixed capabilities,
  selected inputs, tool schemas and executor/version/lineage checks remain the
  enforcement layers. Prompt `analyst-v6` redacts configured credentials before
  provider calls. SQL/code arguments are retained with hashes and bounded export.
- SQL fixes reject MySQL executable comments, qualified functions and hidden
  source references under scoped/shadowed CTEs. AST depth/nodes, joins, literals
  and REPEAT counts are bounded. Real PostgreSQL and MySQL tests cover read-only
  transactions even when AST validation is deliberately bypassed, deadlines,
  cancellation, row/byte bounds, unsafe sinks and unchanged original totals.
  All 12 connector checks pass; see `phase08-sql-live-2026-10-03.json`.
- Real microVM checks deny public/metadata networking, expose none of the five
  configured credential environment names, preserve the application's original
  file after guest-copy mutation, bound console flooding, preserve a file created
  before timeout, and serialize concurrent session work. A symlink to `/etc/passwd`
  exports no artifact. Initial symlink cleanup lost its stop response and failed;
  the app now reconciles DELETE plus status 404 without repeating execution.
  The focused retest passes and catalog inspection finds no extra probe guest.
  The initial failure remains in `phase08-sandbox-2026-10-03.json`.
- Resource limits are explicit deployment gaps: one CPU and about 481 MiB memory
  are visible, but requested memory enforcement has no OOM/stress verification.
  The 2 GiB overlay disk setting does not quota `/workspace`, a separate host bind
  volume visible as 4 GiB. Deadline/output limits pass; broad resource stress and
  workspace quotas are not claimed as verified. Unsupported controls are identified
  as required by this phase's acceptance contract.
- XLSX rejects duplicate and noncanonical archive members. Existing crawler checks
  retain exact allowlists, pinned public DNS addresses, revalidated redirects and
  disabled environment proxies. Archive traversal/symlink/expansion tests, isolated
  active-content rendering and safe spreadsheet export contracts pass.
- Configured plaintext secret sentinels cover prompts, results, frontend messages
  and artifact routes, guest console/output collection, logs, audit, report bytes,
  and portable metadata/assets. Redaction precedes console display clipping;
  unsafe downloads/archives are refused rather than corrupting original hashes.
  This is not detection of unknown, encoded, compressed or short credentials.
- Synthetic EN/HI fixtures cover document/OCR/cell/database/error/archive content.
  A single bounded `gemma-4-26B-A4B-it` trial summarizes their hostile intent with
  one model call, no execution capabilities, no executed action and no reasoning
  retry. This small diagnostic is not a universal injection benchmark. Negative
  capability/selection/credential/path/SQL/network tests enforce the boundaries;
  see `phase08-injection-2026-10-03.json` and `test_phase08_boundaries.py`.
- Added filtered/paged run audit inspection and schema-1 JSON export, per-entry
  depth/content bounds, a 2 MiB cap, explicit truncation counts, producer IDs,
  selected/current source versions, source processing outcomes and code/result
  links. Valid declared prior-run citations/artifacts in the same thread are
  included. The retained phase-06 mixed-source run reconstructs its question,
  three sources/versions, aggregate count 2 / INR 25000.00, cited evidence and
  retained outputs. Historical policy/arguments remain explicitly unrecorded.
  Actual HTTP filters/page cursor and export return 200; export is 50,547 bytes
  with no unresolved declared references. Reports: `phase08-audit-run-6d1d074b-2026-10-03.json`
  and `phase08-http-2026-10-03.json`.
- Hard answer validation rejects unknown/mismatched reference kinds, threads,
  source selection and versions. Bounded structured-preview checks add visible
  warnings for unmatched numbers, absent units, unqualified partial results and
  unsupported creation claims. They are descriptive checks, not semantic proof.
- Final checks: 268 deterministic tests pass; full integration 55 pass/2 explicit
  skips plus the new focused dispatch-sentinel test passes (56 aggregate checks).
  The skips reuse earlier pinned-embedding and microVM analysis evidence; this
  phase has separate actual sandbox probes. Two initial integration failures
  (over-redacted portable token counts and old rejection-status expectations)
  were fixed and their focused reruns pass. PostgreSQL/MySQL/RustFS, Black/mypy,
  frontend type/build/format and `alembic check` pass. No migration is needed.
  Host API and worker were restarted with phase 08; full browser QA stays deferred.
- Read `docs/guardrails-and-audit.md` for controls, tested bypasses, export contracts
  and phase-09 release gates. Phase 08 is complete with the recorded resource and
  UI limits. Stop before phase 09 until the user requests it.

## 3 October 2026: phase 09 core evaluation baseline

- User narrowed this phase to the important components first. The core scope is
  implemented and committed; the complete phase plan remains integration/review
  pending. Stop before phase 10. Optional judges and broad comparisons are deferred.
- Added isolated `backend/evaluation` contracts, deterministic metrics, public-API
  runner, checkpoint/resume, experiment identity, JSON/CSV/static HTML reports and
  human-review export/import. Production request handling does not load the runner
  or optional judging dependencies. `make eval-list`, `make eval-check`, and the
  explicit expensive `make eval-live` target are available; normal chat is unchanged.
- `evals/cases/core-v1.json`, inventory core-v1.1, contains ten synthetic cases:
  EN/HI CSV calculations, EN/HI documents, mixed-source criteria/calculation,
  Hindi ambiguity, an unsupported question, hostile source text, XLSX and OCR.
  The default repeat baseline selects the two CSV cases. Fixture paths/hashes,
  source versions, expected numbers/tolerances/passages/artifacts, allowed actions,
  language, answerability, rubric and synthetic review provenance are explicit.
  Conversation and wider table/OCR workflows remain future inventory work.
- Every trial creates an `eval:` workspace/thread and uploads through the public
  APIs; source originals are verified by recomputing bytes from the public portable
  archive. Evaluation never reads application DB tables to perform the workflow.
  Typed timeouts, API/model failures and interrupted runs remain in reports.
  Atomic checkpoints and a directory lock prevent concurrent overwrite; persisted
  run IDs are observed on resume without resubmitting execution. Ambiguous resource
  creation fails explicitly rather than creating a duplicate. Completed failures
  remain cached until an explicit fresh experiment.
- Identities include cases/fixtures, configured endpoint/model, local application/
  runner code and dependency hashes, prompt/policy, parser/chunker/retrieval,
  embedding/reranker, OCR/profile, budgets, guest image, repeats and timeout. Actual
  run config/index generation metadata is retained. Key deployment mismatches fail;
  changed identities refuse resume. Hidden endpoint model-weight changes cannot be
  inferred from a name; operator versioning/fresh experiments remain necessary.
- Metrics compare structured executed results with Decimal tolerances, not SQL
  strings. They inspect the latest successful calculation, excluding samples and
  earlier queries; expected values/units in prose are checked separately. Citations
  require the selected source/version and declared reference, and artifacts are
  checked through download/schema contracts. Pure CER/WER, aligned-table and
  retrieval precision/recall/MRR helpers have bounded tests. They are not a full
  unlabeled OCR/retrieval benchmark or proof of semantic answer correctness.
- Live `gemma-4-26B-A4B-it` baseline: three EN and three HI file-SQL trials all
  complete and return count 2 / INR 25000.00 with evidence and intact originals.
  Numeric and answer-unit checks pass, but all six final trial statuses are
  `needs_review` because structured unit provenance is absent. EN latency p50/p95
  is 8.94/10.36 seconds; HI is 9.04/9.08 seconds. Total 24 model calls and 103,963
  reported tokens. No reasoning retry or tuning was made.
- Six single workflow trials: EN/HI documents and hostile-text handling pass their
  synthetic checks; mixed-source criteria/calculation returns the correct numbers
  and citations but needs unit review. Hindi ambiguity and English unsupported
  answers ask sensible clarification in prose but omit the required clarification
  flag, so both fail the contract. These misses are retained without prompt tuning.
  The hostile-text run rejects a malformed search call then uses the valid summary
  tool; no prohibited capability or original-source mutation occurs.
- Review found an actual scoring bug: a completion pass hid missing verification
  metrics. The reducer now keeps non-provenance review checks pending. Another
  false failure came from a fixture allowlist omitting `summarize_documents`; the
  inventory was corrected. Saved observations were rescored without additional
  model calls; execution identities and previous statuses are retained separately
  from the new scoring identity. Initial/corrected status evidence is in
  `phase09-scoring-preflight-2026-10-03.json`. Across the 12 final trials there are
  3 synthetic passes, 2 contract failures and 7 review items, not a passed release gate.
- Retained reports/viewers are in `evals/reports/phase09-baseline-2026-10-03/` and
  `phase09-workflows-2026-10-03/`. Reports separate EN/HI status/metric counts,
  latency, known/unknown token and model-call coverage, source/run/artifact links
  and label provenance. Runner peak RSS excludes API/worker/guest resource use;
  the initial ingestion timings measured readiness wait only, not complete upload
  cost. The runner now measures upload plus readiness and records worker query
  time when available. No additional live trial was needed for this timing fix.
- Human-review imports bind to experiment/case/repetition/answer hashes and reject
  foreign, duplicate, malformed or oversized labels; annotations cannot erase
  automatic calculation failures. Labels remain synthetic and uncalibrated.
  RAGAS/provider adapters, judge calibration, multi-model/tool-format comparisons,
  fine-grained stage ablations, production thresholds, broad resource profiling
  and full UI QA are deferred. No judge score or universal quality claim is made.
- Validation: 287 deterministic tests pass via `make eval-check`; 10 focused real
  PostgreSQL runtime/selection/lease/cancellation/credential checks pass; Black and
  strict mypy pass for production and evaluation modules. Actual API, worker,
  sandbox, PostgreSQL and RustFS served the 12 trials. No migration or new package
  dependency is needed. Numeric operational token counters are preserved by
  redaction while credentials remain masked. Read `docs/evaluation.md` for usage,
  resume/rescore semantics, review workflow and remaining work.

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
| 04    | Not started | Not run                     | All phase checks |
| 05    | Not started | Not run                     | All phase checks |
| 06    | Not started | Not run                     | All phase checks |
| 07    | Not started | Not run                     | All phase checks |
| 08    | Not started | Not run                     | All phase checks |
| 09    | Not started | Not run                     | All phase checks |
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

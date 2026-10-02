# Agentic RAG Analyst

A local analyst workspace for documents, SQL, spreadsheets, and generated Python.
Source files remain immutable. RustFS stores original and derived bytes;
PostgreSQL stores metadata, jobs, events, and conversation history. Generated
Python runs in a separate network-disabled microVM.

## Start locally

Install Docker Compose, uv, Node.js, and pnpm. Docker runs infrastructure only.
The API, worker, frontend, and sandbox service run directly on the host.

```bash
make setup
make up
make migrate
make dev
```

`make setup` copies `.env.example` when needed and installs locked backend and
frontend dependencies. `make up` starts PostgreSQL 17 with pgvector and RustFS,
waits for infrastructure health, and initializes the S3 bucket from the host.
`make dev` starts the API, worker, and Vite in one foreground process. Ctrl-C
stops the apps. API and frontend reload on changes; restart the worker after
backend changes. `make up-all` combines infrastructure, migrations, and dev.

Open [the workspace](http://127.0.0.1:5173). API health is at
[port 8000](http://127.0.0.1:8000/api/health). Individual terminals can instead
use `make api`, `make worker`, and `make frontend`. Configure `API_PORT` and
`FRONTEND_PORT` in `.env` for `make dev`, or export them for individual commands.

PostgreSQL listens on loopback port 55432. RustFS exposes its
[S3 endpoint](http://127.0.0.1:19000) and
[console](http://127.0.0.1:19001). Default development credentials are
`analyst-dev` and `analyst-dev-secret`. Override infrastructure ports and
credentials in `.env` and keep host S3/database settings aligned. Both databases
persist in Docker volumes. RustFS runs as UID 10001; its startup service sets
ownership on the data volume.

`make down` stops infrastructure and preserves volumes. Stop host apps with
Ctrl-C separately. `make logs` follows infrastructure logs; app logs appear in
their terminal. Removing Docker volumes deletes saved data and requires running
`make up` and `make migrate` again.

## Sandbox service on macOS

The standalone HTTP service is pinned to revision
`b3f032b6a0ce1fab7cebc75250073f58b5b6c63c` of
[Hari31416/sandbox](https://github.com/Hari31416/sandbox). Install the
Mac-compatible microsandbox CLI, `msb`, then run:

```bash
bash scripts/setup-sandbox.sh
make sandbox
```

Setup installs the service into `.sandbox/venv`. The launcher uses port 8787 and
`.sandbox/data`, selects the microVM backend, and passes only sandbox settings
and required host runtime paths to the service. Model, database, and RustFS
credentials stay outside that environment. Set `SANDBOX_IMAGE` to the supported
analysis image digest in `.env`. The service binds to loopback by default.
`SANDBOX_AUTH_TOKEN` enables service authentication.

Startup alone does not prove execution readiness. `make live-sandbox` creates a
real guest, executes Python, exports verified bytes, and stops/deletes the
session. Missing microsandbox support fails explicitly. There is no host or
subprocess execution fallback.

Each run owns its guest session. A fixed launcher executes staged Python files;
imports include only retained artifacts permitted for that thread and selected
sources. Outputs are copied to application storage before cleanup. Code,
output hashes, and tool lineage remain available after the guest is deleted.

## Configuration and verification

The root `.env` configures host apps and Compose infrastructure. RustFS is the
default object store through `STORAGE_BACKEND=s3` and the host S3 URL. Select
`filesystem` explicitly for isolated development or tests.

Set `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` for the supplied
OpenAI-compatible endpoint. Missing model configuration keeps the workspace
available and disables new chat runs. Set a valid Fernet
`DATABASE_ENCRYPTION_KEY` before saving external database credentials.
Restart API/worker processes after changing their environment settings.

```bash
make health             # Infrastructure status and local HTTP health
make format             # Format backend and frontend
make check              # Backend checks and frontend typecheck/build
make test-integration   # Requires TEST_DATABASE_URL / TEST_S3_* settings
make live-model         # Explicit English/Hindi provider tool-call checks
make live-sandbox       # Explicit real microVM check
make live-agent         # Full English/Hindi model + microVM + RustFS checks
```

Compose reads the root `.env` explicitly. The API/worker load it through
Pydantic settings. Vite receives only its API proxy URL, without backend
credentials. Default tests skip live gates unless explicitly enabled.

## Chat and live evaluation

Create a workspace and thread, select the answer language, and send a question.
The worker validates tool calls and records progress, generated code, artifacts,
and final answers. Artifact rows provide downloads and bounded text or PNG/JPEG
previews. Stop requests cancellation and keeps progress connected until guest
cleanup is recorded. Retry creates a new run; reconnecting replays saved events.
The source panel supports file uploads and encrypted database connections.

`make live-agent` uses synthetic grant amounts in two saved threads. It checks
count 2 and INR 25,000, English/Hindi answers, output hashes after guest cleanup,
final references, saved history, request idempotency, and event replay. It writes
`data/reports/phase01-live.json`. Live commands intentionally make model requests.

Run submission accepts a UUID `request_id` for idempotency. Events at
`GET /api/runs/{id}/events` use numeric sequence IDs and accept `Last-Event-ID`
or `?after=`. Run status records cleanup separately as `pending`, `complete`,
or `failed`, so an answer does not hide a guest cleanup failure.

## RustFS image

The image is pinned as `ghcr.io/rustfs/rustfs:1.0.0-glibc` at digest
`sha256:bffcab0c9d647aab0055d1c69d340b202d0909966b385932d4ead1aeb7602858`.
See the [release list](https://github.com/rustfs/rustfs/releases),
[official package](https://github.com/rustfs/rustfs/pkgs/container/rustfs), and
[container setup docs](https://docs.rustfs.com/en/installation/container).

## Structured sources

Upload CSV, XLSX, or legacy XLS in the source panel. Originals remain immutable
in RustFS. Profiles report exact row counts, sampled type inference, missingness,
bounded examples, and formula-cache warnings. CSV decoding/delimiter detection
is explicit; leading-zero identifiers stay text. Workbook macros and unsafe
archives are rejected. File SQL stages canonical UTF-8 working copies and treats
columns as VARCHAR; numeric/date casts must be explicit.

Run `make analysis-image` once to build and register the DuckDB 1.4.4 / xlrd 2.0.2
microVM image. This builds a guest image and records its microsandbox digest in
`.env`. Restart host apps afterward. `make eval-infra` starts the optional MySQL
8.4 fixture database on port 33306. PostgreSQL remains on port 55432. Application
processes run through `make dev` directly on the host.

Database credentials use Fernet encryption with `DATABASE_ENCRYPTION_KEY`.
Connection setup discovers tables, columns, keys, and relationships. The UI
supports TLS modes and explicit schema refresh. Queries parse as one selected-table
read statement, with an allowlist of scalar/aggregate functions. Writes, unsafe
functions, file/network readers, optimizer hints, and system schemas are rejected.
Read-only transactions and server query deadlines add database enforcement.
Queries retain at most 5,000 tool rows and run for at most 30 seconds. Limits apply
to returned rows after aggregation; decimal values serialize as exact strings.

Use selected sources and optionally narrow to particular sheets/tables. Source
descriptions and metric hints are optional. `run_sql` retains SQL, result CSV,
input/schema versions, hashes, and calculation evidence. `run_python` can stage
selected file datasets or retained result artifacts; database credentials never
enter the guest. `register_dataset` turns a CSV artifact into a derived source
for selection in a later run and retains its lineage.

Repeat the five synthetic live source cases with
`cd backend && uv run python -m app.structured_probes`. Reports are saved under
`evals/reports`. Complete browser verification is deferred.

## Document retrieval

Upload digital PDF or DOCX in the source panel. RustFS retains the original
bytes. The worker stores extracted blocks, stable page/paragraph/table locations,
script metadata, warnings, and separate versioned chunks. Scanned PDFs report
`ocr_needed`; OCR is scheduled for phase 04. Extraction is bounded to 2,000 PDF
pages, 12 MiB of text, 100,000 blocks, and 512 chunks per document. Unsupported
or oversized documents receive a visible failure instead of an empty index.

Run `make embedding-model` once to download the pinned quantized
`intfloat/multilingual-e5-small` assets and record their SHA-256 manifest and
configuration in `.env`. Restart `make dev` afterward. Inference runs locally
on the host CPU and produces 384-dimensional normalized vectors. Assets are
loaded from the configured directory; ingestion never downloads missing models.
Without compatible assets, documents remain available for lexical retrieval and
report `index_degraded`. Dense requests report unavailable, and hybrid responses
identify the lexical fallback in their trace.

The `structure-token-v2` chunker groups narrative blocks under headings and
keeps table groups separate. With the pinned tokenizer, body windows contain at
most 384 tokens with 48-token overlap and bounded heading context. Without a
matching tokenizer, conservative UTF-8 byte windows bound the input instead.
Original Unicode excerpts remain separate from normalized search text.

Retrieval supports English stemming, Hindi/simple PostgreSQL full-text search,
exact cosine search, and reciprocal rank fusion. Selected document versions
scope candidates and neighbor expansion. Every index generation records its
source hash, extractor/chunker, model revision, dimensions, and adapter version.
`POST /api/documents/{id}/reindex` indexes existing chunks without changing saved
extraction or citation locations.

Document search and passage tools retain evidence IDs, original excerpts,
locations, rank/score metadata, actual retrieval mode, and bounded context.
Saved answers reopen the same citation version through `/api/evidence/{id}`.
The source panel includes extraction inspection and the chat includes citation
previews and retrieval traces. Full browser verification remains deferred.

Run `make live-documents` with host apps and infrastructure running to measure
Hindi/English fixture recall and execute live mixed-source cases. The report in
`evals/reports/phase03-2026-10-02.json` includes misses, document plus calculation
citations, retained CSV/PNG outputs, and original-file/database integrity checks.

Document extraction, table review, retry/removal, and approved website imports are documented in [docs/ingestion.md](docs/ingestion.md).

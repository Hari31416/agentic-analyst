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
Source uploads and database connections are introduced in phase 02.

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

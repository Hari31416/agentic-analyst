# Agentic RAG Analyst

An evidence-backed workspace for asking questions across documents, spreadsheets,
and SQL databases. Combine document retrieval with SQL and Python analysis, then
inspect the sources, calculations, and generated artifacts behind an answer.

The application runs locally, with an OpenAI-compatible endpoint for chat.
Original files remain immutable, and generated Python runs in a separate,
network-disabled microVM.

## Features

- **Document search:** ingest PDF, DOCX, TXT, Markdown, HTML, and PPTX, with local
  OCR for scanned PDFs and lexical, dense, or hybrid retrieval.
- **Structured analysis:** upload CSV, XLSX, or XLS files and connect external
  databases for read-only SQL queries.
- **Python execution:** run generated analysis in isolated microVMs and retain
  code, output hashes, and source lineage after cleanup.
- **Evidence and outputs:** inspect citations, retrieval traces, tables, charts,
  downloadable artifacts, and versioned PDF reports.
- **English and Hindi:** choose an answer language, with optional local speech
  transcription.
- **Saved work:** organize workspaces and threads, pin useful content, replay run
  progress, and export analysis.

## Architecture

| Component | Technology | Runs in |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite, Tailwind CSS | Host |
| API | FastAPI, Pydantic, SQLAlchemy | Host |
| Worker | Python, durable PostgreSQL jobs | Host |
| Metadata and retrieval | PostgreSQL 17, pgvector | Docker |
| File and artifact storage | RustFS, S3-compatible API | Docker |
| Python execution | Sandbox service and microsandbox microVMs | Host service, isolated guests |

PostgreSQL stores metadata, jobs, events, and conversation history. RustFS stores
original files and derived artifacts. Docker runs infrastructure only; the API,
worker, frontend, and sandbox service run directly on the host.

## Getting started

### Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- Node.js and [pnpm](https://pnpm.io/).
- Docker with Docker Compose.
- An OpenAI-compatible model endpoint to enable chat.
- A compatible `msb` CLI installation for microVM execution. The sandbox setup
  below targets macOS.

### Install and configure

From the repository root:

```bash
make setup
```

This installs locked backend and frontend dependencies and creates `.env` from
[.env.example](.env.example) if it does not already exist.

Before starting the API, set these values in `.env`:

```dotenv
JWT_SECRET_KEY=<random secret of at least 32 bytes>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your initial password>

OPENAI_BASE_URL=<your endpoint URL>
OPENAI_API_KEY=<your API key>
OPENAI_MODEL=<your model ID>
```

Generate a signing key with:

```bash
cd backend
uv run python -c 'import secrets; print(secrets.token_urlsafe(48))'
cd ..
```

The first API startup creates the admin account. Sign in with the configured
credentials; admins can create additional accounts through **Users**. Accounts
share workspace data. See [authentication](docs/authentication.md) for account
and session behavior.

Missing model configuration leaves the workspace available but disables new
chat runs. Set `DATABASE_ENCRYPTION_KEY` to a valid Fernet key before saving
external database credentials. Keep credentials in the ignored `.env` file.

### Start the application

```bash
make up       # Start PostgreSQL and RustFS; initialize the storage bucket
make migrate  # Apply database migrations
make dev      # Run the API, worker, and frontend in the foreground
```

The migration history is consolidated into `001_initial_schema` for fresh
databases. Recreate databases using the older revisions before running
`make migrate`. Historical data migration is not supported.

| Service | Default address |
| --- | --- |
| Workspace | [http://127.0.0.1:5173](http://127.0.0.1:5173) |
| API health | [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health) |
| RustFS S3 endpoint | [http://127.0.0.1:19000](http://127.0.0.1:19000) |
| RustFS console | [http://127.0.0.1:19001](http://127.0.0.1:19001) |
| PostgreSQL | `localhost:55432` |

Use `.env` to change ports and infrastructure credentials. Keep the host
connection settings aligned with the exposed infrastructure ports. If you change
the frontend origin, update `AUTH_ALLOWED_ORIGINS` too.

### Enable Python analysis

Install the macOS-compatible microsandbox CLI, then prepare the sandbox service
and analysis image:

```bash
bash scripts/setup-sandbox.sh
make analysis-image
make sandbox
```

`make analysis-image` pulls the published
`hari31416/agentic-rag-analyst-runtime:py312-v1` image into microsandbox and records
the pinned registry digest from `.env.example` as `SANDBOX_IMAGE` in `.env`.
The image extends `hari31416/data-science-heavy-runtime:py312-v2` with
DuckDB 1.4.4 and xlrd 2.0.2, and supports Linux ARM64 and AMD64.
No local Docker build is needed. Restart the API
and worker after the configuration changes. Run `make sandbox` in a separate
terminal; the service defaults to loopback port 8787.

Verify real guest execution with `make live-sandbox`. Python execution requires
a working microVM backend; there is no host or subprocess fallback. Each run
owns its guest, and outputs are copied to application storage before cleanup.

### Enable local document models and OCR

```bash
make embedding-model  # Download pinned multilingual embedding assets
```

Restart the API and worker afterward. Without compatible embedding assets,
documents remain available through lexical retrieval and expose their degraded
index status. Ingestion does not download missing models at runtime.

For scanned PDFs, install Tesseract separately with its `eng`, `hin`, and `osd`
assets. Optional reranking and speech models can be provisioned with
`make reranker-model` and `make speech-model`. See the
[ingestion](docs/ingestion.md), [retrieval](docs/retrieval.md), and
[language and voice](docs/language-and-voice.md) guides for configuration.

## Usage

1. Sign in and create a workspace and thread.
2. Upload sources or add a database connection in the source panel.
3. Select the sources, sheets, or tables relevant to your question.
4. Choose the answer language and send your question.
5. Inspect the answer's citations, tool activity, and generated artifacts.

Stop requests cancellation and retains progress through guest cleanup. Retry
creates a new run, and reconnecting replays saved events. Original source files
remain unchanged. Website imports are disabled until exact hostnames are
explicitly approved in configuration.

## Development

```bash
make format            # Format backend and frontend
make check             # Backend formatting, typing, tests; frontend typing/build
make test              # Backend tests
make test-integration  # Requires TEST_DATABASE_URL and applicable TEST_S3_* settings
make eval-check        # Deterministic checks without live services or model calls
make eval-list         # List evaluation cases
```

API and frontend reload during `make dev`; restart the worker after backend
changes. To run services in separate terminals, use `make api`, `make worker`,
and `make frontend`. Restart API and worker processes after environment changes.

Live checks are explicit and require the corresponding services:

```bash
make live-model      # Provider tool-call checks in English and Hindi
make live-sandbox    # Real microVM execution and artifact checks
make live-agent      # Model, microVM, storage, history, and replay checks
make live-documents  # Document retrieval and mixed-source checks
```

Model-backed checks make requests to the configured provider. See the
[evaluation guide](docs/evaluation.md) for repeatable trials, checkpoints,
offline scoring, and report generation.

### Process management

| Command | Action |
| --- | --- |
| `make start` / `make stop` / `make restart` | Manage infrastructure and background apps |
| `make app-start` / `make app-stop` / `make app-restart` | Manage host apps separately |
| `make status` | Show infrastructure, local processes, and health |
| `make logs` | Follow Docker infrastructure logs |
| `make logs-backend` / `make logs-worker` | Follow API or worker logs |
| `make logs-frontend` / `make logs-sandbox` | Follow frontend or sandbox logs |
| `make down` | Stop infrastructure and preserve volumes |
| `make help` | List common development commands |

Background application logs are stored in `logs/`. Ctrl-C stops foreground apps.
Docker volumes retain database and object-store data; removing them deletes saved
state and requires infrastructure initialization and migrations again.

## Agent tools

The chat agent has nine tools:

- `list_sources`, `inspect_schema`, and `sample_rows` discover selected inputs.
- `search_documents` and `source_passage` retrieve original document evidence.
- `run_python` and `run_sql` calculate results and retain output files.
- `list_artifacts` and `inspect_artifact` discover and read retained outputs.

Python handles cleaning, joins, statistics, charts, and requested report files.
`analyze_data`, `dataset_profile`, `summarize_documents`, `generate_report`, and
`register_dataset` are not exposed to the chat agent. Their backend
implementations remain for existing internal callers and APIs.

## Project structure

```text
backend/    API, workers, analysis tools, evaluation runner, and tests
frontend/   React application
infra/      Docker Compose infrastructure
scripts/    Development launchers and model/sandbox provisioning
docs/       Feature and operational documentation
evals/      Fixtures, evaluation cases, reports, and reviews
plans/      Implementation plans
```

## Documentation

| Guide | Covers |
| --- | --- |
| [Authentication](docs/authentication.md) | Accounts, sessions, API clients, and shared access |
| [Document ingestion](docs/ingestion.md) | Formats, OCR, table review, and website imports |
| [Retrieval](docs/retrieval.md) | Search stages, reranking, context, and inspection |
| [Analysis and exports](docs/analysis-and-exports.md) | Analyst operations and portable outputs |
| [Reports and artifacts](docs/reports-and-artifacts.md) | Artifact viewers and answer references |
| [Versioned reports](docs/reports.md) | PDF reports, pinned inputs, and regeneration |
| [Language and voice](docs/language-and-voice.md) | English/Hindi behavior and local transcription |
| [Saved pins](docs/pins.md) | Personal thread, message, and artifact pins |
| [Resource lifecycle](docs/resource-lifecycle.md) | Rename, deletion, retention, and blob cleanup |
| [Guardrails and audit](docs/guardrails-and-audit.md) | Execution boundaries, limits, and run inspection |
| [Evaluation](docs/evaluation.md) | Cases, scoring, repeatable runs, and review |

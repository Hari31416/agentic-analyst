# Implementation plan

Build an on-prem workspace for document research, SQL, spreadsheet analysis,
and generated Python. Use one tool-using agent, Hindi/English support, original
sources that remain read-only, and derived datasets and reports with evidence.

These plans turn the decisions in the conversation into build instructions.
They are the implementation reference; `docs/initial-design.md` records the
earlier design discussion. Apply later user instructions before these plans.

## Start here

1. Read [build rules and contracts](contracts.md).
2. Check [implementation status](status.md) and identify the first unfinished phase.
3. Read that phase and its listed reference material in [reference projects](references.md).
4. Implement its deliverables, run its checks, and record evidence in `status.md`.
5. Continue through the ordered phases within the assigned task scope. Routine
   implementation decisions and reversible fixes do not require new approval.

Every phase delivers working behavior, including the relevant UI, tests, and
documentation. Finish dependencies before dependent work. Use actual repository
scripts after they exist; command names below define the intended development
interface rather than claiming those commands already exist.

## Phase sequence

| Phase                                  | Build                                                                    | Completion outcome                                                         |
| -------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| [00](phase-00-foundation.md)           | Repository, Compose, persistence, contracts, fixtures                    | Repeatable development environment and known-answer sample pack            |
| [01](phase-01-agent-and-sandbox.md)    | Model adapter, single-agent loop, sandbox, streaming chat                | Agent executes Python and produces a durable artifact                      |
| [02](phase-02-structured-analysis.md)  | CSV/Excel, MySQL/PostgreSQL, read-only SQL and file analysis             | Correct, inspectable analysis over every initial structured source         |
| [03](phase-03-document-rag.md)         | PDF/DOCX ingestion, multilingual embeddings, hybrid retrieval, citations | First complete bilingual mixed-source workflow                             |
| [04](phase-04-ingestion-and-ocr.md)    | OCR, document hierarchy, robust tables/chunks, additional formats        | Reliable digital/scanned document processing with traceable extraction     |
| [05](phase-05-advanced-rag.md)         | Multi-query, dependent hops, reranking, summaries, context management    | Advanced RAG capabilities with measured profiles                           |
| [06](phase-06-analysis-and-reports.md) | Cleaning, statistics, joins, charts, reports and portable exports        | Full analyst workspace with reproducible derived outputs                   |
| [07](phase-07-language-and-voice.md)   | Language extensions, translation/transliteration, STT/TTS                | Hindi/English text and voice workflows with explicit provider availability |
| [08](phase-08-guardrails-and-audit.md) | Adversarial controls, audit completeness, failure recovery               | Tested execution safeguards and inspectable audit history                  |
| [09](phase-09-evaluation.md)           | RAGAS integration, human labels, repeated trials and quality reports     | Versioned quality measurements and release gates                           |
| [10](phase-10-operations-and-scale.md) | Packaging, storage options, scaling, backup and restore                  | Verified development and on-prem deployment profiles                       |

The first mixed-source milestone is phase 03. The requested application scope
continues through phase 10. Audit, evaluation fixtures, source protection, and
language metadata start in phases 00 through 03; later phases deepen those capabilities.

## Settled choices

- One agent decides tool use and synthesis; the runtime enforces execution rules.
- The user supplies an OpenAI-compatible endpoint through `OPENAI_BASE_URL`,
  `OPENAI_API_KEY`, and `OPENAI_MODEL`.
- Use the standalone sandbox service used by Nexus through our own HTTP adapter.
  The user confirms it works on Mac out of the box. Follow that setup.
- Use Docker Compose for local development, with infrastructure-only and full
  stack Makefile targets following the reference applications.
- Use PostgreSQL with pgvector and text search; store file bytes in RustFS
  through an S3 adapter by default. Filesystem is a development option.
- Start with PDF, DOCX, CSV, Excel, MySQL, and PostgreSQL.
- Preserve originals. Cleaning and analysis create derived outputs.
- Internet is available. Process source content locally by default, apart from
  the explicitly configured model endpoint. Other external providers require
  deployment configuration rather than automatic fallback.
- Keep uploads, connections, and workspace selection simple. Department-level
  permissions, RBAC, SSO, and database provisioning/topology are outside scope.
- Use reference projects for patterns. Implement app-owned components here.
  The sandbox service and language library are intentional external dependencies.

## Implementation defaults

Use Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy/Alembic, standard logging,
Black, and pytest. Use TypeScript, React, Vite, pnpm, shadcn, and frontend type
checks. Choose compatible supported dependency versions, pin them in lockfiles,
and record model/service revisions. Resolve ordinary library choices by inspecting
the references and current official documentation.

Use a small application-owned tool loop and an OpenAI-compatible client adapter.
Use DuckDB for file SQL and pandas/numpy for Python analysis in the sandbox.
Use Plotly for interactive charts and an image fallback for static plots. Use
PostgreSQL jobs initially, filesystem-backed durable files initially, and a
storage interface that can receive an S3-compatible implementation in phase 10.
Use a persistent workspace and thread model without a permissions hierarchy.

Build a useful UI alongside each backend capability. Chat, upload/source
selection, connection forms, evidence viewing, tables/charts, outputs, progress,
and history are sufficient. Add controls only when they change useful behavior.

## Verification and handoff

Distinguish deterministic tests, real-service tests, and live model tests.
Mocks may test errors and protocol handling; they cannot prove real execution,
retrieval quality, or model performance. If credentials or a service are missing,
finish independent work and record the remaining gate as integration pending.
Never mark that gate verified or quietly use a weaker runtime.

Each phase handoff records changed behavior, commands and results, migrations,
configuration additions, unresolved failures, and the next dependency. Keep
`status.md` current. If committing is authorized, use the user's conventional
commit format with scopes `backend`, `frontend`, `infra`, or `general`.

## Prompt for the implementing agent

> Build this application following `plans/README.md`. Read `plans/contracts.md`
> and `plans/status.md`, then implement the phase documents in order. Treat the
> settled choices as constraints and the implementation defaults as starting
> choices. Inspect the reference projects only when the phase calls for them.
> Deliver working backend and UI behavior, maintain meaningful tests and docs,
> and record verification evidence in `plans/status.md`. Continue through the
> assigned phases without pausing for routine decisions. Keep source files and
> databases read-only, use the existing Nexus-style sandbox, and configure the
> supplied OpenAI-compatible model endpoint through environment variables. Do
> not claim live checks passed when credentials or services were unavailable.

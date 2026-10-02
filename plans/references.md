# Reference projects

Read the paths relevant to the active phase. Treat source code as a reference
for behavior, interfaces, and known gaps. Keep these repositories unchanged.
If they are unavailable in the implementing environment, use these plans and
the public repositories where available; record the unavailable reference.

## Unstructured data

Root: `/Users/hari/Desktop/advanced-rag`.

- `backend/app/services/document/`: extraction, hybrid PDF handling, Docling,
  hierarchy, preprocessing, chunking, and document lifecycle.
- `backend/app/services/query/`: staged retrieval, multi-query, decomposition,
  context assembly, citations, and conversation handling.
- `backend/app/services/summary/`: clustering and hierarchical summaries.
- `backend/app/services/custom_chunks/`: import/export schemas.
- Tests under `backend/tests/services/` and query/ingestion API tests.
- Frontend citation viewers and pipeline debugger as interaction references.

Redesign service coupling for PostgreSQL and native bilingual retrieval. The
inspected multi-hop implementation includes independent subquestions; dependent
hops must also use evidence from earlier searches.

## Structured analysis

Root: `/Users/hari/Desktop/Miscs/codeagent`.

- `backend/app/services/db_tools.py` and `db_connection_service.py`.
- `backend/app/services/workspace_tools.py` and `workspace_service.py`.
- `backend/app/shared/serialization.py` and typed stream/artifact models.
- `backend/app/services/export_service.py` and frontend artifact rendering.
- Agent/executor files for interface patterns, not an in-process execution boundary.

The inspected SQL service relies on savepoint rollback. Implement proper
read-only enforcement and bounded queries rather than carrying that guard over.

## PostgreSQL, durable runs, and sandbox

Root: `/Users/hari/Desktop/Miscs/nexus/nexus`.

- `packages/nexus-core/nexus_core/repositories/runs.py`: claims and leases.
- `packages/nexus-core/nexus_core/rag/` and `repositories/rag.py`: pgvector,
  lexical retrieval, fusion, evidence, and extraction.
- `packages/nexus-core/nexus_core/sql/`: connector protocols and SQL AST validation.
- `packages/nexus-core/nexus_core/sandbox_client.py`: client/protocol reference.
- `sandbox/sandbox_service/api/routes/`, `models.py`, and runtime code: actual
  session, execution, stdout/stderr, file, heartbeat, and artifact contracts.
- Compose files and Makefile/justfile patterns; `evals/` for mixed-source cases.

Standalone sandbox repository: <https://github.com/Hari31416/sandbox>.
Use the service through an app-owned adapter pinned to a verified revision.
The user confirms it works on Mac. Follow that runtime setup and verify actual
execution. Do not generalize the Linux-specific Compose profile into a Mac blocker.
The inspected execution POST completes execution before returning; stdout/stderr
are separate endpoints. Inspect the pinned service before assuming asynchronous
job behavior, execution cancellation support, or an artifact-download endpoint.

The inspected Nexus reranker is a no-op and lexical search is English-specific.
Implement actual reranking and language-aware indexing. Keep DeepAgents,
billing, Clerk, and unrelated control-plane components outside this application.

## Language library

Root: `/Users/hari/Desktop/sandbox/language-utils`.
Repository: <https://github.com/Hari31416/indic-language-utils>.

Read `languages.py`, capability/provider protocols, routing, relevant clients,
and provider tests in `src/indic_language_utils/` and `tests/`.
Use the library through adapters with optional extras. Local detection,
Aksharamukha transliteration, and Faster-Whisper STT are present in the inspected
version. Inspected translation/TTS providers are remote; local equivalents need
explicit adapters and validated models. Do not silently enable cloud fallbacks.

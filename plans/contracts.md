# Build rules and shared contracts

Read before implementing any phase. Define these contracts in code during
phase 00 and evolve them through migrations and versioned API schemas.

## Repository and service boundaries

Suggested layout:

```text
backend/app/
  api/                 # HTTP, SSE, request validation
  agent/               # tool loop, model adapter, context handling
  tools/               # typed model-facing operations
  sources/             # uploads, datasets, database connectors
  ingestion/           # extraction, OCR, chunking, index jobs
  retrieval/           # dense/lexical search, fusion, reranking
  sandbox/             # HTTP client, sessions, staging, artifact collection
  language/            # registry, capability adapters, provider policy
  policy/              # execution checks and decisions
  evidence/            # provenance and answer references
  storage/             # durable file interface and implementations
  db/                  # ORM, repositories, migrations
  workers/             # durable run/ingestion dispatch
  audit/               # redacted action records
backend/tests/
frontend/src/
evals/                 # cases, fixtures, adapters, reports
infra/                 # Compose profiles and startup helpers
plans/
```

Use one backend package and shared code for API and worker processes. Keep
heavy OCR, embedding, and evaluation dependencies out of the API's startup path.
External sandbox/language dependencies must be reproducible and pinned. Reference
repository paths are not production imports or required Docker build contexts.

## Persistent records

| Record         | Required information                                                                                          |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| Workspace      | Stable ID, label, creation time; simple grouping of sources/threads                                           |
| Source         | ID, kind, version, display name, state, metadata, content hash or schema version                              |
| Connection     | MySQL/PostgreSQL type, endpoint, database, username, encrypted credential reference, description              |
| Dataset        | Source/version, sheet or table identity, typed schema, original/derived designation, lineage                  |
| Document       | Original file reference, extraction/index versions, languages, status, extraction warnings                    |
| Chunk          | Document/version, text, language/script metadata, hierarchy, neighbors, page/block location, index generation |
| Thread/Message | Conversation identity, roles, content, selected source IDs, evidence/artifact references                      |
| Run            | State, configuration/model/prompt versions, budgets, lease owner, timestamps, final/partial outcome           |
| Tool call      | Stable call ID, name, validated input reference, decision, status, result/error reference, timing             |
| Evidence       | Stable ID, type, source versions, excerpt or calculation/result reference, relevant assumptions               |
| Artifact       | ID, storage key, MIME/type, byte size, hash, producer run/tool, input lineage, durable status                 |
| Audit event    | Run/tool/source IDs, action, decision/reason code, timestamp, redacted metadata                               |

RustFS/S3 blob storage is the default for Compose, selected by the user during
phase 00. PostgreSQL stores metadata and object keys; original and derived bytes
use separate prefixes. The filesystem adapter remains a development option.

Original bytes remain immutable until explicit source deletion. Generated work
never overwrites an original. A live database result records query time and
result provenance; reproducing it requires a retained result or source snapshot.
Connection secrets stay server-side and encrypted at rest. Encryption keys,
API keys, and sandbox tokens stay outside model/tool-visible records.

## Tool input and result contract

Resolve source IDs and paths server-side within the run's selected sources.
Tool arguments cannot select arbitrary connection hosts or credential values.
Validate Pydantic schemas, IDs, path boundaries, resource limits, and SQL before
execution. Native model tool calls are the baseline interface.

Every result contains `status`, a compact model-readable summary, evidence IDs,
artifact IDs, and a typed error when relevant. Use statuses such as `ok`,
`partial`, `rejected`, and `failed`. Errors contain a stable code, retryability,
and a bounded safe message. Store large results durably; report truncation and
limits explicitly and provide an inspection operation.

Concurrent dispatch is allowed only for independent calls. SQL results required
by Python and document definitions required by a calculation must arrive first.
Serialize calls sharing a mutable sandbox session; support parallel work across
independent sessions. Infrastructure retries must not duplicate derived artifacts.

## Evidence and final answers

Document evidence includes document/version, chunk/block/page location, original
excerpt, retrieval metadata, and any translation reference. DOCX locations may
be paragraph/heading/table anchors; do not invent page numbers.

Structured evidence includes connection/dataset identity, schema version,
SQL/parameters or code reference, execution time, result artifact/hash, row
limits, units, and assumptions. Preserve exact decimals and large integers in
serialization; do not silently convert monetary values to imprecise floats.

Final answers contain text, cited evidence IDs, and artifact IDs. Validate that
references exist and belong to the run's selected inputs or derived lineage.
Render references in a source viewer and preserve them through history/export.
Checking that a citation ID exists is distinct from checking claim support.

## Runs, events, and durable jobs

Use explicit states: queued, running, awaiting clarification, completed,
failed, cancelled, and budget exhausted. Treat partial output as outcome metadata
rather than successful completion. A clarification resumes through a new run
using the thread's recorded context and artifacts.

Events have a schema version, event ID, monotonically increasing per-run
sequence, timestamp, run ID, type, and payload. Persist events before streaming.
Support replay after disconnect, duplicate-event handling in the UI, and terminal
events. Expose operational summaries and tool actions, not private model reasoning.

Claim PostgreSQL jobs using short transactions and leases. Work outside the
transaction. Heartbeats, ownership checks, bounded retries, and idempotent writes
provide at-least-once processing without stale workers finalizing a reclaimed run.
On cancellation or lease loss, stop new tool dispatch and clean up active work.

## Languages and capabilities

Use the language library's canonical BCP 47 registry. Accept aliases such as
`hi`/`en`, store canonical tags, and keep a list for mixed-language content.
Mixed language is a metadata property rather than a made-up language tag.
Keep original Unicode text separate from normalized search text. Language failure
must preserve unknown/uncertain status rather than silently labeling it English.

Record supported capabilities by language, provider, locality, configured model,
and availability. Enforce local-only/default external-provider policy before
selecting fallbacks. The explicitly configured main model endpoint is allowed;
that does not authorize unrelated cloud OCR, translation, or speech fallbacks.

## Execution controls required from first use

- SQL uses dialect-aware parsing, a function/statement policy, restricted
  credentials where available, read-only transactions, deadlines, and row limits.
  Rollback and a leading SELECT check alone are insufficient.
- Python executes through the Nexus-style microVM sandbox. Preserve application
  originals and stage working copies; provide no model/database credentials.
  Default guest networking is disabled. Never substitute local `exec`, an import
  allowlist, or a host subprocess as equivalent isolation.
- Validate uploaded file size/type and all storage/sandbox/archive paths.
- Verify artifact bytes after export into application-controlled storage.
  Sandbox artifact URIs are not assumed readable by another service or host.
- Treat retrieved text, spreadsheet cells, tool output, and database values as
  untrusted content. They cannot change execution policy or provider routing.
- Keep raw secrets out of logs, audit, traces, prompts, API responses, and frontend.

These controls protect source integrity and execution. They do not introduce
department permissions, source-sharing administration, or database topology.

## Common verification

Provide Makefile targets for format/type checks, backend tests, frontend
typecheck/build, Compose integration tests, and explicit live model/sandbox tests.
Use Python logging with run/tool/source IDs. Update API schemas, migrations,
configuration examples, and user/developer docs with each changed capability.

Critical correctness and source-protection fixtures use deterministic assertions.
Model-dependent checks record model/prompt/pipeline versions, repeated trials,
timeouts, and failures. Track which checks are skipped and why. A feature flag
must report unavailable functionality rather than accepting an option as a no-op.

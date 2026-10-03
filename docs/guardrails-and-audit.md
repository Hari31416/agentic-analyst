# Guardrails and run audit

Phase 08 adds execution policy `execution-policy-v1` and prompt `analyst-v6`.
The model proposes actions; application code decides whether they can execute.
Source text, OCR, database values, cells, tool errors, and imported metadata
cannot change the configured tools, providers, selected inputs, or guest network.

## Execution decisions

`PolicyDecision` records action, outcome (`allow`, `reject`, or `clarify`), stable
reason code, and policy version. Tool dispatch checks the fixed tool catalog,
selected source/dataset IDs, and configured credential values before starting
execution. Tool-specific validators then check source versions, selected table
acceptance, SQL, analysis inputs, artifact lineage, and output paths. An allowed
dispatch is permission to attempt an action, not a claim that its result succeeded.

Audit rows retain their operational states and a typed policy snapshot. Failed or
cancelled actions map to `reject`; clarification answers map to `clarify`.
`unknown_tool`, `configured_secret_detected`, `source_not_selected`, and
`dataset_not_selected` identify dispatch rejections. Upload and source worker
audits include source versions, extractor/chunker/pipeline versions, failures,
partial indexing, and cancellation. Existing run ownership, lease, budget and
cancellation checks prevent a stale worker from publishing a late success.

## Tested boundaries

| Boundary                    | Control and evidence                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SQL                         | Dialect-specific AST validation, scoped CTE resolution, fixed function allowlist, selected datasets, bounded query structure and output. Real database read-only transactions, timeouts, cancellation and cleanup provide another enforcement layer.                                                                                                                                                                                                       |
| SQL bypasses                | Tests reject write/nested CTEs, multiple statements, qualified functions, hidden/system tables, file sinks and MySQL executable comments. A deliberately bypassed AST validator still cannot UPDATE either live source database.                                                                                                                                                                                                                           |
| Guest networking            | Actual microVM TCP attempts to a public address and the metadata address fail. Configured credential environment names are absent.                                                                                                                                                                                                                                                                                                                         |
| Guest originals and outputs | Inputs are guest working copies; changing one leaves the application original hash/content unchanged. Output paths and counts/bytes are bounded. A symlink to `/etc/passwd` fails output collection and exports no artifact.                                                                                                                                                                                                                               |
| Timeout and teardown        | A file created before timeout remains available. A lost stop response is reconciled through idempotent DELETE and a fresh status check; an active or unknown session leaves cleanup unconfirmed. Execution is never repeated to recover a lost response.                                                                                                                                                                                                   |
| Ingestion and crawl         | Upload/parser/expansion limits, duplicate/noncanonical XLSX archive rejection, traversal/symlink rejection for portable archives, exact crawl allowlists, public DNS resolution and pinned addresses, and redirect revalidation. Environment proxies cannot redirect crawls.                                                                                                                                                                               |
| Rendering and downloads     | Model/guest HTML and SVG cannot become executable previews; downloads use content-type protection and sandbox headers. CSV/XLSX exports neutralize spreadsheet formulas.                                                                                                                                                                                                                                                                                   |
| Secrets                     | Known configured SecretStr values of at least eight characters are removed from prompts, persisted tool results, events, audit, logs and answer responses. Guest output and public artifact downloads containing those values are refused. Console redaction precedes display clipping. Upload/message checks refuse known plaintext credentials. Portable exports refuse credential-bearing assets or metadata rather than changing their hashes/content. |

Secret handling is a defense against configured plaintext values and sensitive
field names, not a general data-loss detector. Encoded, transformed, compressed,
unknown, or short credential values are not universally detectable. Normal
source content remains untrusted even when no credential or injection pattern is
recognized. A diagnostic fixture match never grants additional capabilities.

## Resource limits still needing work

The real guest reports one CPU and about 481 MiB of memory. Requested memory is
1 GiB; effective memory enforcement was not verified by an allocation/OOM test.
The service's disk setting limits an OCI overlay, while `/workspace` uses a
separate host-backed bind volume without an application quota. The guest sees a
4 GiB workspace filesystem despite a requested 2 GiB disk limit. Application
console/artifact byte caps do not bound every guest disk write. CPU/memory/disk
stress containment therefore remains a deployment limitation, documented rather
than claimed as passed. Execution deadlines and output bounds were exercised.

The symlink probe initially rejected output but lost the stop response. Its
initial failure remains in the report. After cleanup recovery was added, the
focused retest returned status 404 and catalog inspection found no extra guest
from that probe. An unrelated active session was left in place.

## Inspecting a run

The chat's **Run audit** action opens action/state filters and links to retained
artifacts and citations. Backend contracts are:

- `GET /api/runs/{run_id}/audit?action=tool.dispatch&state=rejected&cursor=0&limit=50`
- `GET /api/runs/{run_id}/audit/export`

The JSON export uses schema version 1. It retains the question, messages, selected
source versions, run/model/prompt/pipeline configuration, tool decisions and
arguments, SQL/code where recorded, results, evidence, artifact lineage, events,
source processing audits, errors and cancellation. Valid declared references to
prior runs in the same thread are included with their producer IDs. The export
has per-entry depth/content bounds and a 2 MiB ceiling, with explicit truncation
metadata. Large results and code artifacts remain separate retained objects.
Historical runs are marked when policy versions or arguments were not recorded;
the exporter does not invent a historical policy decision.

## Answer checks

Invalid/nonexistent evidence, wrong reference kind, another thread, unselected
sources, or incompatible source versions block answer completion. Additional
checks produce visible warnings for numbers absent from retained structured
previews, missing recorded units, unqualified partial results, and creation
claims without declared artifacts. These checks inspect bounded previews and
simple English/Hindi phrases. They can miss errors or warn about valid derived
numbers; they cannot prove semantic correctness. Warnings preserve the answer
and its supporting links for review.

## Validation and phase 09 gates

Retained reports:

- `evals/reports/phase08-sql-live-2026-10-03.json`
- `evals/reports/phase08-sandbox-2026-10-03.json`
- `evals/reports/phase08-integration-2026-10-03.json`
- `evals/reports/phase08-audit-run-6d1d074b-2026-10-03.json`
- `evals/reports/phase08-injection-2026-10-03.json`
- `evals/reports/phase08-http-2026-10-03.json`

The injection diagnostic combines six synthetic English/Hindi excerpts in one
bounded model trial, with no execution capabilities. It is not six independent
trials or a full runtime attack benchmark. Separate deterministic release checks
exercise fixed capabilities, selected inputs, configured secret sentinels,
invalid references, audit/export bounds, archive safety and cleanup ambiguity.
Real SQL and guest probes exercise the actual execution boundaries.

Phase 09 should retain these negative tests as release gates, repeat milestone
SQL/sandbox checks after changes to those boundaries, and measure autonomous
mixed-source task success. Resource stress/quota verification and broad browser
QA remain explicit gaps. RBAC, SSO, tamper-evident infrastructure and compliance
certification remain outside this phase.

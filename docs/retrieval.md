# Retrieval and conversation context

Chat offers Basic and Advanced retrieval. Basic remains the default. It runs the
phase 03 hybrid pipeline once. Advanced searches the original query and up to two
literal keyword variants, combines ranks, balances document coverage, and expands
parent/neighbor passages. Source selection and version checks apply to every stage.

The synthetic four-document comparison found that multi-query alone did not
improve recall at 3 and reduced English recall at 5 from 1.00 to 0.75. Document
coverage improved English recall at 3 from 0.50 to 0.75. Hindi recall at 3 stayed
0.50. Keep Basic as the general default until a broader corpus supports a change.
The optional multilingual reranker improved Hindi recall at 5 from 0.75 to 1.00,
with about 300 ms warm local inference in this fixture. It remains opt-in.
These are supporting-passage measures, not a claim-support guarantee.

## Stages and budgets

`search_documents` accepts explicit variants, independent `subquestions`, optional
`rerank`, `consensus`, `compress`, and `expand_context`. An explicit advanced option
activates the advanced pipeline even when the run uses Basic. `multi_query=false`
disables automatic variants inside an advanced search. The advanced pipeline caps
queries at three, candidate passages at 60, reranking at 20, and parent/neighbor
reads at three per chosen passage. Main-agent calls and elapsed time retain their
existing run limits. Internal query rewriting, compression, and summaries make
zero chat-model calls.

Rewriting adds the previous user question to a referential follow-up variant. It
never replaces the original query. Keyword variants copy the user's words; the
original always runs first. Deployment-owned aliases use `RETRIEVAL_ALIASES`.
Invalid supplied transformations fall back to the original query. No automatic
translation or inferred entity mapping is used.

`hop_evidence_ids` and `hop_terms` express dependent hops. The terms must occur
verbatim in prior document evidence from this thread and the currently selected
source versions. The agent first retrieves an identifier or definition, then
uses it in a later search. Independent subquestions are labelled separately in
the trace. No helper starts another autonomous agent.

Context expansion emits separate original passages with their own chunk, source,
version, location, and evidence ID. Deduplication uses chunk IDs and preserves
distinct source versions. Extractive compression copies matching sentences and
keeps both the original excerpt and its source reference. Character budgets and
a conservative UTF-8-byte token bound count original and compressed text together.
The trace reports truncation and partial results. This bound applies to passage
text; the outer context/result guards also count serialized tool metadata.

## Local reranking

`make reranker-model` downloads a pinned int8 Jina multilingual model into ignored
`data/models/jina-reranker-v2`, writes checksums and updates local `.env`. It needs
about 300 MB of downloaded assets. Runtime inference verifies the local manifest
and never downloads or sends source content to another provider.

The chosen public model is `jinaai/jina-reranker-v2-base-multilingual`, revision
`9cfeff2df7d40d1b78e75e5e9cebec92a99813c9`, with CC-BY-NC-4.0 terms. Deployments must
choose assets whose license permits their use. The adapter also accepts pinned
BAAI base assets, whose documented language scope is English and Chinese.
`RERANKER_MODEL`, `RERANKER_MODEL_PATH`, and `RERANKER_REVISION` configure local
assets. Missing files, checksum failures, model-load failures, and invalid scores
return an explicit unavailable stage and retain the fused evidence.

The adapter uses the installed FastEmbed cross encoder's actual pair scoring,
not a keyword score. Supported API reference:
[FastEmbed rerankers](https://qdrant.tech/documentation/fastembed/fastembed-rerankers/).

## Summaries and history

`summarize_documents` returns cached document, section, or selected-corpus overview
summaries. Optional thematic summaries group structural headings. The default is
extractive, with zero chat-model calls/tokens/cost. Neural clustering and generative
map-reduce are not enabled. Each selected summary sentence has an original
supporting excerpt and evidence ID; summary prose itself is never primary evidence.

The summary cache uses migration `a8c204d39f51`. Fingerprints include source/hash,
source version, extraction/chunking versions, sampled chunk hashes, scope, section,
algorithm/prompt/model versions and bounds. Long documents use a bounded sample;
coverage and incomplete results are explicit. A concurrent duplicate cache write
reuses the committed row. Archival removal excludes documents from new summaries.

Conversation compaction creates a temporary model view and never changes durable
messages. User turns, corrections, source/dataset selections, source versions and
evidence/artifact IDs are retained. A sentence detector reserves definitions,
assumptions and units across assistant turns before allocating space to recent
prose. It is conservative and cannot recognize every possible definition. If
required material exceeds the bound, the run reports budget exhaustion. Ordinary
prose may be omitted, with counts in the `context_selected` event. Retained
artifacts remain inspectable and can be staged in a later guest after expiry.

## Inspection and checks

Each saved citation has its full retrieval trace. Chat answers expose collapsed
Retrieval details, backed by `GET /api/runs/{run_id}/retrieval`; this route also
returns empty and failed searches with no citations. Runs capture the profile,
pipeline version, alias settings and candidate limits.

Run deterministic tests and PostgreSQL integration checks for changes. The saved
milestone is `evals/reports/phase05-2026-10-03.json`. `make live-retrieval` repeats
local ablations and four configured-model trials. Use
`python -m app.advanced_probes --retrieval-only` from the backend to refresh local
measurements while retaining previous live results. The report records model
usage, latency, stages, supporting recall, and source integrity. Full browser QA
remains deferred by the user.

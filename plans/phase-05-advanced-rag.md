# Phase 05: advanced retrieval, reasoning, and summaries

Prerequisite: phase 04. Read advanced-rag multi-query, multi-hop, context, and summaries.
Outcome: the requested advanced RAG capabilities work behind the existing tools.

## Build in order

1. Add conversational query rewriting and optional domain keyword/alias expansion.
   Preserve entity names, identifiers, dates, and numbers. Keep original queries
   and transformations in the retrieval trace. Fall back to the original query
   on invalid expansion instead of changing user intent.
2. Implement bounded multi-query retrieval, rank fusion, deduplication/diversity,
   and optional consensus boosting. Keep per-variant provenance and coordinate
   total candidate/model budgets with the outer agent run.
3. Implement a real multilingual reranker adapter over bounded candidates. Record
   model/version, reranking status, latency, and candidate ranks. Requested but
   unavailable reranking returns an explicit status; it must not be a silent no-op.
4. Improve context assembly with parent/neighbor expansion, duplicate removal,
   token budgets, per-document coverage, and optional contextual compression.
   Any compressed passage links to its original evidence and cannot replace the
   retained source excerpt. Select contexts without reading every document into RAM.
5. Support independent subquestions and true dependent hops. The main agent can
   search again using discovered entities/definitions. If a retrieval helper
   performs internal decomposition, keep it bounded and expose supporting evidence
   and gaps rather than creating another autonomous supervisor or agent hierarchy.
6. Add versioned, cached section/document summaries, overview retrieval, and
   optional thematic clustering/hierarchical map-reduce summaries. Preserve
   supporting chunk references. Summary generation cost is explicit and invalidated
   when source/extraction/prompt/model versions change.
7. Add thread context selection/compaction preserving user corrections, selected
   sources, definitions, assumptions, units, and evidence/artifact IDs. Keep durable
   messages separate from a compact model-context view; historical artifact use
   must work after sandbox expiry.
8. Add inspectable retrieval traces and simple basic/advanced profiles. Keep
   expert tuning out of the main chat flow. Capture settings in every run.
9. Add fixtures needing a second evidence-dependent hop, multiple documents,
   conflicting versions, overview answers, and follow-up references.

## Acceptance and validation

- [ ] Multi-query, reranking, context expansion, summaries, and independent/
  dependent-hop cases execute and report which stages actually ran.
- [ ] A dependent-hop case uses first-hop evidence to locate the next passage.
- [ ] Original citations remain valid after fusion, compression, and synthesis.
- [ ] Follow-up conversation preserves definitions and uses earlier artifacts
  without requiring a live sandbox session.
- [ ] Summary caching invalidates correctly and no unreferenced summary is treated
  as primary evidence for a factual or numerical claim.
- [ ] Compare each optional stage separately against phase 03 retrieval metrics,
  answer quality, model calls, tokens, latency, and resource use.
- [ ] Choose defaults from measured benefit; capabilities remain available even
  when an expensive stage is off in the baseline profile.
- [ ] Failure/budget exhaustion yields explicit partial results and recoverable
  evidence rather than fabricated completion.

Run deterministic pipeline tests and repeated live cases. Record all failed and
timed-out trials. Phase 09 formalizes the broader benchmark and release gates.

## Handoff

Record stage interfaces, available profiles, measured default choices, prompt/
model versions, summary costs, compaction behavior, and multi-hop limitations.

# Phase 09: evaluation, model profiles, and quality gates

Prerequisite: phase 08. Reuse and expand the fixtures and measurements from earlier phases.
Outcome: repeatable component/workflow evaluation with an inspectable quality report.

## Build in order

1. Formalize the versioned evaluation case schema: question, language, source
   versions/hashes, expected calculations/passages/artifacts, answerability,
   allowed actions, rubric, labels/review provenance, and tolerances. Cover
   document-only, data-only, mixed-source, OCR/tables, conversations, Hindi,
   English, code-switching, ambiguity, unsupported questions, and adversarial cases.
2. Build an isolated evaluation CLI using the same public application APIs and
   real tools as normal runs. Support case/tag filters, repeats, bounded concurrency,
   timeouts, checkpoint/resume, and explicit fresh runs. Include failures in the
   report; distinguish infrastructure failure, model error, and incorrect answer.
3. Add deterministic component metrics: OCR CER/WER and table accuracy; retrieval
   recall/precision/ranking by labeled sources; SQL execution result correctness;
   numeric/unit consistency; artifact existence/schema; source immutability and
   prohibited action rejection. Compare SQL results, not exact SQL strings.
4. Integrate RAGAS as an optional evaluation dependency for context relevance,
   retrieval/answer metrics, and claim support where appropriate. Configure judge
   and embedding providers explicitly and use the installed supported API.
   Keep this integration out of production request handling. DeepEval is an
   optional alternative adapter; installing both is not a completion requirement.
5. Add human-review export/import of answer rubrics and judge disagreements.
   Record label provenance. Calibrate any judge on reviewed Hindi/English cases;
   self-judging by the same small model is supplementary evidence, not a replacement
   for known answers or human review. Report uncalibrated scores as such.
6. Implement experiment identity and cache invalidation using case/fixture hashes,
   model/endpoint identity, prompts, policies, parser/chunker/index versions,
   retrieval settings, judge/embedding settings, and repetitions. Store redacted
   per-run outcomes with evidence and artifact links, never API keys.
7. Run ablations: lexical/dense/hybrid, reranking, multi-query, dependent hops,
   summaries/context expansion, and compaction. Compare candidate local models
   and tool-call formats without changing the one-agent architecture. Use at
   least three trials per model-dependent benchmark case in the release comparison.
8. Report accuracy/grounding, language quality, completion/repair rates, latency
   percentiles, token/model calls, ingestion/query time, peak resources, and
   failures. Export JSON/CSV and readable reports; add a minimal results viewer.
9. Establish quality gates from reviewed fixture expectations and measured
   baseline. Critical deterministic source-protection tests and golden calculations
   must pass. Define retrieval/answer thresholds with dataset size and review
   status, rather than presenting a universal score as production quality.

## Acceptance and validation

- [ ] Evaluation runs are reproducible and identify every relevant version/configuration.
- [ ] Known-answer cases fail when calculations/citations/results are deliberately wrong.
- [ ] Runner timeout/failure/restart behavior retains failed cases and avoids silent skips.
- [ ] RAGAS metrics run against explicitly configured providers and disclose required inputs.
- [ ] Hindi/English metrics are reported separately; label/judge review status is visible.
- [ ] Repeated trials and stage ablations produce comparable reports with resource use.
- [ ] Critical regression checks are integrated into CI/test targets; expensive live
  benchmarks have separate explicit targets and reports.
- [ ] Model/judge unavailability yields integration-pending status, not fabricated scores.
- [ ] Configured local-only evaluation disables telemetry/hosted reporting and
  unexpected network calls; the chosen model endpoint remains an explicit exception.
- [ ] Failed release gates remain visible and actionable with supporting traces.

Human review may require user input. Complete the runner, deterministic gates,
and available live benchmarks first; record remaining review/calibration separately.
Synthetic labels are useful but cannot be described as client-validated quality.

## Handoff

Record benchmark case inventory, metric adapters, review provenance, live trial
results, chosen model/retrieval defaults, and release thresholds/limitations.

# Real-source evaluation pack v1

This pack contains 30 cases based on the UCI Online Retail workbook and the
Government of India's Economic Survey 2023–24. It covers structured aggregation,
row filtering, invoice-versus-line grain, distinct counts, missing values,
cancellation handling, grouped ranking, mixed document/data reasoning, CSV and
chart artifacts, English/Hindi/code-switched requests, cross-language document
retrieval, table finding, ambiguity, unsupported information, and causal
overstatement.

## Ground-truth revision of 6 October 2026

This revision preserves the 30-case inventory and verified retail gold, and adds
complete reference claims, source locations, partial-credit criteria and full
22-country CSV expectations in the generated case rubrics. Labels remain
`unreviewed`; source-backed agent verification is not independent human or
native-speaker certification.

The action allowlist remains an expected tool-appropriateness policy. The agent
receives its ordinary tools and must decide which are appropriate to the query;
using every allowed tool is not required. `register_dataset` and `generate_report`
remain excluded because these tasks do not request derived-source registration
or reports. Tool appropriateness is recorded separately from factual correctness.

Survey passage anchors are diagnostic. A declared citation from the correct
selected source/version can receive `needs_review` when it uses different wording;
missing, undeclared or wrong-source/version citations still fail. Accepted anchor
alternatives are pinned to original source text. Anchor presence does not certify
that the answer's claims follow from that passage.

Numeric prose checks support Devanagari digits, comma grouping and protected
Western/Indian grouping spaces. GBP/INR/EUR codes and their unambiguous symbols
are accepted. Structured units still require explicit unit metadata. Numeric
checks prefer declared calculation evidence when tool/evidence links are available;
legacy observations use the latest successful SQL/analysis result. Python remains
available for supporting work, but numeric questions require the final named
SQL/analysis row. Currency, percentage and count tolerances are explicit.

The changed questions require fresh trials: `retail-jan-sales-en`,
`retail-customer-null-en`, `retail-sales-chart-csv`,
`retail-missing-cost-unsupported`, `retail-missing-customer-sales`,
`retail-export-and-chart-hi`, `retail-scope-clarification`,
`survey-inflation-drivers-en`, `survey-inflation-policy-measures-en`,
`survey-inflation-hi-source`, `survey-appendix-table-retrieval`, and
`survey-causal-overclaim`. The unsupported/scope/causality questions no longer
supply the expected decision. The missing-customer sales question now requests
`missing_customer_sales_percent`, expected at 17.63% to two decimal places.
The runner refuses to rescore old observations under changed execution questions.
Historical runs and AI reviews remain unchanged. Scoring-only corrections can be
applied to unchanged questions in a separately identified rescore.

## Inputs and generation

The seven public originals are stored under the ignored
`evals/fixtures/real-v1/raw/` directory. Their URLs, extracted archive member,
byte sizes, and SHA-256 hashes are pinned in `evals/fixtures/real-v1/downloads.json`.
The generator checks every local original against that manifest before reading
it. Use the downloader to fetch or verify the local originals, then regenerate
the row slice, rule note, case inventory, and gold manifest:

```sh
python3 evals/generators/download_real_v1.py
python3 evals/generators/download_real_v1.py --verify
cd backend
uv run python ../evals/generators/generate_real_v1.py
```

The generator keeps every January 2011 row from the workbook in an ignored CSV
slice; it does not pre-filter cancellations, returns, null customers, or other
records. The separate authored rules file defines a qualifying sale as an
invoice line whose invoice number does not begin with `C`, whose quantity is
positive, and whose unit price is positive. The amount is `Quantity × UnitPrice`
in GBP. These are explicit evaluation rules, not accounting guidance attributed
to UCI.

Expected retail values are derived from the original workbook with Python
`Decimal` values and written to `evals/fixtures/real-v1/manifest.json`. Independent
verification with integer-price SQLite calculations confirmed 35,147 source
rows, 34,306 qualifying lines, GBP 691,364.56 qualifying sales, 387,785 units,
1,086 distinct invoices, 22 countries, 741 non-missing customers, and 13,077
qualifying lines with a missing CustomerID. The largest qualifying sales total
is the United Kingdom at GBP 561,289.98; 701 raw rows are cancellations. The
generator repeats these calculations on every regeneration.

Case source versions are `1`, matching each trial's freshly uploaded API
source. `real-v1` identifies the evaluation pack in its manifest and is not an
application source version.

`survey-hindi-source-search-en` uses the 18-page Hindi inflation chapter and
asks for an English answer with a chapter PDF page citation. This retains
Hindi-source OCR and cross-language retrieval coverage within the 100-page OCR
budget. The full Hindi Survey remains downloaded but is not selected by this
case. This case's source and question changed; use a fresh trial rather than
rescoring or resuming its earlier full-report execution.

## Running cases

Start the API, worker, sandbox, and required infrastructure as described in the
project setup. Live runs also require a regular application account. Create one
through the admin **Users** screen and set its username and password as
`EVAL_USERNAME` and `EVAL_PASSWORD` in the repository's ignored `.env`. The
runner reads these settings on startup; no service restart is needed. A configured
admin account also works when `EVAL_USERNAME` and `EVAL_PASSWORD` identify it. See
[authentication setup](../docs/authentication.md#evaluation-and-validation).

List and schema-check the pack without model calls:

```sh
cd backend
uv run python -m evaluation.cli list --cases ../evals/cases/real-v1.json
```

Run a small deterministic selection first, then the full pack when the upload
and retrieval paths are ready:

```sh
uv run python -m evaluation.cli run --live \
  --cases ../evals/cases/real-v1.json \
  --case retail-jan-sales-en --repeats 1 \
  --output ../evals/runs/real-v1-smoke

uv run python -m evaluation.cli run --live \
  --cases ../evals/cases/real-v1.json \
  --repeats 1 --output ../evals/runs/real-v1
```

### Parallel model comparison

Set `WORKER_CONCURRENCY=4` and restart the worker. Its supervisor starts four
independent host processes, each using the durable PostgreSQL queue. This isolates
native PDF/OCR state and allows four application queries to execute at once.
`--concurrency 4` bounds the runner's in-flight trials; setting it alone does not
increase worker capacity. Leave capacity at one on a resource-constrained host.

Add the exact candidate IDs to `OPENAI_ALLOWED_MODELS` as a JSON list in `.env`,
then restart the API. All candidates use the existing configured endpoint/key.
Run the five Krutrim candidates with one trial per case:

```sh
cd backend
uv run python -m evaluation.cli run-matrix --live \
  --cases ../evals/cases/real-v1.json --repeats 1 --concurrency 4 \
  --timeout 900 --output ../evals/runs/real-v1-krutrim-matrix \
  --model gpt-oss-120b --model gemma-4-31b-it \
  --model gemma-4-26B-A4B-it --model Qwen3.5-9B \
  --model Qwen3.6-35B-A3B
```

The matrix runs models sequentially, with four trials per model in flight, so
five models do not multiply the concurrency bound. Each model has its own hashed
folder, checkpoint and JSON/CSV/HTML reports. `matrix.json` links the model IDs to
these folders and aggregates automatic results. Use the same command with
`--resume` after interruption; completed successes and failures are retained.
`run --model MODEL_ID` selects one configured candidate. The API snapshots the
model ID into the run, includes it in idempotency checks, and the worker uses that
snapshot. Runner and worker concurrency are part of experiment identity.

Reports retain tool order, decisions, status and error counts, per-tool durations,
model attempts/responses, provider failures and retryability, response durations,
usage counters and their coverage, queue/query/ingestion timing, and bounded
response/validation diagnostics. Existing observations retain redacted tool inputs,
results and evidence. Operational telemetry excludes prompt text, submitted
argument values and private reasoning. Reported tool attempts include finalization
and attempts rejected before runtime dispatch; retained tool execution rows can
therefore be fewer than attempted calls. Aggregate outcome usage does not prove
per-attempt coverage. Provider failures without usage remain
unavailable; audit truncation and missing counters must be considered when
interpreting totals. Timing at concurrency four measures throughput under shared
host/provider load and is not directly comparable to single-query latency.

One repetition is exploratory. Automatic statuses still require the manual
reviews described below, and this run does not establish a stable model ranking.

The normal runner supports 1–20 repetitions. A repetition of three is useful
for selected comparisons, but increases cost and still does not make these
unreviewed labels a quality certification. Keep original failures and review
the report's per-case metrics, evidence, artifacts, latency, and missing usage
data. The standard runner accepts only loopback API URLs and requires the
configured evaluation account.

Credentials and supported answer languages are checked before the runner writes
a checkpoint. Use `--fresh` or a different output directory when experiment
inputs change.

The unsupported-data cases distinguish formal clarification events from prose.
An explicit clarification event passes the contract. A refusal or clarification
written only in prose remains `needs_review`; the runner does not use language
patterns to certify abstention. Incorrect calculations, citations, source
integrity, and prohibited actions remain deterministic failures.

## How to evaluate

The structured retail checks compare calculation results returned by `run_sql`
or `analyze_data` with independent Decimal gold values. Questions name the
expected output column so the existing scorer can find it. Exact counts use zero
tolerance; currency amounts and rounded percentages use 0.01. Inspect the question rubric for the
checks that need a human: top-country names and ordering, the top-five values,
null denominator, chart contents, CSV row correctness, and the quality of an
answer to an ambiguous or unsupported request.

For documents, the current runner can check selected-source citations against a
verified phrase. English phrases in this pack were checked against the local
PDF text extraction. It does not prove that a cited passage supports the whole
answer, validate page numbers in prose, or score numeric claims in document
answers as a structured calculation. Review those against the indicated source
passage and page. Recheck the complete list or comparison when a question asks
for more than the matched phrase.

The runner requires structured units in SQL/analysis evidence to score a unit.
Writing `GBP` in the answer does not supply unit metadata to that check. A
correct numeric result can therefore remain `needs_review` on the structured
unit check. The answer's visible number and unit are checked separately, but
presence alone does not establish correct reasoning.

## Coverage limits

All cases currently have `unreviewed` label provenance. Retail calculations
are reproducible and independently cross-checked; English source phrases are
verified excerpts, not a full human-reviewed document benchmark. Hindi and
code-switched language quality requires a native-speaker review. The Hindi PDFs
are visually legible, but embedded text extraction contains legacy-glyph
garbling. Their retrieval cases are descriptive and require a visual check of
the rendered page and citation.

The current case schema cannot represent multi-turn conversations. Prompt
language and requested answer language are separate fields when they differ;
live requests must use a configured canonical answer language. A prompt written
in Romanized Hindi can therefore retain its `hi-Latn-IN` input label while
requesting a `hi-IN` answer. Cases with a rubric `manual_review` note remain
`needs_review` even when deterministic checks pass. The CLI emits bounded JSON
stage/status progress events and periodic heartbeats; HTTP diagnostics retain
safe error codes and validation field names, never submitted values or response
bodies. The scorer cannot automatically compare grouped per-country top-k values,
clarification wording, table cell accuracy, citation page numbers, or complete
prose factuality. OCR measurements are descriptive. There is no calibrated
judge, broad ablation, or claim of a complete quality benchmark. The Economic
Survey is a Government of India publication; the UCI dataset attribution and
all source URLs/hashes are in the generated manifest. Check each source page's
reuse terms before redistributing the original files.

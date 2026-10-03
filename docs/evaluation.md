# Evaluation baseline

The phase 09 first pass lives in `backend/evaluation`, outside production request
handling. It runs fixture-backed trials through the public workspace, upload,
chat, audit, artifact and portability APIs. It does not execute a separate mock
analyst or query application tables directly.

## Run it

Start infrastructure and the host API, worker and sandbox as usual. List cases
without model calls:

```sh
make eval-list
make eval-check
```

Run the configured model explicitly, with the default six-trial EN/HI baseline:

```sh
make eval-live
```

For one bounded case, from `backend`:

```sh
uv run python -m evaluation.cli run --live --case mixed-en \
  --timeout 240 --output ../evals/runs/mixed
```

Useful options are `--case` and `--tag` filters, `--repeats 1..20`,
`--concurrency 1..4`, `--timeout 1..1800`, and `--profile basic|advanced`.
Concurrency controls runner trials; the application worker may still serialize
execution. `--strict` exits 1 when synthetic deterministic gates fail. Ordinary
runs write failures and return a report without demanding a perfect model answer.
Invalid setup/inputs return exit 2.

Reports include `report.json`, formula-safe `trials.csv`, and a standalone
`index.html` viewer. Open the HTML locally; it has no external scripts or CDNs.
It shows cases, trial status, answers, metrics, review provenance and links back
to application runs/artifacts. API links require that host API to be available.
Local checkpoints/traces under `evals/runs` are ignored by Git. Retain selected
redacted benchmark reports under `evals/reports`.

## Cases and scoring

`evals/cases/core-v1.json` has schema-1 cases with question/language, source aliases,
fixture paths/SHA-256/version, answerability, allowed tools, numeric tolerance,
expected original passages, artifact expectations, rubric and label provenance.
The first inventory covers CSV/XLSX calculation, English/Hindi documents,
mixed-source evidence, Hindi ambiguity, unsupported questions and hostile source
text. An OCR case is descriptive and is not in the default repeat baseline.
Conversation and broader OCR/table workflows remain future inventory work.
All labels are synthetic developer expectations, not client or native-speaker
quality validation.

Known-answer metrics compare structured results, not SQL strings or model prose.
They use the latest successful SQL/analysis result, avoiding inspection samples
and earlier calculations. Numeric expectations use Decimal tolerances. Unit
hints come from explicit result/source evidence; absent hints require review,
while conflicting hints fail. Answer-number/unit presence is checked separately
and cannot prove that the prose explains the correct calculation.

Passage checks require the expected selected source and version and a declared
citation containing the normalized phrase. Artifacts must be declared and
available through the public download API; an optional schema subset checks
JSON outputs. Original-source hashes are recomputed from the public portable
archive, then compared with the uploaded fixture hash. Tool allowlists and
required clarification are deterministic checks. Unknown/missing observations
remain review items or failed checks, not fabricated scores.

CER/WER, aligned-table accuracy and retrieval precision/recall/MRR helpers are
available for component tests. They do not automatically create a full OCR or
retrieval benchmark from unlabeled end-to-end runs. CER counts Unicode code
points, WER splits whitespace, and bounded edit-distance inputs prevent runaway
quadratic work. These definitions are limited for Hindi graphemes and tokenization.

A trial that passes synthetic deterministic expectations is not a reviewed
production-quality answer. Reports retain label provenance and uncalibrated
judge status. Hindi and English cohort counts, metric failures, latency p50/p95,
token/model calls and coverage are reported separately. Missing token/call data
stays unavailable. A missing verification metric leaves the trial `needs_review`;
a completion or action-allowlist pass cannot hide it. Synthetic label provenance
is the explicit exception, and remains visible even when deterministic checks pass. Peak RSS describes the runner process lifetime, excluding API,
worker and microVM resource use. It is not a deployment-capacity measurement.

## Checkpoint and identity

Each trial has its own workspace and thread, keeping fixtures and selected sources
separate from normal workspaces. These retained workspaces use an `eval:` label;
there is no automatic deletion of run traces or partial outputs.

Before API mutations, checkpoint state records the attempt. Before run submission,
it persists the application's request ID. Checkpoints use atomic replacement and
a single-process directory lock. A resumed in-flight run is observed by its ID;
the runner does not repeat execution to recover an uncertain response. An
interruption during workspace/upload/thread creation can leave an unknown
resource ID, which is retained as an infrastructure failure rather than retried.

Use the same options with `--resume` to observe unfinished trials and reuse every
finished result, including failures. Use `--fresh` or another output directory
for new trials. A fresh run archives the previous checkpoint before replacing it.
Timeout requests cancellation and remains a timeout trial even if cleanup later
succeeds. Application run/audit links preserve the server-side trace.

Identity includes case and fixture hashes, configured model/endpoint fingerprints,
API identity, application/evaluation code hash, dependency lock, prompt/policy,
extractor/chunker and retrieval versions/settings, embedding/reranker settings,
OCR/profile, budgets, guest image, repeats and timeout. API and model URLs reject
userinfo/query credentials. The first runner accepts only a loopback API and
uses no environment proxies or redirects. The configured model endpoint is the
explicit provider exception. No RAGAS/hosted reporting/telemetry package is loaded by the runner.
Actual run config and document/index generations accompany each trial; key model,
prompt, policy and retrieval profile mismatches fail as infrastructure errors.
A changed experiment identity refuses checkpoint reuse. Endpoint-side model
weights/build changes still require an operator-supplied model version or fresh
experiment; a model name alone cannot identify invisible provider changes.

Saved observations can be rescored after a metric or synthetic expectation fix,
without another model call:

```sh
uv run python -m evaluation.cli rescore ../evals/runs/baseline/report.json \
  --output ../evals/runs/rescored
```

Rescoring retains the original execution identity, adds a separate scoring code/
case identity, and refuses changes to questions, languages, sources or
answerability. Use a fresh trial when execution inputs change. Original trial
reports remain available for comparing old scoring with corrected scoring.


## Human review

From `backend`, create a template bound to the experiment, case, repetition and
answer hash:

```sh
uv run python -m evaluation.cli review-export ../evals/runs/baseline/report.json \
  --output ../evals/runs/baseline/review.json
```

Fill reviewer identity, provenance, rating and comments, then import it:

```sh
uv run python -m evaluation.cli review-import ../evals/runs/baseline/report.json \
  ../evals/runs/baseline/review.json --output ../evals/runs/reviewed
```

Imports reject changed answers, foreign experiments, duplicate/unknown trials,
malformed labels and oversized files. Review annotations remain separate from
automatic metric results; they cannot silently turn a failed calculation into a
passing regression check. This is a local annotation workflow, not reviewer
identity authentication or signed attestations.

## Deferred work

RAGAS and judge/embedding provider adapters, Hindi/English judge calibration,
multi-model/tool-format comparisons, fine-grained retrieval stage ablations,
conversation cases, production retrieval thresholds and full resource profiling
are deferred by the user's request to prioritize the core first pass. No judge
scores are invented. Basic remains the default; the runner can record Basic vs
Advanced experiments, but that is not isolation of every retrieval stage.

`make eval-check` is the deterministic release target. It includes golden fixture
checks, SQL/ingestion and local cancellation protection, credential sentinels, deliberate
wrong-result/citation tests, runner failures/resume, and review/report safety.
Database worker lease/cancellation checks remain under the service-backed
`make test-integration` target. Live benchmarks remain a separate explicit target. Three repetitions satisfy the
trial-count requirement for a chosen case; they do not establish universal release
thresholds. Preserve all misses and expand reviewed cases before setting those
thresholds or comparing candidate models.

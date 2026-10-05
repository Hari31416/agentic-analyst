# Real-source evaluation pack v1

This pack contains 30 cases based on the UCI Online Retail workbook and the
Government of India's Economic Survey 2023–24. It covers structured aggregation,
row filtering, invoice-versus-line grain, distinct counts, missing values,
cancellation handling, grouped ranking, mixed document/data reasoning, CSV and
chart artifacts, English/Hindi/code-switched requests, cross-language document
retrieval, table finding, ambiguity, unsupported information, and causal
overstatement.

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

## Running cases

Start the API, worker, sandbox, and required infrastructure as described in the
project setup. Live runs also require a regular application account. Create one
through the admin **Users** screen and set its username and password as
`EVAL_USERNAME` and `EVAL_PASSWORD` in the repository's ignored `.env`. The
runner reads these settings on startup; no service restart is needed. See
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

The normal runner supports 1–20 repetitions. A repetition of three is useful
for selected comparisons, but increases cost and still does not make these
unreviewed labels a quality certification. Keep original failures and review
the report's per-case metrics, evidence, artifacts, latency, and missing usage
data. The standard runner accepts only loopback API URLs and requires the
configured evaluation account.

If an attempt fails during login, it may already have created an empty
checkpoint. Retry the same command with `--resume` to use that checkpoint.
Use `--fresh` or a different output directory when experiment inputs change.

The unsupported-data cases exercise the current clarification contract. The
runner checks for a clarification event, so a reasonable prose refusal alone
can fail that automatic check. Review the response and trace before classifying
it as a factual failure.

## How to evaluate

The structured retail checks compare calculation results returned by `run_sql`
or `analyze_data` with independent Decimal gold values. Questions name the
expected output column so the existing scorer can find it. Exact counts use zero
tolerance; currency amounts use GBP 0.01. Inspect the question rubric for the
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

The current case schema cannot represent multi-turn conversations. It cannot
automatically compare grouped per-country top-k values, chart semantics,
clarification wording, table cell accuracy, citation page numbers, or complete
prose factuality. OCR measurements are descriptive. There is no calibrated
judge, broad ablation, or claim of a complete quality benchmark. The Economic
Survey is a Government of India publication; the UCI dataset attribution and
all source URLs/hashes are in the generated manifest. Check each source page's
reuse terms before redistributing the original files.

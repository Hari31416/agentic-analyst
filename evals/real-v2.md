# Real-data evaluation pack v2

V2 contains 50 questions: the 30 frozen v1 cases plus 20 additions drafted by
Luna and checked by the primary agent. It reuses the pinned UCI Online Retail
January 2011 slice, authored analysis rules, and Economic Survey 2023–24 PDFs.
The [v1 guide](real-v1.md) describes source acquisition, runner setup, scoring
and historical runs. V1 and its saved observations remain unchanged.

## Added coverage

The additions include 12 retail questions and 8 document questions. All have
`real-v2-added` tags, so they can be listed and evaluated separately. The full
pack has 32 retail and 18 Survey cases. The new questions are English; the
inherited pack retains its six Hindi and one code-switched questions.

| Added retail topic                         | What the question checks                              |
| ------------------------------------------ | ----------------------------------------------------- |
| Weekend versus weekday                     | Calendar grouping, invoice counts and per-line means  |
| Inclusive calendar bands                   | Boundary filters across the full January period       |
| First and last qualifying dates            | Calendar endpoints and daily aggregates               |
| Cancellation/negative-quantity cross-tab   | Four disjoint raw-row populations                     |
| Signed row amounts versus qualifying sales | Signs, raw rows and positive-sale filters             |
| Invoice line-count buckets                 | Row filtering before invoice classification           |
| Line versus invoice averages               | Different denominators over the same sales numerator  |
| Top-three country concentration            | Ranking, combined sales and share denominator         |
| Leading StockCode                          | Text identifier, units, lines and sales for one group |
| Unit-price bands                           | Inclusive threshold and sales shares                  |
| Quantity bands                             | Lines versus units and sales shares                   |
| Conditional missing customers              | UK versus other-country line and value denominators   |

| Added document topic                          | Original PDF evidence                                |
| --------------------------------------------- | ---------------------------------------------------- |
| FY24 GDP and GVA growth                       | Full English Survey, PDF 56, paragraph 1.11          |
| Union fiscal deficit                          | Full English Survey, PDF 64, paragraph 1.22          |
| e-KYC and UPI scale                           | Full English Survey, PDF 106, paragraph 2.56         |
| MFI rural and women clientele                 | Full English Survey, PDF 108, paragraphs 2.62–2.63   |
| Nifty 50 fiscal-year returns                  | Full English Survey, PDF 111, paragraph 2.73         |
| LPG, petrol and diesel reductions             | English prices chapter, PDF 4, paragraph 3.9         |
| Core inflation definition and purpose         | English prices chapter, PDF 5, paragraph 3.11        |
| State/UT count and rural/urban basket weights | English prices chapter, PDF 13, paragraphs 3.25–3.26 |

## Ground truth and tool expectations

The [inventory](cases/real-v2.json) preserves the inherited 30 case objects
exactly. Each new case specifies its units, populations, period and answer
criteria. Retail numeric expectations use Decimal arithmetic over the pinned
CSV and independent integer-penny SQLite checks. Labels, dates, grouping
interpretation and answer completeness also require manual review. Document
references include PDF page numbers, printed pages and paragraphs; their exact
passage anchors are diagnostic, and factual completeness requires manual review.
Source-reported figures, including market returns, are evaluated against the
selected Survey passage.

The new retail cases allow 14 relevant runtime tools, including optional reports.
The raw cancellation cross-tab does not require citing qualifying-sales rules.
Document cases allow seven discovery, retrieval, summary and evidence tools.
These are generous tool-appropriateness expectations, not mandatory sequences.
The runtime exposes its tool catalog; the user question does not prescribe tools.

All review labels remain `unreviewed`. Luna drafting and primary source checking
are recorded as agent review, not independent human certification. No model
trials, judge calibration or rescoring were performed. Fifty questions broaden
coverage but do not alone establish reliable model rankings: cases still share
source families, and the additions do not expand Hindi coverage.

## Reproduce and run later

From `backend`, rebuild v2 without model calls:

```sh
uv run python ../evals/generators/generate_real_v2.py
uv run python -m evaluation.cli list --cases ../evals/cases/real-v2.json
uv run python -m evaluation.cli list --cases ../evals/cases/real-v2.json --tag real-v2-added
```

The generator freezes the v1 inventory hash, verifies pinned inputs, recomputes
new numeric answers, and checks document anchors on the declared pages. Its
reviewed definitions live in `evals/generators/real_v2_additions.json` and its
manifest lives in `evals/fixtures/real-v2/manifest.json`. The original v1 source
fixtures must be present locally.

Once services are ready, run all 50 or just the additions into fresh directories:

```sh
uv run python -m evaluation.cli run --live \
  --cases ../evals/cases/real-v2.json --repeats 1 \
  --output ../evals/runs/real-v2
uv run python -m evaluation.cli run --live \
  --cases ../evals/cases/real-v2.json --tag real-v2-added --repeats 1 \
  --output ../evals/runs/real-v2-added
```

Use the existing review workflow to record factual success, tool appropriateness,
citations, artifacts, language and execution separately. Keep results split by
case cohort when comparing v1 and v2 coverage.

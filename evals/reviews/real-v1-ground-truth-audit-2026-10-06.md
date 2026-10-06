# Real-v1 ground-truth audit

Reviewed 6 October 2026. Scope: all 30 questions, selected inputs, expected
calculations, passages, artifacts, rubrics, answerability, languages, allowed
actions, and the code that scores them. This is a source-backed agent review,
not independent human or native-speaker certification.

**Verdict: the retail numeric gold is correct, but the pack is not ready to use
as an unquestioned, fully automatic ground-truth benchmark.** The original audit found incomplete answer specifications and reproducible
scoring false negatives. See the fixes recorded at the end of this audit. The original tool-restriction concern below was withdrawn after the
user confirmed that the allowlist intentionally evaluates tool appropriateness. The existing documentation discloses many limitations, but
disclosure does not remove their effect on model comparisons.

During the initial audit, the case inventory, generator, historical reports and
scorer were not changed. Subsequent fixes are recorded below.
No model calls, service starts, or infrastructure changes were made. Existing
uncommitted comparison-viewer edits were left untouched.

## Verification performed

- Verified byte sizes and SHA-256 hashes of all seven downloaded originals.
- Verified hashes of every source selected by the 30 cases.
- Read the original XLSX independently and compared every January row, field,
  and row order with the derived CSV. All 35,147 rows match exactly.
- Recalculated gold with independent SQLite SQL using integer pennies. Verified
  every retail expected calculation, all 22 country totals, and the complete
  top-five ranking against the manifest. No numeric ground-truth error found.
- Located every expected document phrase in its actual selected source.
- Visually checked English chapter PDF page 1, full English Survey PDF page 132,
  Hindi chapter PDF pages 1, 5 and 9, and appendix PDF pages 92 and 93. Read
  relevant English chapter paragraphs, including 3.18 on PDF page 9.
- Ran the existing real-inventory, download-safety and evaluation-metric tests:
  **19 passed**. Separate in-memory probes reproduced the formatting, tool and
  calculation-route behavior described below.

Verified retail values: GBP 691,364.56; 34,306 qualifying lines; 387,785 units;
1,086 distinct qualifying invoices; 22 countries; 741 non-missing customers;
13,077 qualifying lines with missing CustomerID; missing-line percentage
38.1186964379%, correctly represented by 38.12 with the current tolerance;
missing-customer sales GBP 121,919.52; 701 raw cancellation lines. Missing
customers contribute 17.6346210167% of qualifying sales value.

The inclusion convention is clearly authored for this evaluation and is not
misrepresented as UCI accounting policy. Positive quantity, positive price and
non-cancellation filtering, invoice versus line grain, and null preservation
are coherent. Counting raw cancellation rows separately is also correct.

## Findings that need attention

### 1. The action allowlist is intentional, not a defect

The user confirmed that choosing tools appropriate to the query is part of the
evaluation. The agent is expected to choose among its ordinary runtime tools
without instructions naming which ones to use. Allowed actions are optional,
not a required sequence. The original shared 13-action list was retained in the
first fix, then superseded by Luna's generous per-question review at the user's
request. The current case inventory and generator define those lists.

The prior `retail-scope-clarification` result can therefore retain correct
factual content while failing tool appropriateness. Both dimensions should be
visible. My earlier recommendation to expose or broaden the allowlist is
withdrawn. This section supersedes the original audit finding.

### 2. Exact passage matching is not a complete or unique answer key

The phrase labels all exist in the originals. However, a selected-source citation
must contain the exact normalized phrase to pass. Correct evidence elsewhere
can fail, while an answer that cites the phrase but says something wrong can
pass the passage metric. The manual-review status prevents a wholly automatic
success, but exact-phrase failures still become automatic failures.

For example, `survey-causal-overclaim` expects wording from the opening summary.
Paragraph 3.18 on English chapter PDF page 9 also supports an answer discussing
extreme weather, reservoir levels and crop damage without proving weather alone
was the cause. That paragraph does not contain the required opening-summary
phrase. The prior full-run review already records a substantively supported
causal answer despite this phrase-check failure.

`retail-jan-sales-en` also requires a rules passage even though its question
explicitly asks only for calculation evidence. Applying the supplied rules and
citing the calculation is insufficient for the hidden additional citation gate.

Recommendation: specify required claims and accepted supporting passages,
including alternatives. Keep exact phrase checks as retrieval diagnostics unless
the question explicitly requires that passage. Explicitly request the rules
citation in `retail-jan-sales-en` if it should be mandatory.

### 3. Numeric text scoring rejects correct presentation

`backend/evaluation/runner.py:383` accepts comma digit grouping but does not
normalize grouping spaces. An in-memory probe with the correct answer values
using U+202F narrow nonbreaking spaces failed all three `answer_number` checks
for `retail-jan-sales-en`. The same values with comma grouping passed.
The repository's status log already records this issue in a retained Hindi run.

GBP unit scoring requires the literal string `GBP`. An answer using the correct
`£` symbol passes the number checks but fails the unit check. This is reasonable
for a question that explicitly requests the literal code, but many currency
cases do not require that spelling. A currency symbol should be accepted when
the supplied rules make the denomination unambiguous.

Recommendation: normalize valid grouping characters, define supported numeral
formats, and accept equivalent unit forms where the question permits them.
Continue to verify the structured calculation separately from prose presence.

### 4. Several requested outputs have no complete reference specification

The rubric is currently an informal review instruction rather than a complete
answer key. Important examples:

- `survey-fy24-retail-inflation-en` should explicitly require both 5.4% and
  the lowest level since the pandemic period. Its label checks only the figure.
- `survey-inflation-policy-measures-en` should enumerate all four opening-summary
  measures: dynamic stock management, open market operations, subsidised
  provision of essential food items, and trade policy measures. The phrase key
  includes only the first two. The question should name the opening summary if
  this exact list is intended, because it currently refers to the whole chapter.
- `survey-inflation-hi-source` has no explicit retail or food answer key. A
  useful fixed reference would distinguish retail inflation of 5.4% from CFPI
  food inflation of 7.5% in FY24, up from 6.6% in FY23. Food-and-beverages CPI
  and CFPI should not be silently treated as the same measure. Either require
  these figures in the question or define which qualitative FY24 descriptions
  also earn full credit.
- `survey-appendix-table-retrieval` should explicitly list CPI-IW General;
  CPI-NS Rural, Urban and Combined; CPI-AL General; and CPI-RL General. Rural,
  Urban and Combined belong under CPI-NS, not CPI-IW. Store both PDF pages
  92-93 and printed appendix pages 85-86 as the reference.
- `retail-missing-customer-sales` requests a value share, but stores only its
  numerator and denominator. Add the 17.6346210167% reference and a declared
  reporting tolerance.
- Both chart/CSV cases should explicitly require the complete 22-country CSV,
  all country values, and the ordered top-five chart. The English chart rubric
  stores only the top five; the Hindi chart rubric stores no value list. All
  country values are available in the manifest but should be bound clearly
  to both review specifications.

`survey-hindi-source-search-en` intentionally permits any supported FY24 claim.
That is a useful open-ended task, but it cannot have one fixed answer. Define
claim validity, FY24 scope, translation fidelity and page-citation criteria, with
accepted examples and a consistent manual evaluation process.

Recommendation: add reference claims, allowed alternatives, completeness rules,
page locations and partial-credit rules before freezing the pack as ground truth.
Reviewers should not have to infer these after reading model outputs.

### 5. The drivers question mixes two time windows

`survey-inflation-drivers-en` asks about FY22 and FY23 while requiring the
opening-summary sentence about food prices in the last two years. The summary
ties pandemic supply disruptions and conflict-related commodity prices to core
price pressures in FY22/FY23, then describes food and overall pressures in
FY23/FY24. Paragraph 3.18 explicitly identifies FY23/FY24 for the weather-related
food conditions.

Recommendation: ask which factors the opening summary associates with core
pressures in FY22/FY23 and food pressures in FY23/FY24. Do not imply that both
groups of factors refer to the same two-year window.

### 6. Allowed tools and scorable final calculation routes differ

`run_python` is allowed, but `_observation_rows` reads only `run_sql` and
`analyze_data` results and takes their latest successful result. A Python-only
calculation does not automatically become a scorable structured calculation.
The numeric questions explicitly request a final SQL/analysis row with named
columns, so this is an intentional output contract rather than an incorrect
numeric label. It nevertheless constrains what this benchmark measures.

Recommendation: document that Python may support work but the final numeric
evidence must be SQL/analysis, or add a structured Python-output contract. If
the goal is tool-independent model evaluation, support all permitted routes.
Later supplementary queries can also displace the result being scored; bind
gold checks to declared final calculation evidence rather than an unrelated
last tool result.

### 7. Some cases reveal the desired reasoning decision

`retail-missing-cost-unsupported` says to ask for cost data and not infer margins.
`retail-scope-clarification` supplies the missing-period diagnosis and desired
clarification. `survey-causal-overclaim` instructs the distinction to make.
These are valid instruction-following tasks, but passing them does not establish
spontaneous detection of unsupported data or causal overstatement.

Recommendation: retain these as guided tasks and add separate neutral versions
if autonomous ambiguity/unsupported-data detection is a claimed capability.

### 8. Coverage and provenance do not support a certified quality score

All 30 labels remain `unreviewed`; all cases have a manual-review note. Prior
AI-assisted reviews are useful evidence, but do not establish independent human
or Hindi-native-speaker review. Several questions repeat the same retail
aggregates or opening inflation passage. Treat the 30 cases as a selected task
inventory, not 30 independent tests of broad factual reasoning.

This is an end-to-end application benchmark. Parsing, OCR, retrieval, tool
contracts, prompts and execution failures affect outcomes alongside the selected
answering model. It is suitable for comparing models in a fixed application,
with those components recorded, rather than attributing every miss to model
reasoning alone.

Recommendation: retain separate factual/task, retrieval, tool-contract,
execution and language judgments. Freeze reviewed gold before candidate runs;
keep failed trials; predeclare cohort weighting and use repeated trials for a
reliability comparison. Formal clarification status remains a separate contract
from the quality of a correct prose refusal or clarification.

## All 30 cases

Every row below was checked. "Correct" refers to numeric/reference content,
not certification that the current scorer completely measures task success.
Common findings above apply across the inventory.

| Case                                     | Verified target and remaining issue                                                                                                                                                                                |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| retail-jan-sales-en                      | Correct: GBP 691,364.56; 34,306 lines; 1,086 invoices. Additional rules citation should be explicit.                                                                                                               |
| retail-distinct-countries                | Correct: 22 qualifying countries.                                                                                                                                                                                  |
| retail-customer-null-en                  | Correct: 13,077 lines and 38.12%; denominator is 34,306 qualifying lines. Specify two-decimal rounding if intended.                                                                                                |
| retail-top-country-en                    | Correct: United Kingdom, GBP 561,289.98. Country identity remains manual.                                                                                                                                          |
| retail-cancellations                     | Correct: 701 raw cancellation rows; GBP 691,364.56 qualifying sales. Distinct populations are explicit.                                                                                                            |
| retail-sales-chart-csv                   | Correct top-five gold. Add complete 22-country export reference and content validation.                                                                                                                            |
| retail-revenue-hindi                     | Correct: same three global sales aggregates. Language review remains separate.                                                                                                                                     |
| retail-codeswitch-top-country            | Correct: UK, GBP 561,289.98 and overall 1,086 invoices. Overall invoice scope is explicit. Input hi-Latn-IN and output hi-IN are intentionally separate.                                                           |
| retail-mixed-rule-retrieval              | Correct rules and GBP 691,364.56. Citation is explicitly requested.                                                                                                                                                |
| retail-missing-cost-unsupported          | Correct unsupported classification: no costs. Prompt reveals desired abstention.                                                                                                                                   |
| retail-lines-vs-invoices                 | Correct: 1,086 distinct invoices versus 34,306 lines. Explanation remains manual.                                                                                                                                  |
| retail-units-sold                        | Correct: 387,785 qualifying units.                                                                                                                                                                                 |
| retail-missing-customer-sales            | Correct: GBP 121,919.52 / GBP 691,364.56. Missing explicit 17.6346210167% share reference.                                                                                                                         |
| retail-top-five                          | Correct ordered UK 561,289.98; Netherlands 26,611.16; EIRE 21,904.19; France 17,740.12; Germany 16,910.84, all GBP. Values and order remain manual.                                                                |
| retail-hindi-english-rules               | Correct: 34,306 lines; GBP 691,364.56; preserve missing IDs.                                                                                                                                                       |
| retail-export-and-chart-hi               | Same correct country gold as English. Add explicit full CSV and top-five reference.                                                                                                                                |
| retail-cancellation-only-count           | Correct: 701 raw rows, no other filters.                                                                                                                                                                           |
| retail-distinct-customer-count           | Correct: 741 non-missing qualifying customer IDs.                                                                                                                                                                  |
| retail-netherlands-sales                 | Correct: GBP 26,611.16 after inclusion and country filters.                                                                                                                                                        |
| retail-scope-clarification               | Correct missing-period expectation for January-only data. More precisely a coverage/unsupported-input task than an ambiguous definition. Prompt reveals diagnosis; intentional tool-appropriateness gate has affected a prior answer. |
| survey-fy24-retail-inflation-en          | Correct: 5.4%, lowest since pandemic period. Chapter PDF page 1, paragraph 3.1. Add full claim reference.                                                                                                          |
| survey-fy24-inflation-hi-query-en-source | Correct: 5.4% in FY24 with English-source evidence and Hindi output. Chapter PDF page 1, paragraph 3.1.                                                                                                            |
| survey-inflation-drivers-en              | Source-backed factors, but clarify FY22/FY23 core versus FY23/FY24 food periods. Chapter PDF page 1; food timing confirmed by paragraph 3.18 on page 9.                                                            |
| survey-inflation-policy-measures-en      | Four opening-summary measures are supported on chapter PDF page 1. Complete list and intended passage scope need explicit gold.                                                                                    |
| survey-inflation-hi-source               | Answerable. Hindi PDF page 1 supports retail 5.4%; page 9, paragraph 3.18 supports CFPI 6.6% to 7.5%. Add complete reference and accepted descriptions.                                                            |
| survey-summary-vs-chapter                | Correct: both report 5.4% in FY24. Full Survey PDF page 132 and chapter PDF page 1. ID says summary, but question correctly specifies the full report's inflation chapter.                                         |
| survey-inflation-hi-cross-language       | Correct: core inflation reached a four-year low in FY24. Opening summary on chapter PDF page 1. Alternative valid evidence should not automatically fail.                                                          |
| survey-hindi-source-search-en            | Answerable open-ended claim task with 18-page Hindi source. Add explicit manual criteria and accepted examples; it has no fixed answer.                                                                            |
| survey-appendix-table-retrieval          | Correct table title. Correct locations are PDF 92-93 / printed 85-86. Add exact category hierarchy; CPI-NS contains Rural/Urban/Combined.                                                                          |
| survey-causal-overclaim                  | Correct: chapter does not prove adverse weather alone caused FY24 food inflation. Accept relevant alternative passages, including paragraph 3.18.                                                                  |

## Source references

English opening claims and cross-document agreement:
:codex-file-citation{path="/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/inflation-en.pdf" purpose="source"}
and
:codex-file-citation{path="/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/economic-survey-2023-24-en.pdf" purpose="source"}.

Hindi retail and food statements:
:codex-file-citation{path="/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/inflation-hi.pdf" purpose="source"}.

CPI category hierarchy and continuation:
:codex-file-citation{path="/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/statistical-appendix-en.pdf" purpose="source"}.

Inventory: `evals/cases/real-v1.json`. Retail gold and complete country values:
`evals/fixtures/real-v1/manifest.json`. Generation logic:
`evals/generators/generate_real_v1.py`. Declared coverage limitations:
`evals/real-v1.md` and `docs/evaluation.md`.

## Recommended order before freezing ground truth

1. Keep the intentional action allowlist and correct numeric-format false negatives.
2. Write complete claim/category/artifact references and accepted evidence
   alternatives; clarify the drivers' periods and intended policy-list passage.
3. Have those references reviewed independently, with Hindi language review.
   Version changed execution questions separately from scoring-only corrections.
4. Keep automatic diagnostics separate from reviewed task correctness. Preserve
   current historical runs and report each scoring revision explicitly.

## Fixes applied after the audit

The 6 October revision corrects protected number grouping and currency-symbol
scoring; supports source-backed alternative passage anchors and diagnostic
passage checks; binds calculations to declared evidence where linkage exists;
adds complete Survey and artifact references and review criteria; clarifies
periods, requested units/measures, CSV coverage and the missing-customer value
share; and removes answer-revealing guidance from three decision tasks.
The first fix preserved the shared action allowlist. The user subsequently
requested generous per-question lists from Luna; that review supersedes the
shared list without prescribing a tool sequence or changing runtime permissions.

Historical reports and labels remain untouched. Changed questions require fresh
execution. Independent human/Hindi review, calibrated semantic scoring and
repeated model reliability measurements remain outstanding. This revision does
not convert unreviewed labels into certified human ground truth.

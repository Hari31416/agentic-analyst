# Gemma versus GPT OSS 120B: real-v1

OSS 120B completes **24/30 (80.0%)**, versus Gemma **20/30 (66.7%)**. Following the tool-validation error message bound repair, all eight rerun queries completed without fatal exceptions, leaving zero terminal execution failures for both models. This single run does not establish a stable ranking.

| Metric | Gemma 4 26B A4B | GPT OSS 120B |
| --- | ---: | ---: |
| Complete / partial / failed | 20 / 6 / 4 | 24 / 5 / 1 |
| Retail complete | 16/20 (80%) | 17/20 (85%) |
| Survey complete | 4/10 (40%) | 7/10 (70%) |
| Invalid model responses | 0 | 0 |
| Infrastructure failures | 0 | 0 |
| Automatic numeric checks passed | 23/25 | 23/25 |
| Median query time, all trials | 9.41s | 17.14s |
| P95 query time, all trials | 20.99s | 30.43s |

The recorded identities differ only in model (`gemma-4-26B-A4B-it` versus `gpt-oss-120b`). Cases, fixture hashes, application/runner code, prompts, extraction, retrieval, embedding/reranker configuration, provider endpoint, budgets and timeout match. Query timing excludes ingestion and includes completed trials.

OSS gains: With execution crashes resolved, GPT OSS produced both required CSV and chart deliverables in both retail analysis cases (`retail-sales-chart-csv` and `retail-export-and-chart-hi`), safely recovering from an oversized tool-validation error in the latter. In survey queries, it cited both full survey and standalone inflation documents to verify 5.4% retail inflation, and retrieved Hindi chapter evidence in English. It also passed CPI appendix table retrieval and annual-scope clarification.

Remaining OSS issues: In `retail-customer-null-en`, both models computed missing CustomerID over the raw January slice instead of qualifying invoice lines, failing the calculation check. In cross-language and Hindi survey searches, minor presentation issues (mislabeled page numbers as chapters or omitting explicit in-text PDF page citations) resulted in partial verdicts.

Shared issues: missing-customer SQL omits qualifying-sale filters; code-switch top-country answer omits United Kingdom; FY24 food discussion does not establish the actual requested trend. Numeric checks alone miss missing entities, percentages, citation precision and complete exports.

Across all 30 paired cases, median query time is 17.14s for GPT OSS versus 9.41s for Gemma. Recorded tokens are 1,231,952 versus 1,165,156. All 30 paired cases now reach a completed or awaiting-clarification terminal state.

Review is AI-assisted and uses the same criteria for declared evidence, numeric tolerance, scope and required deliverables. Original automatic metrics and runs are preserved. One repetition and no independent native-speaker calibration limit reliability and language conclusions.

[Gemma review](../real-v1-full-2026-10-05/performance.md) · [OSS review](../real-v1-full-oss-120b-2026-10-05/performance.md) · [Rerun report](../../runs/real-v1-oss-120b-failed-rerun-2026-10-05/report.json) · [Comparison data](comparison.json) · [Paired cases CSV](cases.csv)

| Case | Gemma | OSS 120B | OSS review reason |
| --- | --- | --- | --- |
| retail-jan-sales-en | pass | pass | Correct qualifying filters and requested numeric results within tolerance. The SQL returns one aggregate row and valid calculation evidence IDs are declared in the answer payload. Sparse inline citation/answer formatting is a presentation issue; the same declared-evidence criterion is applied to both models. |
| retail-distinct-countries | pass | pass | 22 distinct countries after all three qualifying filters, matching gold. |
| retail-customer-null-en | fail | fail | The query filters the raw January slice (35,147 rows) but does not apply qualifying sales filters (non-cancellation, positive quantity, positive price). It returns 13,235 missing-customer rows and 37.66%; gold is 13,077 and 38.12% among qualifying invoice lines. Identical population error to Gemma. |
| retail-top-country-en | pass | pass | United Kingdom is correctly named and ranked first; 561289.98 GBP matches gold within the 0.01 tolerance. SQL uses all qualifying filters and descending country sales. |
| retail-cancellations | pass | pass | 701 raw cancellation rows and 691364.560 qualifying sales match gold. SQL correctly separates cancellation count from sales filters. |
| retail-sales-chart-csv | pass | pass | Run completed in 10 model requests. Produced valid result.csv containing the top five countries matching gold to cents (UK £561,289.98, Netherlands £26,611.16, EIRE £21,904.19, France £17,740.12, Germany £16,910.84) and valid PNG bar chart top5_sales.png. Declared valid evidence and both artifact IDs. |
| retail-revenue-hindi | pass | pass | All requested totals match gold and SQL applies the qualifying filters. First-pass Hinglish/Hindi output is understandable; a native-speaker quality certification is outside this review. |
| retail-codeswitch-top-country | partial | partial | The top sales amount 561289.98 and distinct invoice count 1086 match. However, neither the SQL projection nor answer identifies the country; gold category is United Kingdom, so the main categorical answer is incomplete. |
| retail-mixed-rule-retrieval | pass | pass | Correct qualifying filters and requested numeric results within tolerance. The SQL returns one aggregate row and valid calculation evidence IDs are declared in the answer payload. Sparse inline citation/answer formatting is a presentation issue; the same declared-evidence criterion is applied to both models. |
| retail-missing-cost-unsupported | pass | pass | Correctly refuses to invent profit or margin without cost data and asks for the needed cost information. |
| retail-lines-vs-invoices | pass | pass | Correct 1086 distinct invoices and 34306 invoice-line rows, with a clear explanation of the different grains. |
| retail-units-sold | pass | pass | 387785 qualifying units matches gold. SQL sums quantity only for non-cancelled lines with positive quantity and price. |
| retail-missing-customer-sales | pass | partial | The numerator 121919.52 and denominator 691364.56 match gold, and SQL applies the correct qualifying filters without imputing identity. The answer omits the explicitly requested share (about 17.63%), although it gives both values from which to calculate it. |
| retail-top-five | pass | pass | Names, order and rounded amounts match the gold top five. SQL applies the qualifying filters and sorts descending. |
| retail-hindi-english-rules | pass | pass | Correctly retrieved the attached rules document and calculated qualifying rows (34,306) and total sales GBP (£691,364.56). Both values match gold exactly. Automatic number check missed only due to narrow nonbreaking spaces in the Hindi digit grouping (34 306 and 691 364.560). |
| retail-export-and-chart-hi | partial | pass | Run completed and safely recovered from a 626-character validation rejection in analyze_data. Generated valid result.csv with top five countries (UK £561,289.98, Netherlands £26,611.16, EIRE £21,904.19, France £17,740.12, Germany £16,910.84) and valid PNG bar chart top5_countries_sales.png. Hindi answer includes full summary table and declared artifacts. |
| retail-cancellation-only-count | pass | pass | 701 raw January rows whose InvoiceNo begins with C matches gold. No other filters are applied. |
| retail-distinct-customer-count | pass | pass | 741 distinct non-missing customers under all qualifying filters matches gold. |
| retail-netherlands-sales | pass | pass | Netherlands qualifying sales 26611.16 GBP matches gold. SQL contains country predicate and all three qualifying rules. |
| retail-scope-clarification | fail | pass | Correctly explains that the source covers January 2011 only, gives that month’s qualifying total, and asks whether the user wants it or can provide the remaining months. |
| survey-fy24-retail-inflation-en | pass | pass | Run completed in 2 model requests. Correctly reported retail inflation of 5.4% in FY24 and characterized it as the lowest level since the Covid-19 pandemic period. Declared valid passage citation matching English inflation chapter PDF page 1, paragraph 3.1. |
| survey-fy24-inflation-hi-query-en-source | fail | pass | Hindi answer supplies FY24 retail inflation of 5.4 per cent. Declared original English chapter page-1 evidence supports the figure; percentage units and requested language are correct. |
| survey-inflation-drivers-en | pass | pass | Correctly distinguishes pandemic supply disruption and conflict-related commodity conditions from adverse-weather food-price pressure. Declared opening-summary evidence supports the requested FY22/FY23 explanation. |
| survey-inflation-policy-measures-en | pass | pass | All four measures match the original opening summary: stock management, open market operations, subsidised essential foods and trade measures. Original evidence is declared. |
| survey-inflation-hi-source | partial | partial | Useful Hindi retail/state and policy discussion, but the FY24 food subsection describes mitigation without establishing the actual food-inflation trend. Hindi chapter page 9 paragraph 3.18 shows CFPI rising from 6.6 per cent in FY23 to 7.5 per cent in FY24. Its evidence does not include that passage; mitigation versus an observed decline remains ambiguous. |
| survey-summary-vs-chapter | partial | pass | Successfully cited both the full Economic Survey PDF (page 73) and the standalone inflation chapter (page 1) with declared evidence IDs. Quoted both sources verifying the 5.4% retail inflation figure for FY24 and correctly explained that both sources agree. |
| survey-inflation-hi-cross-language | partial | partial | The answer accurately captures the core inflation decline to a four-year low in FY24 and core services inflation reaching a nine-year low, citing chapter pages 6 and 7 with declared evidence. However, it mislabels the pages as Chapter 6 and Chapter 7 in the Hindi text. |
| survey-hindi-source-search-en | partial | partial | Run completed and successfully retrieved the Hindi chapter PDF (page 4), translating the headline energy/fuel inflation downward trend into English with declared evidence. However, it omitted the explicit in-text PDF page number and characterized headline inflation as falling to its lowest level rather than lowest since pandemic. |
| survey-appendix-table-retrieval | fail | pass | Identifies Table 4.3 and all four CPI series and categories, with PDF pages 92 and 93 and declared evidence from both. Rendered headers establish IW General, NS Rural/Urban/Combined, AL General and RL General. The flat header list does not make the incorrect grouped assignment seen in Gemma. |
| survey-causal-overclaim | pass | pass | Correctly declines to treat adverse weather alone as proven cause. Declared original passages support contributing weather and other conditions without establishing exclusive causation. |

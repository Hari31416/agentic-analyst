# Gemma versus GPT OSS 120B: real-v1

OSS 120B completes **19/30 (63.3%)**, versus Gemma **20/30 (66.7%)**. It improves Survey content on completed answers, but five invalid model responses reduce overall task completion. This single run does not establish a stable ranking.

| Metric | Gemma 4 26B A4B | GPT OSS 120B |
| --- | ---: | ---: |
| Complete / partial / failed | 20 / 6 / 4 | 19 / 4 / 7 |
| Retail complete | 16/20 (80%) | 14/20 (70%) |
| Survey complete | 4/10 (40%) | 5/10 (50%) |
| Invalid model responses | 0 | 5 |
| Infrastructure failures | 0 | 0 |
| Automatic numeric checks passed | 23/25 | 21/25 |
| Median query time, all trials | 9.41s | 16.01s |
| P95 query time, all trials | 20.99s | 26.89s |

The recorded identities differ only in model (`gemma-4-26B-A4B-it` versus `gpt-oss-120b`). Cases, fixture hashes, application/runner code, prompts, extraction, retrieval, embedding/reranker configuration, provider endpoint, budgets and timeout match. Query timing excludes ingestion and includes failed trials.

OSS gains: Hindi question against English inflation evidence now supplies 5.4%; CPI appendix retrieval gives the correct series/categories and both pages; annual-scope clarification respects the action contract. Its core-inflation answer also finds the requested four-year low, though an additional chart attribution error leaves that case partial.

OSS regressions: both chart/export tasks, English FY24 inflation, full-versus-chapter comparison and Hindi-source/English-output search end in invalid_model_response. The Hindi rules task asks for a source already attached. Missing-customer sales supplies the correct amounts but omits the requested 17.63% share. Saved generic errors cannot tell us the exact response/parser problem.

Shared issues: missing-customer SQL omits the qualifying-sale filters; the code-switch top-country answer omits United Kingdom; FY24 food discussion does not establish the actual requested trend. Numeric checks alone miss missing entities, percentages, citation precision and complete exports.

For the same 25 cases whose OSS runs did not fail, median query time is 16.33s versus 9.33s for Gemma (about 75% longer). Recorded tokens are 853,234 versus 908,144 (about 6% fewer). Five failed OSS runs lack token/model-call totals, so overall token totals cannot establish lower cost. No pricing comparison is made.

Review is AI-assisted and uses the same criteria for declared evidence, numeric tolerance, scope and required deliverables. Original automatic metrics and runs are preserved. One repetition and no independent native-speaker calibration limit reliability and language conclusions.

[Gemma review](../real-v1-full-2026-10-05/performance.md) · [OSS review](../real-v1-full-oss-120b-2026-10-05/performance.md) · [Comparison data](comparison.json) · [Paired cases CSV](cases.csv)

| Case | Gemma | OSS 120B | OSS review reason |
| --- | --- | --- | --- |
| retail-jan-sales-en | pass | pass | Correct qualifying filters and requested numeric results within tolerance. The SQL returns one aggregate row and valid calculation evidence IDs are declared in the answer payload. Sparse inline citation/answer formatting is a presentation issue; the same declared-evidence criterion is applied to both models. |
| retail-distinct-countries | pass | pass | 22 distinct countries after all three qualifying filters, matching gold. |
| retail-customer-null-en | fail | fail | The SQL filters the raw January slice but does not apply the three qualifying-sale filters. It counts 13235 missing-customer rows and reports 37.6561%; gold is 13077 and 38.12% among qualifying invoice lines. Percentage units are explicitly expressed, but the population and result are wrong. |
| retail-top-country-en | pass | pass | United Kingdom is correctly named and ranked first; 561289.98 GBP matches gold within the 0.01 tolerance. SQL uses all qualifying filters and descending country sales. |
| retail-cancellations | pass | pass | 701 raw cancellation rows and 691364.560 qualifying sales match gold. SQL correctly separates cancellation count from sales filters. |
| retail-sales-chart-csv | pass | fail | Run failed before SQL, CSV export, or PNG chart creation. Answer is empty and artifact_ids is empty despite both outputs being required. |
| retail-revenue-hindi | pass | pass | All requested totals match gold and SQL applies the qualifying filters. First-pass Hinglish/Hindi output is understandable; a native-speaker quality certification is outside this review. |
| retail-codeswitch-top-country | partial | partial | The top sales amount 561289.98 and distinct invoice count 1086 match. However, neither the SQL projection nor answer identifies the country; gold category is United Kingdom, so the main categorical answer is incomplete. |
| retail-mixed-rule-retrieval | pass | pass | Correct qualifying filters and requested numeric results within tolerance. The SQL returns one aggregate row and valid calculation evidence IDs are declared in the answer payload. Sparse inline citation/answer formatting is a presentation issue; the same declared-evidence criterion is applied to both models. |
| retail-missing-cost-unsupported | pass | pass | Correctly refuses to invent profit or margin without cost data and asks for the needed cost information. |
| retail-lines-vs-invoices | pass | pass | Correct 1086 distinct invoices and 34306 invoice-line rows, with a clear explanation of the different grains. |
| retail-units-sold | pass | pass | 387785 qualifying units matches gold. SQL sums quantity only for non-cancelled lines with positive quantity and price. |
| retail-missing-customer-sales | pass | partial | The numerator 121919.52 and denominator 691364.56 match gold, and SQL applies the correct qualifying filters without imputing identity. The answer omits the explicitly requested share (about 17.63%), although it gives both values from which to calculate it. |
| retail-top-five | pass | pass | Names, order and rounded amounts match the gold top five. SQL applies the qualifying filters and sorts descending. |
| retail-hindi-english-rules | pass | fail | The answer asks the user to provide the attached rules even though the rules source is present and retrievable. It returns neither requested number. The automatic calculation and answer-number checks fail. |
| retail-export-and-chart-hi | partial | fail | Run failed after document search/profile only; no SQL, CSV, chart, final answer, or artifact IDs were produced despite both outputs being required. |
| retail-cancellation-only-count | pass | pass | 701 raw January rows whose InvoiceNo begins with C matches gold. No other filters are applied. |
| retail-distinct-customer-count | pass | pass | 741 distinct non-missing customers under all qualifying filters matches gold. |
| retail-netherlands-sales | pass | pass | Netherlands qualifying sales 26611.16 GBP matches gold. SQL contains country predicate and all three qualifying rules. |
| retail-scope-clarification | fail | pass | Correctly explains that the source covers January 2011 only, gives that month’s qualifying total, and asks whether the user wants it or can provide the remaining months. |
| survey-fy24-retail-inflation-en | pass | fail | Application run failed with invalid_model_response; no final answer. Saved error does not identify the malformed response or underlying parser cause. |
| survey-fy24-inflation-hi-query-en-source | fail | pass | Hindi answer supplies FY24 retail inflation of 5.4 per cent. Declared original English chapter page-1 evidence supports the figure; percentage units and requested language are correct. |
| survey-inflation-drivers-en | pass | pass | Correctly distinguishes pandemic supply disruption and conflict-related commodity conditions from adverse-weather food-price pressure. Declared opening-summary evidence supports the requested FY22/FY23 explanation. |
| survey-inflation-policy-measures-en | pass | pass | All four measures match the original opening summary: stock management, open market operations, subsidised essential foods and trade measures. Original evidence is declared. |
| survey-inflation-hi-source | partial | partial | Useful Hindi retail/state and policy discussion, but the FY24 food subsection describes mitigation without establishing the actual food-inflation trend. Hindi chapter page 9 paragraph 3.18 shows CFPI rising from 6.6 per cent in FY23 to 7.5 per cent in FY24. Its evidence does not include that passage; mitigation versus an observed decline remains ambiguous. |
| survey-summary-vs-chapter | partial | fail | Application run failed with invalid_model_response; no final comparison answer or verification of both requested sources. |
| survey-inflation-hi-cross-language | partial | partial | The main four-year-low core-inflation claim is correct and supported by declared chapter page-6 evidence, despite the automatic opening-phrase miss. However, it attributes the almost-four-percentage-point Apr22-Jun24 decline to Chart III.12 instead of III.13 and extends a housing-rent explanation for core services to overall core inflation. |
| survey-hindi-source-search-en | partial | fail | Application run failed with invalid_model_response; no supported English statement or requested PDF page citation. |
| survey-appendix-table-retrieval | fail | pass | Identifies Table 4.3 and all four CPI series and categories, with PDF pages 92 and 93 and declared evidence from both. Rendered headers establish IW General, NS Rural/Urban/Combined, AL General and RL General. The flat header list does not make the incorrect grouped assignment seen in Gemma. |
| survey-causal-overclaim | pass | pass | Correctly declines to treat adverse weather alone as proven cause. Declared original passages support contributing weather and other conditions without establishing exclusive causation. |

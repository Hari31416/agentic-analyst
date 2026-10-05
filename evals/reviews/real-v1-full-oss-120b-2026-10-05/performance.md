# OSS 120B real-v1 review

All 30 cases reviewed offline. Complete: **19/30 (63.3%)**; partial: **4**; failed: **7**. Retail: 14/20 complete; Survey: 5/10 complete. This is AI-assisted review with a Luna 6 retail subreview.

Five application runs fail with `invalid_model_response`: both CSV/chart cases, English FY24 inflation, full-versus-chapter comparison, and Hindi-source/English-output search. Saved generic errors do not reveal the exact malformed response. These are model/application failures; no infrastructure failures were recorded. The other two failed tasks omit qualifying filters or ask for already attached rules.

All 13 declared CSV artifacts were downloaded and their hashes verified. Neither required chart task produced a final deliverable. Correct artifact hashes establish integrity, not calculation correctness.

Automatic statuses remain 22 needs_review and eight failed; numeric checks remain 21 pass and four fail. The original report is unchanged. Manual labels are bound to experiment, case/repetition and exact answer hash. The report stores review labels in its existing human_review field, but their provenance explicitly identifies AI-assisted review.

Valid declared evidence IDs are accepted as API citations consistently with the Gemma review. One-row requirements concern the actual SQL/analysis result, not Markdown styling. Requested country names, food trends, percentages, pages and deliverables must still be present. Currency tolerance remains 0.01 GBP. No new live execution, tuning or independent native-speaker calibration was performed.

[Review labels](review.json) · [Performance data](performance.json) · [Reviewed trace](report.json) · [Artifact checks](artifact-review.json)

| Case | Automatic | Review | Reason |
| --- | --- | --- | --- |
| retail-jan-sales-en | needs_review | pass | Correct qualifying filters and requested numeric results within tolerance. The SQL returns one aggregate row and valid calculation evidence IDs are declared in the answer payload. Sparse inline citation/answer formatting is a presentation issue; the same declared-evidence criterion is applied to both models. |
| retail-distinct-countries | needs_review | pass | 22 distinct countries after all three qualifying filters, matching gold. |
| retail-customer-null-en | failed | fail | The SQL filters the raw January slice but does not apply the three qualifying-sale filters. It counts 13235 missing-customer rows and reports 37.6561%; gold is 13077 and 38.12% among qualifying invoice lines. Percentage units are explicitly expressed, but the population and result are wrong. |
| retail-top-country-en | needs_review | pass | United Kingdom is correctly named and ranked first; 561289.98 GBP matches gold within the 0.01 tolerance. SQL uses all qualifying filters and descending country sales. |
| retail-cancellations | needs_review | pass | 701 raw cancellation rows and 691364.560 qualifying sales match gold. SQL correctly separates cancellation count from sales filters. |
| retail-sales-chart-csv | failed | fail | Run failed before SQL, CSV export, or PNG chart creation. Answer is empty and artifact_ids is empty despite both outputs being required. |
| retail-revenue-hindi | needs_review | pass | All requested totals match gold and SQL applies the qualifying filters. First-pass Hinglish/Hindi output is understandable; a native-speaker quality certification is outside this review. |
| retail-codeswitch-top-country | needs_review | partial | The top sales amount 561289.98 and distinct invoice count 1086 match. However, neither the SQL projection nor answer identifies the country; gold category is United Kingdom, so the main categorical answer is incomplete. |
| retail-mixed-rule-retrieval | needs_review | pass | Correct qualifying filters and requested numeric results within tolerance. The SQL returns one aggregate row and valid calculation evidence IDs are declared in the answer payload. Sparse inline citation/answer formatting is a presentation issue; the same declared-evidence criterion is applied to both models. |
| retail-missing-cost-unsupported | needs_review | pass | Correctly refuses to invent profit or margin without cost data and asks for the needed cost information. |
| retail-lines-vs-invoices | needs_review | pass | Correct 1086 distinct invoices and 34306 invoice-line rows, with a clear explanation of the different grains. |
| retail-units-sold | needs_review | pass | 387785 qualifying units matches gold. SQL sums quantity only for non-cancelled lines with positive quantity and price. |
| retail-missing-customer-sales | needs_review | partial | The numerator 121919.52 and denominator 691364.56 match gold, and SQL applies the correct qualifying filters without imputing identity. The answer omits the explicitly requested share (about 17.63%), although it gives both values from which to calculate it. |
| retail-top-five | needs_review | pass | Names, order and rounded amounts match the gold top five. SQL applies the qualifying filters and sorts descending. |
| retail-hindi-english-rules | failed | fail | The answer asks the user to provide the attached rules even though the rules source is present and retrievable. It returns neither requested number. The automatic calculation and answer-number checks fail. |
| retail-export-and-chart-hi | failed | fail | Run failed after document search/profile only; no SQL, CSV, chart, final answer, or artifact IDs were produced despite both outputs being required. |
| retail-cancellation-only-count | needs_review | pass | 701 raw January rows whose InvoiceNo begins with C matches gold. No other filters are applied. |
| retail-distinct-customer-count | needs_review | pass | 741 distinct non-missing customers under all qualifying filters matches gold. |
| retail-netherlands-sales | needs_review | pass | Netherlands qualifying sales 26611.16 GBP matches gold. SQL contains country predicate and all three qualifying rules. |
| retail-scope-clarification | needs_review | pass | Correctly explains that the source covers January 2011 only, gives that month’s qualifying total, and asks whether the user wants it or can provide the remaining months. |
| survey-fy24-retail-inflation-en | failed | fail | Application run failed with invalid_model_response; no final answer. Saved error does not identify the malformed response or underlying parser cause. |
| survey-fy24-inflation-hi-query-en-source | needs_review | pass | Hindi answer supplies FY24 retail inflation of 5.4 per cent. Declared original English chapter page-1 evidence supports the figure; percentage units and requested language are correct. |
| survey-inflation-drivers-en | needs_review | pass | Correctly distinguishes pandemic supply disruption and conflict-related commodity conditions from adverse-weather food-price pressure. Declared opening-summary evidence supports the requested FY22/FY23 explanation. |
| survey-inflation-policy-measures-en | needs_review | pass | All four measures match the original opening summary: stock management, open market operations, subsidised essential foods and trade measures. Original evidence is declared. |
| survey-inflation-hi-source | needs_review | partial | Useful Hindi retail/state and policy discussion, but the FY24 food subsection describes mitigation without establishing the actual food-inflation trend. Hindi chapter page 9 paragraph 3.18 shows CFPI rising from 6.6 per cent in FY23 to 7.5 per cent in FY24. Its evidence does not include that passage; mitigation versus an observed decline remains ambiguous. |
| survey-summary-vs-chapter | failed | fail | Application run failed with invalid_model_response; no final comparison answer or verification of both requested sources. |
| survey-inflation-hi-cross-language | failed | partial | The main four-year-low core-inflation claim is correct and supported by declared chapter page-6 evidence, despite the automatic opening-phrase miss. However, it attributes the almost-four-percentage-point Apr22-Jun24 decline to Chart III.12 instead of III.13 and extends a housing-rent explanation for core services to overall core inflation. |
| survey-hindi-source-search-en | failed | fail | Application run failed with invalid_model_response; no supported English statement or requested PDF page citation. |
| survey-appendix-table-retrieval | needs_review | pass | Identifies Table 4.3 and all four CPI series and categories, with PDF pages 92 and 93 and declared evidence from both. Rendered headers establish IW General, NS Rural/Urban/Combined, AL General and RL General. The flat header list does not make the incorrect grouped assignment seen in Gemma. |
| survey-causal-overclaim | needs_review | pass | Correctly declines to treat adverse weather alone as proven cause. Declared original passages support contributing weather and other conditions without establishing exclusive causation. |

# Real-v1 final performance review

Experiment `cc6d2d3822955703-2afac1a9`. Reviewed all 30 completed trials from 5 October 2026. This is an AI-assisted review by Codex with a Luna 6 retail subreview, not independent human certification.

## Results

| Dataset | Complete | Partial | Failed | Complete rate |
| --- | ---: | ---: | ---: | ---: |
| Retail | 16 | 2 | 2 | 80.0% |
| Survey | 4 | 4 | 2 | 40.0% |
| All 30 cases | 20 | 6 | 4 | 66.7% |

All 30 reached a valid terminal state: 29 completed and one awaited clarification. There were no infrastructure, timeout or model-error trial statuses. Automatic scoring remains 24 needs_review and six failed. The manual review changes neither those metrics nor the original run.

Automatic numeric checks: 23 passed, 2 failed. Both failed values belong to the missing-customer case. Fifteen cases have numeric expectations; 14 satisfy all their numeric expectations. Passing numeric checks do not cover country names, artifact completeness or factual table headers.

## Important findings

- Missing-customer SQL omits qualifying filters and returns a fraction rather than a percentage.
- The code-switch answer omits United Kingdom despite correct amount and invoice count. It remains Romanized even with hi-IN output requested; language fidelity requires native review.
- Hindi export rebuilds the CSV from a 20-row SQL preview instead of the retained 22-row result. Israel and Iceland are missing. Both required charts are readable and correctly identify the top five and GBP; the English CSV contains all 22 correct country totals.
- Hindi FY24 retail answer omits 5.4%; Hindi retail/food answer substitutes FY22 food discussion for the FY24 trend.
- Full/standalone comparison does not cite the same figure from both requested chapters. Grouped short citations remain unresolved in multiple answers.
- CPI table headers are misassigned even though the table and starting page are found. Visual source review exposes the error.
- Scope clarification text is useful, but its register_dataset call violates the case action allowlist.
- Causal-overclaim answer is substantively correct using other original passages. Its exact-phrase automatic failure remains recorded as a retrieval-proxy mismatch.

## Method and limits

Complete means the requested answer and deliverables are correct within case tolerances. Partial means some required content or deliverable is missing. Failed means a core result/header mapping is wrong or the action contract fails. Missing structured GBP metadata is a tooling gap; explicit GBP content and source units were checked without altering its automatic review metric.

Currency tolerance is 0.01 GBP. Some UK outputs round to 561,289.97 instead of 561,289.98 due to float casts; those fall within the pack tolerance and are recorded rather than silently made exact.

Review is bound to experiment ID, case/repetition and exact answer hashes. Relevant English/Hindi PDF pages were rendered and read, including table headers and continuation. Declared CSVs and PNGs were fetched through the authenticated artifact route and their SHA-256 hashes verified. No new model execution or question tuning was performed. Full browser QA and independent native-speaker assessment remain unmeasured. One trial per case does not measure run-to-run reliability.

Files: [review labels](review.json), [performance data](performance.json), [reviewed report](report.json), [automatic report HTML](index.html), [artifact checks](artifact-review.json).

## Per-case review

| Case | Automatic | Review | Reason |
| --- | --- | --- | --- |
| retail-jan-sales-en | needs_review | pass | All three values match gold: GBP 691364.56 (within 0.01 tolerance), 34306 qualifying invoice lines, 1086 distinct invoices. SQL uses the stated C-prefix, positive quantity, positive price filters and January bounds; answer states GBP and distinguishes line count from invoice count. |
| retail-distinct-countries | needs_review | pass | 22 distinct countries; SQL applies all three qualifying filters. |
| retail-customer-null-en | failed | fail | SQL counts missing CustomerID over every January row, without excluding cancellations or nonpositive quantity/price. It returns 13235 and raw fraction 0.3765613; expected is 13077 qualifying lines and 38.12%. Answer repeats the wrong count and fraction, does not express percentage units. |
| retail-top-country-en | needs_review | pass | United Kingdom is correctly ranked first. SQL amount 561289.972759 differs from gold 561289.98 by about 0.0073, within 0.01 tolerance. Correct filters and ranking are present. |
| retail-cancellations | needs_review | pass | 701 cancelled raw rows and sales 691364.56 match gold. SQL counts C-prefixed rows over January while separately applying all three qualifying filters to sales. |
| retail-sales-chart-csv | needs_review | pass | Displayed top five and SQL grouped rows match manifest order and amounts to cents. Required CSV and PNG artifacts are declared with text/csv and image/png roles. Visual rendering and stored artifact contents were not inspected in this review; root is handling visual inspection. |
| retail-revenue-hindi | needs_review | pass | All three aggregate values match gold and SQL filters are correct. First-pass Hindi output gives the requested table. |
| retail-codeswitch-top-country | needs_review | partial | Top-country sales 561289.98 is within tolerance and invoice_count=1086 matches. SQL obtains UK as winner but selects only the amount and invoice count; answer omits the required country name, so the central categorical answer is incomplete. |
| retail-mixed-rule-retrieval | needs_review | pass | Retrieved rule is explicitly stated and correctly applied; sales 691364.56 matches gold. |
| retail-missing-cost-unsupported | needs_review | pass | Correctly abstains: dataset has sales fields but no cost basis, so profit and margin cannot be calculated. No invented values. |
| retail-lines-vs-invoices | needs_review | pass | Counts match gold and explanation correctly distinguishes 1086 distinct invoice IDs from 34306 qualifying line rows. SQL uses all three filters. |
| retail-units-sold | needs_review | pass | 387785 qualifying units matches gold. SQL sums positive quantity for non-cancelled rows with positive unit price. |
| retail-missing-customer-sales | needs_review | pass | 121919.52 missing-customer qualifying sales and total 691364.56 match gold; stated 17.63% is arithmetically consistent. Failed initial SQL attempt was corrected; final SQL applies all three filters and month bounds and does not impute identities. |
| retail-top-five | needs_review | pass | Five country names, order, and amounts match manifest to cents; all three filters are in SQL and described in answer. |
| retail-hindi-english-rules | needs_review | pass | Requested qualifying_rows and sales_gbp match gold; SQL applies the three filters. CustomerID is not grouped, filtered, or imputed, so missing IDs remain missing as requested. |
| retail-export-and-chart-hi | needs_review | partial | Top-five amounts and chart are correct within the 0.01 GBP tolerance, but the promised complete CSV has 20 countries rather than 22. Israel (379.84 GBP) and Iceland (475.39 GBP) are missing. SQL metadata reports row_count=22, sample_rows=20 and truncated=false: the model reconstructed the export from the displayed sample instead of using the full retained CSV. |
| retail-cancellation-only-count | needs_review | pass | 701 raw January rows with InvoiceNo beginning C matches gold. Query uses no extra sales filters. |
| retail-distinct-customer-count | needs_review | pass | 741 distinct non-null CustomerID values after the qualifying filters matches gold. |
| retail-netherlands-sales | needs_review | pass | Netherlands sales 26611.16 GBP matches gold. SQL includes country predicate and all three qualifying filters. |
| retail-scope-clarification | failed | fail | The answer correctly explains that source covers January only and gives January total while asking for missing months. However the run called register_dataset, which the case allowlist excludes; the automatic action_allowlist failure must stand. |
| survey-fy24-retail-inflation-en | needs_review | pass | Correct FY24 5.4 per cent and lowest-since-pandemic characterization. The declared original passage matches English chapter PDF page 1, paragraph 3.1; rendered source checked. |
| survey-fy24-inflation-hi-query-en-source | failed | fail | The question asks how much FY24 retail inflation was, but the Hindi answer never gives 5.4 per cent. It offers trend and core-inflation context instead. English chapter PDF page 1 establishes the missing figure. |
| survey-inflation-drivers-en | needs_review | pass | Correctly separates pandemic supply disruptions and conflict-related commodity prices from adverse-weather food-price conditions; FY22/FY23 scope matches the opening summary on rendered English chapter PDF page 1. |
| survey-inflation-policy-measures-en | needs_review | pass | All four summary measures match the original: stock management, open market operations, subsidised essential food and trade measures. Additional wheat/rice examples match Box III.2 on rendered chapter PDF page 11. |
| survey-inflation-hi-source | needs_review | partial | Retail FY24 5.4 per cent and state comparison are supported, but the food subsection answers FY22 rather than FY24. The rendered Hindi chapter PDF page 9, paragraph 3.18, shows CFPI rising from 6.6 per cent in FY23 to 7.5 per cent in FY24. The answer omits that requested FY24 food situation. |
| survey-summary-vs-chapter | failed | partial | Full-PDF FY24 5.4 per cent is correctly quoted, but from paragraph 1.40 on full PDF page 73 rather than its inflation chapter. The standalone source only receives a state-level trend statement and unresolved [e1, e22] markers; only one evidence ID is declared. It does not establish the same figure from both requested chapter sources. |
| survey-inflation-hi-cross-language | failed | partial | Correctly describes declining core inflation and distinguishes nine-year-low core services. It omits the requested four-year-low overall core conclusion, present on chapter PDF page 1. Its page-7 label applies to the services quote; the other quoted statement is from page 6. Both retrieval scope and citation precision are incomplete. |
| survey-hindi-source-search-en | needs_review | partial | The FY24 core-services nine-year-low statement is supported by Hindi chapter PDF page 6 paragraph 3.13 and the page-7 chart. Three evidence IDs are declared, but answer contains unresolved [e3, e4, e5] and no chapter PDF page number, despite the explicit page-citation request. |
| survey-appendix-table-retrieval | needs_review | fail | Finds Table 4.3 at PDF page 92 correctly, but assigns its merged headers to the wrong series. Rendered PDF pages 92-93 show CPI-IW: General; CPI-NS: Rural, Urban, Combined; CPI-AL: General; CPI-RL: General. The answer puts Rural/Urban/Combined under CPI-IW and General/Rural under CPI-NS. It also omits the continuation page 93. |
| survey-causal-overclaim | failed | pass | Correctly refuses the claim that adverse weather alone is established and describes contributing conditions without inventing causal proof. Its declared original passages on chapter PDF pages 3 and 9 support the discussion. The automatic failure requires a particular opening-summary phrase; equivalent supporting passages answer the actual question. That automatic recall failure is preserved, while task-answer review passes. |

## Sources inspected

- [inflation-en.pdf](/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/inflation-en.pdf)
- [inflation-hi.pdf](/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/inflation-hi.pdf)
- [economic-survey-2023-24-en.pdf](/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/economic-survey-2023-24-en.pdf)
- [statistical-appendix-en.pdf](/Users/hari/Desktop/sandbox/agentic-rag-analyst/evals/fixtures/real-v1/raw/statistical-appendix-en.pdf)

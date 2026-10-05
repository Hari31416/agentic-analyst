# Real-v1 four-model review

Luna reviewed all 30 retained answers for each of the four requested models. Each label binds to the exact answer SHA-256 in that model's `review.json`; `reviewed-report.json` applies those labels through `evaluation.review.apply_review`. The original run reports, automatic metrics, and statuses remain in the matrix run directory and were not edited.

| Model              | Complete | Partial | Failed | Unresolved |
| ------------------ | -------: | ------: | -----: | ---------: |
| gpt-oss-120b       |       18 |       7 |      5 |          0 |
| gemma-4-31b-it     |       19 |       6 |      5 |          0 |
| gemma-4-26B-A4B-it |       22 |       5 |      3 |          0 |
| Qwen3.6-35B-A3B    |       24 |       4 |      2 |          0 |
| Total              |       83 |      22 |     15 |          0 |

The export review caught two incomplete CSVs. Qwen3.6's `retail-sales-chart-csv` and Gemma 4 26B's `retail-export-and-chart-hi` outputs contain 20 of the 22 qualifying countries, omitting Iceland (GBP 475.39) and Israel (GBP 379.84). Their requested top-five charts are correct, so both task labels are partial. The per-artifact notes record the downloaded SHA-256, row count, missing countries, and chart inspection.

The automatic status and the task label answer different questions. The page shows each independently, along with the run state, deterministic checks, recorded tool sequence, saved evidence IDs, and artifact links. A passage or literal-number proxy miss may still have a substantively correct answer. Conversely, an output artifact can be incomplete even when its top-five chart is sound. Action-allowlist failures remain visible in the automatic checks and are not treated as passage-matching noise.

Review provenance is GPT-6 Luna, AI-assisted, first-pass, and uncalibrated. These labels do not certify human judgment, native-speaker language quality, or stable model quality. Every included case has one trial. Qwen3.5-9B is excluded at the user's request and contributes to no aggregate.

The missing benchmark coverage remains explicit: repeated model trials, native-speaker and independent human review, judge calibration, broad stage ablations, and reviewed release thresholds. This page is an exploratory comparison, not a complete quality benchmark. Its browser rendering was not visually verified because the browser security policy blocked local file URLs; data, hashes, review imports, and JavaScript syntax were checked offline.

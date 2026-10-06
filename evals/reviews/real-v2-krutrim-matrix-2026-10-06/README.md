# Real-v2 Krutrim comparison

This bundle compares the completed `evals/runs/real-v2-matrix` run: four models,
50 cases per model, one trial per case. Luna reviews the exact saved answers;
the primary agent assembles and checks the comparison. Labels are AI-assisted
and uncalibrated, not independent human or native-speaker certification.

Open `index.html` for model summaries, latency and known-response token counters,
tool statistics, search/filter controls and the case comparison dialog. The
`New 20 Cases` filter separates the v2 additions from inherited cases. Automatic
statuses and metrics remain unchanged. AI task judgments appear separately.
A complete task judgment can coexist with an automatic failure; the review note
explains the difference. Partial is displayed separately and is not a full pass.

Each model's `review.json` is tied to its experiment and SHA-256 of the exact
answer. `reviewed-report.json` attaches those labels through the existing review
import validation. `comparison.json`, `cases.csv` and `telemetry-coverage.json`
contain the data embedded in the offline HTML.

Rebuild from the repository root, with the original run files present:

```sh
backend/.venv/bin/python evals/reviews/real-v2-krutrim-matrix-2026-10-06/build_comparison.py
backend/.venv/bin/python evals/reviews/real-v2-krutrim-matrix-2026-10-06/build_viewer.py
```

The layout follows the v1 viewer. Its former hardcoded model descriptions and
score/latency/token conclusions were removed. V2 totals, counters and labels come
from this run. Saved raw reports, checkpoints, v1 reviews and case expectations
are not modified. No further model trials or rescoring are part of this review.

CSV/binary artifact contents were not cached in the run. An attempt to retrieve
requested outputs through the local read-only download API could not connect,
because the application API was no longer listening. Retained artifact metadata,
chart schemas and answer previews support limited inspection; contents remain
unverified where unavailable. Missing usage counters are disclosed, and known
response counters are not provider billing totals. One trial per question and
shared source families do not establish a stable ranking.

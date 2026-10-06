# Real-v2 addition review, 6 October 2026

The user requested 20 further questions, a Luna draft, primary checking, a v2
pack, and no model trials. V2 inherits the 30 v1 objects unchanged and adds 12
retail and 8 English document questions. Source fixtures and hashes are shared
with v1; no source downloads or v1 regeneration occurred.

Luna drafted all 20 cases. The primary agent reviewed question scope, numeric
expectations, units, source declarations, tool lists, reference claims and review
criteria. Decimal recomputation checks every new retail scalar against reviewed
values. A separate primary-authored SQLite check uses integer-penny source prices
and extended amounts to verify all 67 expectations, endpoint dates, top-three
country membership and winning StockCode. Currency/percentage answers are
rounded to two decimals; counts are exact.

The primary agent read the relevant extracted source passages and visually
inspected the eight original PDF pages. References distinguish PDF page from
printed page. Generation validates each phrase on the declared reference page,
not anywhere in the PDF. New document anchors are diagnostic; claim completeness,
interpretation and citation choice remain manual review criteria.

## Corrections made during review

- Removed character-by-character tags caused by passing a string where a tag list
  was expected. Added meaningful grouping/grain tags.
- Corrected the invoice-average rubric. Averaging qualifying invoice subtotals is
  equivalent to qualifying sales divided by distinct qualifying invoices; averaging
  UnitPrice is incorrect.
- Corrected the rural/urban basket question to request the single page containing
  both claims, PDF page 13. Page 14 continues the paragraph but is unnecessary for
  these requested facts.
- Made the qualifying-rule anchor diagnostic for the raw cancellation cross-tab,
  whose question explicitly asks for unfiltered rows.

The new cases use generous tool expectations based on their selected inputs.
Retail allows analysis, rule retrieval, artifact inspection and optional report
presentation. PDF-only questions allow discovery, retrieval, summary and evidence
inspection. The runtime exposes tools; the question need not enumerate them, and
permission does not make a tool mandatory.

## Validation and remaining review

429 deterministic tests passed, 80 live/integration tests were deselected. Focused
Black and diff checks passed. New tests load the evaluation schema, check real
runtime tool names, freeze the v1 inventory, verify source hashes and reference
pages, reject duplicate IDs and changed base inventories, independently check
numeric gold, and reproduce the generated 50-case inventory.

All labels remain `unreviewed`. This is agent drafting and agent checking, not
independent human or native-speaker certification. New questions are English;
Hindi coverage is inherited. No model trials, rescoring, judge calibration or
statistical reliability analysis occurred. Shared sources and related tasks mean
50 cases do not represent 50 independent samples of model behavior.

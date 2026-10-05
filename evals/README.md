# Synthetic benchmark fixtures

For the downloaded UCI Online Retail and Economic Survey 2023–24 evaluation
pack, see [real-data cases](real-v1.md). The synthetic pack below remains the
default regression baseline.

`fixtures/v1` is a versioned, developer-only synthetic pack. The records are
invented and do not represent real people. CSV and XLSX contain the same five
applications; PostgreSQL and MySQL scripts seed the same records into the
fixture-owned `synthetic_applications` table. English and Hindi copies of the
source note are provided as both PDF and DOCX.

## Rule and expected result

An application qualifies when its scheme is `S1`, the scheme status is
`active`, and annual income is less than or equal to INR 200,000.00. The
threshold is inclusive. Income and grant values use INR and two exact decimal
places; the CSV, SQL and workbook store them as decimal text/`DECIMAL(12,2)` so
no binary floating-point rounding is introduced.

| Application | Scheme | Status | Annual income (INR) | Grant (INR) | Expected treatment |
| --- | --- | --- | ---: | ---: | --- |
| APP-001 | S1 | active | 180000.00 | 10000.00 | Qualifies |
| APP-002 | S1 | active | 200000.00 | 15000.00 | Qualifies at inclusive boundary |
| APP-003 | S1 | active | 200000.01 | 9000.01 | Excluded: income exceeds threshold by 0.01 |
| APP-004 | S1 | inactive | 120000.00 | 8000.00 | Excluded: scheme inactive |
| APP-005 | S2 | active | 150000.00 | 5000.50 | Excluded: different scheme |

The expected qualifying IDs are APP-001 and APP-002, count `2`, and grant
total INR `25000.00`. Machine-readable values are in `fixtures/v1/expected.json`.
All generated files except the hash manifest and both bundled fonts/license
files are SHA-256 listed in `fixtures/v1/manifest.json`; font assets are recorded
under `font_inputs` in that manifest.

## Regenerate and seed

From the repository root, with the backend development dependencies installed:

```sh
cd backend
uv run --group dev python ../evals/generators/generate_v1.py
```

The generator defaults to `evals/fixtures/v1`; pass `--output /path/to/output`
to write another copy. It fixes Office ZIP member timestamps, Office document
properties, PDF metadata, ordering, and line endings before computing hashes.
The SQL scripts are explicit developer-only fixture seeds and should only be
run against a developer database. They delete/reinsert rows with the `APP-%`
fixture IDs in the dedicated table.

The Hindi PDF uses bundled Noto Sans Devanagari and Noto Sans fonts with
ReportLab 5 and HarfBuzz shaping, so it does not depend on host fonts. Both fonts
come from the [Noto Fonts repository](https://github.com/notofonts/noto-fonts/tree/main/hinted/ttf)
and are distributed under SIL Open Font License 1.1; their license texts are
in `fonts/OFL-NotoSansDevanagari.txt` and `fonts/OFL-NotoSans.txt`. Hindi PDF
generation requires ReportLab 5 with `uharfbuzz`; generation fails clearly if
shaping support is unavailable. Keep the backend dependency lock fixed when
comparing generated PDF hashes.

Run the isolated pack checks with `cd backend && uv run pytest ../evals/tests`.

## Phase 09 evaluation runner

The first repeatable public-API runner uses `cases/core-v1.json`. Start with
`make eval-list` and `make eval-check`; `make eval-live` explicitly runs the
configured model. See [the evaluation guide](../docs/evaluation.md) for bounded
trials, checkpoint/resume, offline rescoring, HTML/JSON/CSV reports and human
review. Synthetic labels and missing judge calibration remain visible.

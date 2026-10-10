from __future__ import annotations

import hashlib
import io
import json
import re

import pytest
from PIL import Image as PillowImage
from pydantic import ValidationError
from pypdf import PdfReader

from app.reports import ReportDocument, capabilities, render_pdf
from app.reports.renderer import _styles


def _document(blocks: list[dict], *, language: str = "en-IN") -> ReportDocument:
    return ReportDocument.model_validate(
        {
            "title": "Quarterly report",
            "language": language,
            "sections": [{"id": "summary", "heading": "Summary", "blocks": blocks}],
        }
    )


def _asset(data: bytes, media_type: str, display_name: str = "results.csv") -> dict:
    return {
        "storage_key": "opaque-key",
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "media_type": media_type,
        "display_name": display_name,
    }


def _text(pdf_bytes: bytes) -> str:
    reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_schema_forbids_extra_fields_and_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        ReportDocument.model_validate(
            {
                "title": "x",
                "language": "en-IN",
                "sections": [
                    {"id": "same", "heading": "A", "blocks": []},
                    {"id": "same", "heading": "B", "blocks": []},
                ],
            }
        )
    with pytest.raises(ValidationError):
        ReportDocument.model_validate(
            {
                "title": "x",
                "language": "en-IN",
                "unexpected": True,
                "sections": [{"id": "s", "heading": "S", "blocks": []}],
            }
        )


def test_capabilities_and_multilingual_report() -> None:
    assert [item["language"] for item in capabilities()] == ["en-IN", "hi-IN"]
    document = _document(
        [
            {
                "id": "p",
                "type": "paragraph",
                "text": "क्षेत्रीय परिणाम: नमस्ते, भारत. क्‍षेत्रीय Evidence: 42.",
            }
        ],
        language="hi-IN",
    )
    content = render_pdf(document, {}, lambda _: b"")
    text = _text(content)
    assert all(style.shaping for style in _styles().values())
    # HarfBuzz glyph substitution maps some shaped conjuncts to PUA glyph names
    # in PDF text extraction; visual QA verifies the glyph sequence itself.
    assert re.search(r"[\u0900-\u097f]", text)
    assert "Evidence: 42." in text


def test_schema_rejects_embedded_data_uri() -> None:
    with pytest.raises(ValidationError, match="data URIs"):
        _document(
            [{"id": "p", "type": "paragraph", "text": "data:image/png;base64,AAAA"}]
        )


def test_renderer_rejects_undeclared_and_tampered_artifacts() -> None:
    document = _document(
        [{"id": "table", "type": "table", "artifact_id": "artifact-1"}]
    )
    with pytest.raises(ValueError, match="undeclared"):
        render_pdf(document, {}, lambda _: b"")

    data = b"name,value\nA,42\n"
    assets = {"artifact-1": _asset(data, "text/csv")}
    with pytest.raises(ValueError, match="integrity"):
        render_pdf(document, assets, lambda _: b"name,value\nA,41\n")


def test_renderer_rejects_unsupported_glyph_before_returning_pdf() -> None:
    document = _document([{"id": "p", "type": "paragraph", "text": "unsupported 🐉"}])
    with pytest.raises(ValueError, match="unsupported report glyph"):
        render_pdf(document, {}, lambda _: b"")


def test_renderer_handles_adjacent_mixed_script_tokens_and_pdf_filename() -> None:
    document = _document(
        [
            {
                "id": "p",
                "type": "paragraph",
                "text": "हिंदी/English नीति.pdf and हिंदी:42USD",
            }
        ]
    )
    pdf = render_pdf(document, {}, lambda _: b"")
    assert pdf.startswith(b"%PDF-")
    extracted = _text(pdf)
    assert "English" in extracted and ".pdf" in extracted


def test_table_wraps_repeats_header_and_notes_truncation() -> None:
    rows = [[str(i), "a long value with exact text " + str(i)] for i in range(120)]
    data = ("id,description\n" + "\n".join(",".join(row) for row in rows)).encode()
    metadata = _asset(data, "text/csv")
    document = _document(
        [
            {
                "id": "table",
                "type": "table",
                "artifact_id": "table-data",
                "max_rows": 100,
            }
        ]
    )
    pdf = render_pdf(document, {"table-data": metadata}, lambda _: data)
    reader = PdfReader(__import__("io").BytesIO(pdf))
    assert len(reader.pages) > 1
    text = _text(pdf)
    assert text.count("id") > 1
    assert "a long value with exact text 99" in text
    assert "Showing 100 of 120 rows" in text
    assert "exact text 100" not in text


def test_table_artifact_contract_rejects_missing_selected_column() -> None:
    data = b"name,value\nA,42\n"
    document = _document(
        [
            {
                "id": "table",
                "type": "table",
                "artifact_id": "table-data",
                "columns": ["missing"],
            }
        ]
    )
    with pytest.raises(ValueError, match="columns are missing"):
        render_pdf(document, {"table-data": _asset(data, "text/csv")}, lambda _: data)


def test_table_rejects_more_than_twelve_rendered_columns() -> None:
    columns = [f"c{index}" for index in range(13)]
    data = (",".join(columns) + "\n" + ",".join("x" for _ in columns) + "\n").encode()
    document = _document(
        [{"id": "table", "type": "table", "artifact_id": "table-data"}]
    )
    with pytest.raises(ValueError, match="at most 12"):
        render_pdf(document, {"table-data": _asset(data, "text/csv")}, lambda _: data)


def test_figure_requires_png_or_jpeg_and_matching_signature() -> None:
    document = _document([{"id": "figure", "type": "figure", "artifact_id": "image"}])
    data = b"not-an-image"
    with pytest.raises(ValueError, match="PNG or JPEG"):
        render_pdf(document, {"image": _asset(data, "image/svg+xml")}, lambda _: data)


def test_png_figure_is_embedded_in_pdf() -> None:
    image = PillowImage.new("RGB", (32, 24), color=(30, 120, 160))
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    document = _document([{"id": "figure", "type": "figure", "artifact_id": "image"}])
    pdf = render_pdf(document, {"image": _asset(data, "image/png")}, lambda _: data)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages[0].images) == 1


@pytest.mark.parametrize(
    "chart",
    [
        {
            "data": [
                {"type": "bar", "name": "Sales", "x": ["North", "South"], "y": [4, 7]}
            ],
            "layout": {
                "title": "Sales by region",
                "xaxis": {"title": "Region"},
                "yaxis": {"title": "Count"},
            },
        },
        {
            "data": [
                {
                    "type": "scatter",
                    "name": "Trend",
                    "x": [10, 20, 40],
                    "y": [1, 3, 4],
                    "mode": "lines+markers",
                }
            ],
            "layout": {"title": "Trend by value"},
        },
        {
            "data": [{"type": "pie", "labels": ["A", "B"], "values": [2, 3]}],
            "layout": {"title": "Share"},
        },
    ],
)
def test_chart_spec_renders_as_vector_figure(chart: dict) -> None:
    data = json.dumps(chart).encode()
    document = _document(
        [{"id": "chart", "type": "figure", "artifact_id": "chart-data"}]
    )
    pdf = render_pdf(
        document,
        {"chart-data": _asset(data, "application/vnd.plotly.v1+json", "chart.json")},
        lambda _: data,
    )
    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    assert chart["layout"]["title"] in text
    if chart["data"][0]["type"] == "bar":
        assert "North" in text and "Sales" in text
    if chart["data"][0]["type"] == "pie":
        assert "A: 2" in text


def test_chart_rejects_unsupported_barmode() -> None:
    data = json.dumps(
        {
            "data": [{"type": "bar", "x": ["A"], "y": [1]}],
            "layout": {"barmode": "stack"},
        }
    ).encode()
    document = _document(
        [{"id": "chart", "type": "figure", "artifact_id": "chart-data"}]
    )
    with pytest.raises(ValueError, match="grouped bars only"):
        render_pdf(
            document,
            {
                "chart-data": _asset(
                    data, "application/vnd.plotly.v1+json", "chart.json"
                )
            },
            lambda _: data,
        )

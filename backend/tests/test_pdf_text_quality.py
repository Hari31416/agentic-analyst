from __future__ import annotations

import pytest

from app.ingestion import extractors
from app.ingestion.pdf_text import (
    PdfTextLimitExceeded,
    bounded_plain_text,
    legacy_font_names,
    normalize_pdf_whitespace,
)
from app.sources.documents import DocumentIngestionError


class _Page:
    def __init__(self, text: str, fonts: dict[str, dict[str, str]] | None = None):
        self.text = text
        self.fonts = fonts or {}

    def get(self, key: str, default=None):
        if key == "/Resources":
            return {"/Font": self.fonts}
        return default

    def extract_text(self, visitor_text=None, extraction_mode=None):
        if visitor_text:
            visitor_text(self.text)
        return self.text


class _Reader:
    def __init__(self, pages):
        self.pages = pages
        self.is_encrypted = False
        self.outline = []


def test_legacy_font_detection_handles_subset_names_and_known_families():
    page = _Page(
        "garbled latin",
        {
            "/F1": {"/BaseFont": "/WBPYMF+Walkman-Chanakya905Normal"},
            "/F2": {"/BaseFont": "/ABCDEF+Kruti Dev 010"},
            "/F3": {"/BaseFont": "/TimesNewRomanPSMT"},
        },
    )
    assert legacy_font_names(page) == ["Kruti Dev 010", "Walkman-Chanakya905Normal"]


def test_page_text_is_bounded_before_fragments_are_accumulated():
    page = _Page("हिंदी")
    assert bounded_plain_text(page, len("हिंदी".encode("utf-8"))) == "हिंदी"
    with pytest.raises(PdfTextLimitExceeded):
        bounded_plain_text(page, len("हिंदी".encode("utf-8")) - 1)


def test_whitespace_normalization_removes_layout_padding_and_keeps_tabs():
    assert normalize_pdf_whitespace("a   b\t c   \n") == "a b\t c"


def test_legacy_page_only_publishes_ocr_text(monkeypatch: pytest.MonkeyPatch):
    page = _Page(
        "vkfFkZd loZs{k.k",
        {"/F1": {"/BaseFont": "/WBPYMF+Walkman-Chanakya905Normal"}},
    )
    monkeypatch.setattr(
        extractors, "PdfReader", lambda *args, **kwargs: _Reader([page])
    )
    seen = []

    def fake_ocr(content, page_number, languages, timeout):
        seen.append((content, page_number, languages, timeout))
        return "आर्थिक सर्वेक्षण", None, {"ocr_confidence": 88.0}

    monkeypatch.setattr(extractors, "_ocr_page", fake_ocr)
    blocks, warnings, _ = extractors.extract_pdf(b"original-pdf")

    assert seen == [(b"original-pdf", 1, "eng+hin", 20)]
    assert len(blocks) == 1
    assert blocks[0].text == "आर्थिक सर्वेक्षण"
    assert blocks[0].location["extractor"] == "ocr"
    assert blocks[0].location["legacy_fonts"] == ["Walkman-Chanakya905Normal"]
    assert "legacy_font_pages_detected:1" in warnings
    assert "vkfFkZd" not in " ".join(block.text for block in blocks)


def test_legacy_ocr_failure_rejects_document_without_publishing_partial_blocks(
    monkeypatch: pytest.MonkeyPatch,
):
    page = _Page(
        "dhersa vkSj eqækLiQhfr",
        {"/F1": {"/BaseFont": "/TZBGWL+WalkmanChanakya901Bold"}},
    )
    monkeypatch.setattr(
        extractors, "PdfReader", lambda *args, **kwargs: _Reader([page])
    )
    monkeypatch.setattr(
        extractors,
        "_ocr_page",
        lambda *args, **kwargs: ("", "ocr_unavailable:tesseract_missing", {}),
    )

    with pytest.raises(DocumentIngestionError) as error:
        extractors.extract_pdf(b"original-pdf")

    assert error.value.code == "legacy_font_ocr_failed"
    assert "1 (ocr_unavailable:tesseract_missing)" in str(error.value)
    assert "retry with working Hindi language assets" in str(error.value)


def test_failed_legacy_page_rejects_otherwise_extractable_document(
    monkeypatch: pytest.MonkeyPatch,
):
    ordinary_page = _Page("A complete ordinary digital page with enough text.")
    legacy_page = _Page(
        "dhersa vkSj eqækLiQhfr",
        {"/F1": {"/BaseFont": "/TZBGWL+WalkmanChanakya901Bold"}},
    )
    monkeypatch.setattr(
        extractors,
        "PdfReader",
        lambda *args, **kwargs: _Reader([ordinary_page, legacy_page]),
    )
    monkeypatch.setattr(
        extractors,
        "_ocr_page",
        lambda *args, **kwargs: ("", "ocr_timeout", {}),
    )

    with pytest.raises(DocumentIngestionError) as error:
        extractors.extract_pdf(b"original-pdf")

    assert error.value.code == "legacy_font_ocr_failed"
    assert "2 (ocr_timeout)" in str(error.value)
    assert "A complete ordinary digital page" not in str(error.value)


def test_legacy_page_count_over_ocr_limit_fails_before_any_ocr(monkeypatch):
    font = {"/F1": {"/BaseFont": "/WBPYMF+Walkman-Chanakya905Normal"}}
    pages = [_Page("legacy", font) for _ in range(extractors.MAX_OCR_PAGES + 1)]
    monkeypatch.setattr(extractors, "PdfReader", lambda *args, **kwargs: _Reader(pages))
    monkeypatch.setattr(
        extractors,
        "_ocr_page",
        lambda *args, **kwargs: pytest.fail("preflight must run before OCR"),
    )

    with pytest.raises(DocumentIngestionError) as error:
        extractors.extract_pdf(b"original-pdf")

    assert error.value.code == "ocr_page_limit"
    assert "101 pages" in str(error.value)
    assert "Split the PDF" in str(error.value)

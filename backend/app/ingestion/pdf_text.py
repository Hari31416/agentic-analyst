"""Small, bounded helpers for deciding whether PDF text is trustworthy."""

from __future__ import annotations

import re
from typing import Any

_LEGACY_FONT_FAMILY = re.compile(
    r"(?:chanakya|kruti\s*dev|devlys|shusha|aps\s*dv|krutidev)", re.IGNORECASE
)


class PdfTextLimitExceeded(Exception):
    """Raised from a pypdf visitor before a page exceeds its remaining budget."""


def legacy_font_names(page: Any) -> list[str]:
    """Return embedded font names whose encodings commonly need legacy mapping."""
    names: set[str] = set()
    try:
        resources = page.get("/Resources")
        if resources is None:
            return []
        resources = (
            resources.get_object() if hasattr(resources, "get_object") else resources
        )
        fonts = resources.get("/Font", {})
        fonts = fonts.get_object() if hasattr(fonts, "get_object") else fonts
        for font in fonts.values():
            font = font.get_object() if hasattr(font, "get_object") else font
            base_font = str(font.get("/BaseFont", "")).lstrip("/")
            # Font programs are commonly subset with a six-letter prefix.
            base_font = re.sub(r"^[A-Z]{6}\+", "", base_font)
            if _LEGACY_FONT_FAMILY.search(base_font):
                names.add(base_font)
    except (AttributeError, TypeError, ValueError):
        return []
    return sorted(names)


def bounded_plain_text(page: Any, max_bytes: int) -> str:
    """Extract ordinary PDF text while bounding visitor accumulation by UTF-8 size."""
    if max_bytes < 0:
        raise PdfTextLimitExceeded
    fragments: list[str] = []
    used = 0

    def visit(text: str, *_: Any) -> None:
        nonlocal used
        if not text:
            return
        used += len(text.encode("utf-8", errors="replace"))
        if used > max_bytes:
            raise PdfTextLimitExceeded
        fragments.append(text)

    page.extract_text(visitor_text=visit)
    return "".join(fragments)


def normalize_pdf_whitespace(text: str) -> str:
    """Trim layout padding while retaining line and tab boundaries."""
    lines: list[str] = []
    for line in text.replace("\x00", "").splitlines():
        # Pypdf layout output can represent page geometry as very long runs of
        # spaces. Preserve tabs/newlines (useful column and paragraph boundaries)
        # while removing only horizontal padding between printable text.
        line = re.sub(r"[ \f\v]+$", "", line)
        line = re.sub(r" {2,}", " ", line)
        lines.append(line)
    return "\n".join(lines)

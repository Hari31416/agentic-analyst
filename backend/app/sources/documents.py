"""Safe PDF/DOCX extraction and a conservative, versioned chunker."""

from __future__ import annotations

import io
import hashlib
import json
import re
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from docx import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import (
    Document,
    DocumentBlock,
    DocumentChunk,
    Source,
    Workspace,
)
from app.storage.filesystem import Storage

EXTRACTOR_VERSION = "pdf-docx-text-v1"
CHUNKER_VERSION = "structure-token-v2"
BLOCK_SCHEMA_VERSION = "document-block-v1"
MAX_ARCHIVE_MEMBERS = 4_096
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_RATIO = 100
MAX_PDF_PAGES = 2_000
MAX_EXTRACTED_BYTES = 12 * 1024 * 1024
MAX_BLOCK_CHARS = 250_000
CHUNK_BODY_TOKENS = 384
CHUNK_OVERLAP_TOKENS = 48
FALLBACK_BODY_BYTES = 480
FALLBACK_OVERLAP_BYTES = 48
HEADING_CONTEXT_TOKENS = 32
FALLBACK_HEADING_BYTES = 16
MAX_BLOCKS = 100_000
MAX_CHUNKS = 512
_ENGLISH_MARKERS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
}
_DOCTYPE_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class DocumentIngestionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExtractedBlock:
    kind: str
    text: str
    heading: str | None
    location: dict[str, Any]
    language: str
    scripts: list[str]


@dataclass(frozen=True)
class ChunkData:
    text: str
    normalized_text: str
    heading: str | None
    location: dict[str, Any]
    language: str
    block_ids: list[str]
    token_count: int


@dataclass(frozen=True)
class TokenizerProfile:
    tokenizer: Any | None
    method: str
    sha256: str | None


@dataclass(frozen=True)
class TextGroup:
    text: str
    heading: str | None
    language: str
    block_ids: list[int]
    spans: list[tuple[int, int, int]]
    locations: list[dict[str, Any]]
    table_id: int | None = None


def validate_document_upload(filename: str, content: bytes, max_bytes: int) -> str:
    if not content or len(content) > max_bytes:
        code = "upload_too_large" if len(content) > max_bytes else "empty_upload"
        raise DocumentIngestionError(
            code, "Document upload is empty or exceeds the size limit."
        )
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix == ".pdf" and content.startswith(b"%PDF-"):
        return "pdf"
    if suffix == ".docx" and content.startswith(b"PK"):
        _validate_docx_archive(content)
        return "docx"
    if suffix in {".pdf", ".docx"}:
        raise DocumentIngestionError(
            "file_type_mismatch",
            "The file extension does not match a supported PDF or DOCX document.",
        )
    raise DocumentIngestionError(
        "file_type_unsupported", "Only PDF and DOCX documents are supported."
    )


def _validate_docx_archive(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise DocumentIngestionError(
                    "archive_too_large", "DOCX archive contains too many members."
                )
            total_size = 0
            names: set[str] = set()
            has_external_relationships = False
            for member in members:
                name = member.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or name in names:
                    raise DocumentIngestionError(
                        "unsafe_archive", "DOCX archive contains an unsafe path."
                    )
                names.add(name)
                if member.file_size > MAX_MEMBER_BYTES:
                    raise DocumentIngestionError(
                        "archive_member_too_large",
                        "DOCX archive member exceeds the size limit.",
                    )
                total_size += member.file_size
                if member.file_size > max(1, member.compress_size) * MAX_RATIO:
                    raise DocumentIngestionError(
                        "archive_ratio_exceeded",
                        "DOCX archive compression ratio exceeds the limit.",
                    )
                lowered = name.lower()
                if any(
                    part in lowered
                    for part in ("vbaproject.bin", "activex/", "embeddings/")
                ):
                    raise DocumentIngestionError(
                        "macros_rejected",
                        "DOCX macros and embedded active content are not supported.",
                    )
                if lowered.endswith(".xml") or lowered.endswith(".rels"):
                    raw = archive.read(member)
                    if _DOCTYPE_RE.search(raw):
                        raise DocumentIngestionError(
                            "unsafe_xml", "DOCX contains a prohibited XML declaration."
                        )
                    if lowered.endswith(".rels") and re.search(
                        rb"TargetMode\s*=\s*['\"]External['\"]", raw, re.IGNORECASE
                    ):
                        has_external_relationships = True
                    if (
                        lowered.endswith("[content_types].xml")
                        and b"macroenabled" in raw.lower()
                    ):
                        raise DocumentIngestionError(
                            "macros_rejected",
                            "Macro-enabled Office documents are not supported.",
                        )
            if total_size > MAX_ARCHIVE_BYTES:
                raise DocumentIngestionError(
                    "archive_too_large", "DOCX expanded content exceeds the size limit."
                )
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise DocumentIngestionError(
                    "invalid_docx", "The ZIP file is not a valid DOCX document."
                )
            return has_external_relationships
    except DocumentIngestionError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise DocumentIngestionError(
            "invalid_docx", "The DOCX package is invalid."
        ) from error


def _script_and_language(text: str) -> tuple[str, list[str]]:
    scripts: set[str] = set()
    saw_latin = False
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "DEVANAGARI" in name:
            scripts.add("Devanagari")
        elif "LATIN" in name:
            scripts.add("Latin")
            saw_latin = True
        elif name:
            scripts.add(name.split(" ", 1)[0].title())
    if scripts == {"Devanagari"}:
        language = "hi-IN"
    elif scripts == {"Latin"}:
        latin_words = re.findall(r"[a-z]+", text.lower())[:500] if saw_latin else []
        markers = sum(word in _ENGLISH_MARKERS for word in latin_words)
        language = "en-IN" if markers >= 2 else "und"
    else:
        language = "und"
    return language, sorted(scripts)


def _block(
    kind: str, text: str, heading: str | None, location: dict[str, Any]
) -> ExtractedBlock:
    language, scripts = _script_and_language(text)
    return ExtractedBlock(kind, text, heading, location, language, scripts)


def _extract_pdf(content: bytes) -> tuple[list[ExtractedBlock], list[str], list[str]]:
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DocumentIngestionError(
                "encrypted_pdf", "Encrypted PDFs are not supported."
            )
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentIngestionError(
                "pdf_page_limit", "PDF has too many pages to process."
            )
        blocks: list[ExtractedBlock] = []
        warnings: list[str] = []
        empty_pages: list[int] = []
        total_bytes = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except Exception as error:
                raise DocumentIngestionError(
                    "pdf_extraction_failed", "PDF text extraction failed."
                ) from error
            text = text.replace("\x00", "")
            if not text.strip():
                empty_pages.append(page_number)
                continue
            total_bytes += len(text.encode("utf-8"))
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise DocumentIngestionError(
                    "extracted_text_too_large",
                    "Extracted document text exceeds the processing limit.",
                )
            if len(text) > MAX_BLOCK_CHARS:
                for start in range(0, len(text), MAX_BLOCK_CHARS):
                    page_text = text[start : start + MAX_BLOCK_CHARS]
                    blocks.append(
                        _block(
                            "page_text",
                            page_text,
                            None,
                            {
                                "page": page_number,
                                "char_start": start,
                                "char_end": start + len(page_text),
                            },
                        )
                    )
            else:
                blocks.append(
                    _block(
                        "page_text",
                        text,
                        None,
                        {"page": page_number, "char_start": 0, "char_end": len(text)},
                    )
                )
        if empty_pages:
            warnings.append(
                "ocr_needed_pages:" + ",".join(str(page) for page in empty_pages[:100])
            )
        if not blocks:
            return [], warnings + ["ocr_needed"], []
        if empty_pages:
            warnings.append(
                "Some PDF pages contain no extractable text and may require OCR."
            )
        language_tags = sorted({block.language for block in blocks})
        return blocks, warnings, language_tags
    except DocumentIngestionError:
        raise
    except Exception as error:
        raise DocumentIngestionError(
            "invalid_pdf", "The PDF could not be read safely."
        ) from error


def _xml_text_guard(content: bytes) -> None:
    if _DOCTYPE_RE.search(content):
        raise DocumentIngestionError(
            "unsafe_xml", "DOCX contains a prohibited XML declaration."
        )
    try:
        ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise DocumentIngestionError(
            "invalid_docx_xml", "DOCX contains malformed XML."
        ) from error


def _extract_docx(content: bytes) -> tuple[list[ExtractedBlock], list[str], list[str]]:
    external_links = _validate_docx_archive(content)
    warnings = ["External document links were not followed."] if external_links else []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml_bytes = archive.read("word/document.xml")
        _xml_text_guard(xml_bytes)
    try:
        doc = WordDocument(io.BytesIO(content))
    except Exception as error:
        raise DocumentIngestionError(
            "invalid_docx", "The DOCX could not be read safely."
        ) from error
    blocks: list[ExtractedBlock] = []
    headings: list[str] = []
    paragraph_index = 0
    table_index = 0
    total_bytes = 0
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, doc)
            text = paragraph.text
            location = {"paragraph": paragraph_index}
            paragraph_index += 1
            if not text.strip():
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            match = re.fullmatch(r"Heading\s+(\d+)", style, flags=re.IGNORECASE)
            if match:
                level = max(1, min(9, int(match.group(1))))
                headings = headings[: level - 1]
                headings.append(text)
                blocks.append(_block("heading", text, " > ".join(headings), location))
            else:
                blocks.append(
                    _block("paragraph", text, " > ".join(headings) or None, location)
                )
            total_bytes += len(text.encode("utf-8"))
        elif child.tag.endswith("}tbl"):
            table = Table(child, doc)
            if len(table.rows) == 0:
                table_index += 1
                continue
            headers = [cell.text.replace("\n", " ") for cell in table.rows[0].cells]
            for row_index, row in enumerate(table.rows):
                values = [cell.text.replace("\n", " ") for cell in row.cells]
                # Include the header with each data row so row chunks retain table meaning.
                cells = (
                    values
                    if row_index == 0
                    else [
                        f"{headers[index] if index < len(headers) and headers[index] else f'Column {index + 1}'}: {value}"
                        for index, value in enumerate(values)
                    ]
                )
                text = " | ".join(cells)
                if not text.strip(" |:"):
                    continue
                total_bytes += len(text.encode("utf-8"))
                blocks.append(
                    _block(
                        "table_row",
                        text,
                        " > ".join(headings) or None,
                        {
                            "table": table_index,
                            "row": row_index,
                            "cell_count": len(values),
                        },
                    )
                )
            table_index += 1
    if total_bytes > MAX_EXTRACTED_BYTES:
        raise DocumentIngestionError(
            "extracted_text_too_large",
            "Extracted document text exceeds the processing limit.",
        )
    if not blocks:
        raise DocumentIngestionError(
            "empty_document", "DOCX contains no extractable text."
        )
    languages = sorted({block.language for block in blocks})
    return blocks, warnings, languages


def extract_document(
    filename: str, content: bytes
) -> tuple[list[ExtractedBlock], list[str], list[str]]:
    kind = validate_document_upload(filename, content, max(len(content), 1))
    if kind == "pdf":
        return _extract_pdf(content)
    return _extract_docx(content)


def _tokenizer_profile(settings: Settings) -> TokenizerProfile:
    """Load the tokenizer only when it matches the pinned local model manifest."""
    if settings.embedding_model_path is None:
        return TokenizerProfile(None, "utf8-byte-fallback-480-v2", None)
    root = settings.embedding_model_path
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    root = root.resolve()
    try:
        manifest = json.loads((root / "analyst-model.json").read_text(encoding="utf-8"))
        if manifest.get("model_id") != settings.embedding_model or (
            settings.embedding_revision
            and manifest.get("revision") != settings.embedding_revision
        ):
            return TokenizerProfile(None, "utf8-byte-fallback-480-v2", None)
        expected = manifest.get("sha256", {}).get("tokenizer.json")
        tokenizer_path = (root / "tokenizer.json").resolve()
        if (
            not isinstance(expected, str)
            or root not in tokenizer_path.parents
            or not tokenizer_path.is_file()
        ):
            return TokenizerProfile(None, "utf8-byte-fallback-480-v2", None)
        digest = hashlib.sha256(tokenizer_path.read_bytes()).hexdigest()
        if digest != expected:
            return TokenizerProfile(None, "utf8-byte-fallback-480-v2", None)
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return TokenizerProfile(tokenizer, "tokenizer-json-v2", digest)
    except Exception:
        return TokenizerProfile(None, "utf8-byte-fallback-480-v2", None)


def _group_blocks(blocks: Sequence[ExtractedBlock]) -> list[TextGroup]:
    groups: list[TextGroup] = []
    active_heading: str | None = None
    heading_block_ids: list[int] = []
    narrative: list[tuple[int, ExtractedBlock]] = []

    def flush_narrative() -> None:
        if not narrative:
            return
        pieces: list[str] = []
        spans: list[tuple[int, int, int]] = []
        cursor = 0
        for block_index, block in narrative:
            if pieces:
                pieces.append("\n\n")
                cursor += 2
            start = cursor
            pieces.append(block.text)
            cursor += len(block.text)
            spans.append((start, cursor, block_index))
        languages = {block.language for _, block in narrative}
        groups.append(
            TextGroup(
                text="".join(pieces),
                heading=active_heading,
                language=next(iter(languages)) if len(languages) == 1 else "und",
                block_ids=heading_block_ids + [index for index, _ in narrative],
                spans=spans,
                locations=[block.location for _, block in narrative],
            )
        )
        narrative.clear()

    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind == "heading":
            flush_narrative()
            active_heading = block.heading or block.text.strip()
            heading_block_ids = [index]
            index += 1
            continue
        if block.kind == "table_row":
            flush_narrative()
            table_id = int(block.location.get("table", -1))
            table_rows: list[tuple[int, ExtractedBlock]] = []
            while index < len(blocks):
                row = blocks[index]
                if (
                    row.kind != "table_row"
                    or int(row.location.get("table", -1)) != table_id
                    or row.heading != block.heading
                ):
                    break
                table_rows.append((index, row))
                index += 1
            pieces: list[str] = []
            spans: list[tuple[int, int, int]] = []
            cursor = 0
            for row_index, row in table_rows:
                if pieces:
                    pieces.append("\n")
                    cursor += 1
                start = cursor
                pieces.append(row.text)
                cursor += len(row.text)
                spans.append((start, cursor, row_index))
            row_languages = {row.language for _, row in table_rows}
            groups.append(
                TextGroup(
                    text="".join(pieces),
                    heading=block.heading or active_heading,
                    language=(
                        next(iter(row_languages)) if len(row_languages) == 1 else "und"
                    ),
                    block_ids=heading_block_ids
                    + [row_index for row_index, _ in table_rows],
                    spans=spans,
                    locations=[row.location for _, row in table_rows],
                    table_id=table_id,
                )
            )
            continue
        if narrative and block.heading != narrative[-1][1].heading:
            flush_narrative()
        if not narrative and block.heading != active_heading:
            active_heading = block.heading
            heading_block_ids = []
        narrative.append((index, block))
        index += 1
    flush_narrative()
    return groups


def _bounded_heading(heading: str | None, profile: TokenizerProfile) -> str | None:
    if not heading:
        return None
    if profile.tokenizer is not None:
        offsets = profile.tokenizer.encode(heading, add_special_tokens=False).offsets
        if len(offsets) <= HEADING_CONTEXT_TOKENS:
            return heading
        return heading[: offsets[HEADING_CONTEXT_TOKENS][0]].rstrip()
    raw = heading.encode("utf-8")[:FALLBACK_HEADING_BYTES]
    return raw.decode("utf-8", errors="ignore").rstrip()


def _actual_windows(text: str, profile: TokenizerProfile) -> list[tuple[int, int, int]]:
    tokenizer = profile.tokenizer
    assert tokenizer is not None
    offsets = tokenizer.encode(text, add_special_tokens=False).offsets
    if not offsets:
        return []
    windows: list[tuple[int, int, int]] = []
    body_start = 0
    while body_start < len(offsets):
        start = max(0, body_start - CHUNK_OVERLAP_TOKENS)
        end = min(len(offsets), body_start + CHUNK_BODY_TOKENS)
        char_start = 0 if start == 0 else offsets[start][0]
        char_end = len(text) if end == len(offsets) else offsets[end][0]
        windows.append((char_start, char_end, end - start))
        if end == len(offsets):
            break
        body_start += CHUNK_BODY_TOKENS
    return windows


def _fallback_windows(text: str) -> list[tuple[int, int, int]]:
    offsets = [
        (index, index + 1, len(char.encode("utf-8"))) for index, char in enumerate(text)
    ]
    windows: list[tuple[int, int, int]] = []
    body_start = 0
    window_start = 0
    while body_start < len(offsets):
        start = window_start
        end = window_start
        byte_count = 0
        while (
            end < len(offsets) and byte_count + offsets[end][2] <= FALLBACK_BODY_BYTES
        ):
            byte_count += offsets[end][2]
            end += 1
        if end == start:
            byte_count = offsets[end][2]
            end += 1
        windows.append((offsets[start][0], offsets[end - 1][1], byte_count))
        if end == len(offsets):
            break
        body_start = end
        window_start = body_start
        overlap = 0
        while (
            window_start > 0
            and overlap + offsets[window_start - 1][2] <= FALLBACK_OVERLAP_BYTES
        ):
            window_start -= 1
            overlap += offsets[window_start][2]
    return windows


def _group_location(group: TextGroup, used_block_ids: list[int]) -> dict[str, Any]:
    used_set = set(used_block_ids)
    selected = [
        location
        for (_, _, block_id), location in zip(group.spans, group.locations, strict=True)
        if block_id in used_set and location
    ]
    if group.table_id is not None:
        rows = [int(location.get("row", 0)) for location in selected]
        return {
            "type": "table",
            "table": group.table_id,
            "row_start": min(rows, default=0),
            "row_end": max(rows, default=0),
        }
    pages = [int(location["page"]) for location in selected if "page" in location]
    if pages:
        location: dict[str, Any] = {
            "type": "pages",
            "page_start": min(pages),
            "page_end": max(pages),
        }
        if min(pages) == max(pages):
            location["page"] = pages[0]
        return location
    paragraphs = [
        int(location["paragraph"]) for location in selected if "paragraph" in location
    ]
    if paragraphs:
        return {
            "type": "paragraphs",
            "paragraph_start": min(paragraphs),
            "paragraph_end": max(paragraphs),
        }
    return {
        "type": "blocks",
        "block_start": min(used_block_ids, default=0),
        "block_end": max(used_block_ids, default=0),
    }


def _make_chunks(
    blocks: Sequence[ExtractedBlock], profile: TokenizerProfile | None = None
) -> list[ChunkData]:
    profile = profile or TokenizerProfile(None, "utf8-byte-fallback-480-v2", None)
    chunks: list[ChunkData] = []
    for group in _group_blocks(blocks):
        windows = (
            _actual_windows(group.text, profile)
            if profile.tokenizer is not None
            else _fallback_windows(group.text)
        )
        heading = _bounded_heading(group.heading, profile)
        for start, end, token_count in windows:
            text = group.text[start:end]
            if not text.strip():
                continue
            used = [
                block_id
                for block_start, block_end, block_id in group.spans
                if block_end > start and block_start < end
            ]
            heading_ids = [
                block_id
                for block_id in group.block_ids
                if block_id not in {span[2] for span in group.spans}
            ]
            block_ids = list(dict.fromkeys(heading_ids + used))
            chunks.append(
                ChunkData(
                    text=text,
                    normalized_text=unicodedata.normalize("NFKC", text).casefold(),
                    heading=heading,
                    location=_group_location(group, used),
                    language=group.language,
                    block_ids=[str(value) for value in block_ids],
                    token_count=token_count,
                )
            )
            if len(chunks) > MAX_CHUNKS:
                raise DocumentIngestionError(
                    "document_chunk_limit",
                    f"Document exceeds the {MAX_CHUNKS}-chunk indexing limit.",
                )
    return chunks


def process_document(
    document_id: str,
    settings: Settings,
    dbfactory: sessionmaker[Session] | Callable[[], Session],
    lease_guard: Callable[[Session], None] | None = None,
) -> dict[str, object]:
    """Extract, chunk, and index one uploaded document under a worker lease."""
    with dbfactory() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise DocumentIngestionError(
                "document_not_found", "Document was not found."
            )
        source = session.get(Source, document.source_id)
        if source is None or source.storage_key is None:
            raise DocumentIngestionError(
                "source_unavailable", "Document source bytes are unavailable."
            )
        if document.state == "ready" and document.stage in {
            "indexed",
            "index_degraded",
        }:
            return {
                "document_id": document.id,
                "state": document.state,
                "stage": document.stage,
                "idempotent": True,
            }
        _guard(lease_guard, session)
        document.state = "running"
        document.stage = "extracting"
        document.progress = 10
        session.commit()

        storage: Storage
        from app.storage.factory import get_storage

        storage = get_storage(settings)
        content = storage.read(source.storage_key, settings.max_upload_bytes)
        try:
            blocks, warnings, language_tags = extract_document(
                source.display_name, content
            )
        except DocumentIngestionError as error:
            return _fail_document(session, document, source, error, lease_guard)
        if not blocks:
            _guard(lease_guard, session)
            document.state = "ocr_needed"
            document.stage = "ocr_needed"
            document.progress = 100
            document.details = {
                "warnings": list(
                    dict.fromkeys(
                        warnings + ["No extractable text was found; OCR is required."]
                    )
                ),
                "languages": language_tags,
            }
            source.state = "ready"
            session.commit()
            return {
                "document_id": document.id,
                "state": document.state,
                "stage": document.stage,
            }

        if len(blocks) > MAX_BLOCKS:
            return _fail_document(
                session,
                document,
                source,
                DocumentIngestionError(
                    "document_block_limit",
                    f"Document exceeds the {MAX_BLOCKS}-block processing limit.",
                ),
                lease_guard,
            )
        profile = _tokenizer_profile(settings)
        try:
            chunks = _make_chunks(blocks, profile)
        except DocumentIngestionError as error:
            return _fail_document(session, document, source, error, lease_guard)

        _guard(lease_guard, session)
        document.stage = "chunking"
        document.progress = 65
        session.commit()

        _guard(lease_guard, session)
        # Reprocessing after a worker crash is safe: the replace and index publish
        # share one transaction, and prior extraction rows are removed first.
        session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == document.id,
                DocumentChunk.chunker_version == document.chunker_version,
            )
        )
        session.execute(
            delete(DocumentBlock).where(DocumentBlock.document_id == document.id)
        )
        persisted_blocks: list[DocumentBlock] = []
        for ordinal, block in enumerate(blocks):
            block_row = DocumentBlock(
                document_id=document.id,
                ordinal=ordinal,
                kind=block.kind,
                text=block.text,
                heading=block.heading,
                location=block.location,
                language=block.language,
                scripts=block.scripts,
            )
            session.add(block_row)
            persisted_blocks.append(block_row)
        session.flush()
        chunk_rows: list[DocumentChunk] = []
        for ordinal, chunk in enumerate(chunks):
            block_ids = [persisted_blocks[int(value)].id for value in chunk.block_ids]
            chunk_row = DocumentChunk(
                document_id=document.id,
                ordinal=ordinal,
                chunker_version=document.chunker_version or CHUNKER_VERSION,
                text=chunk.text,
                normalized_text=chunk.normalized_text,
                heading=chunk.heading,
                location=chunk.location,
                language=chunk.language,
                block_ids=block_ids,
                token_count=chunk.token_count,
            )
            session.add(chunk_row)
            chunk_rows.append(chunk_row)
        session.flush()
        section_parent_ids: dict[str | None, str] = {}
        for index, chunk_row in enumerate(chunk_rows):
            chunk_row.previous_id = chunk_rows[index - 1].id if index else None
            chunk_row.next_id = (
                chunk_rows[index + 1].id if index + 1 < len(chunk_rows) else None
            )
            section = chunk_row.heading
            parent_id = section_parent_ids.setdefault(section, chunk_row.id)
            chunk_row.parent_id = None if parent_id == chunk_row.id else parent_id
        document.stage = "indexing"
        document.progress = 85
        document.details = {
            "extractor_version": document.extractor_version,
            "chunker_version": document.chunker_version,
            "languages": language_tags,
            "warnings": list(dict.fromkeys(warnings)),
            "block_count": len(blocks),
            "chunk_count": len(chunks),
            "tokenization_method": profile.method,
            "tokenizer_sha256": profile.sha256,
            "chunk_body_token_limit": (
                CHUNK_BODY_TOKENS
                if profile.tokenizer is not None
                else FALLBACK_BODY_BYTES
            ),
            "chunk_overlap_token_limit": (
                CHUNK_OVERLAP_TOKENS
                if profile.tokenizer is not None
                else FALLBACK_OVERLAP_BYTES
            ),
        }
        source.state = "ready"
        session.flush()
        # Publish extraction and chunks in a short, lease-guarded transaction.
        # Embedding inference can take a while; do it in the next transaction so
        # the worker can keep heartbeating while no lease row is locked.
        session.commit()
        from app.retrieval.service import build_index_generation

        generation = build_index_generation(
            session, document.id, settings, lease_guard=lease_guard
        )
        _guard(lease_guard, session)
        status = getattr(generation, "status", "ready")
        document.state = "ready"
        document.progress = 100
        if status == "degraded":
            document.stage = "index_degraded"
            document.details = {**document.details, "index_status": "unavailable"}
        else:
            document.stage = "indexed"
            document.details = {**document.details, "index_status": status}
        session.commit()
        return {
            "document_id": document.id,
            "state": document.state,
            "stage": document.stage,
            "block_count": len(blocks),
            "chunk_count": len(chunks),
            "index_generation_id": document.index_generation_id,
        }


def _guard(guard: Callable[[Session], None] | None, session: Session) -> None:
    if guard is not None:
        guard(session)


def _fail_document(
    session: Session,
    document: Document,
    source: Source,
    error: DocumentIngestionError,
    lease_guard: Callable[[Session], None] | None,
) -> dict[str, object]:
    _guard(lease_guard, session)
    document.state = "failed"
    document.stage = "failed"
    document.progress = 100
    document.details = {
        **(document.details or {}),
        "error": {"code": error.code, "message": str(error)},
    }
    source.state = "failed"
    session.commit()
    return {
        "document_id": document.id,
        "state": document.state,
        "stage": document.stage,
        "error": error.code,
    }


def list_documents(
    session: Session, workspace_id: str
) -> list[tuple[Document, Source]]:
    statement = (
        select(Document, Source)
        .join(Source, Source.id == Document.source_id)
        .where(Source.workspace_id == workspace_id)
        .order_by(Document.created_at.desc(), Document.id)
    )
    return [(row[0], row[1]) for row in session.execute(statement).all()]


def get_document_blocks(
    session: Session, document_id: str, *, offset: int = 0, limit: int = 100
) -> tuple[Document | None, list[DocumentBlock], int]:
    if offset < 0 or offset > 100_000:
        raise ValueError("offset must be between 0 and 100000")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    document = session.get(Document, document_id)
    if document is None:
        return None, [], 0
    total = session.scalar(
        select(func.count())
        .select_from(DocumentBlock)
        .where(DocumentBlock.document_id == document_id)
    )
    statement = (
        select(DocumentBlock)
        .where(DocumentBlock.document_id == document_id)
        .order_by(DocumentBlock.ordinal)
        .offset(offset)
        .limit(limit)
    )
    return document, list(session.scalars(statement)), int(total or 0)

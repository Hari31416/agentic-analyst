"""Deterministic evidence-backed Markdown, PDF, and replay notebook reports."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import html
import io
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import Contract, ToolResult
from app.db.models import Artifact, Evidence, Run, Source, Thread, ToolCall
from app.storage.factory import get_storage

if TYPE_CHECKING:
    from app.agent.runtime import RunRuntime


class GenerateReportInput(Contract):
    title: str = Field(min_length=1, max_length=200)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=20)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=40)
    code_artifact_ids: list[UUID] = Field(default_factory=list, max_length=10)
    input_artifact_ids: list[UUID] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=40)
    limitations: list[str] = Field(default_factory=list, max_length=40)


@dataclass(frozen=True)
class _InputSnapshot:
    artifact: Artifact
    content: bytes
    guest_path: str | None = None


def _plain(value: str, limit: int = 4000) -> str:
    value = value.strip()
    if len(value) > limit:
        value = value[:limit] + " [truncated]"
    return value.replace("\x00", "")


def _md_text(value: str) -> str:
    # Keep untrusted evidence as plain text, never as Markdown HTML or a link.
    escaped = html.escape(value, quote=False)
    return (
        escaped.replace("`", "ˋ")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("|", "&#124;")
        .replace("\n", " ")
    )


def _pdf_markup(value: str, *, indic_font: bool) -> str:
    """Escape untrusted text and wrap Devanagari runs in an embedded font."""
    output: list[str] = []
    current_indic: bool | None = None
    chunk: list[str] = []

    def flush() -> None:
        if not chunk:
            return
        safe = html.escape("".join(chunk), quote=False)
        output.append(
            f'<font name="ReportIndic">{safe}</font>'
            if indic_font and current_indic
            else safe
        )
        chunk.clear()

    for character in value:
        codepoint = ord(character)
        is_indic = 0x0900 <= codepoint <= 0x097F or 0xA8E0 <= codepoint <= 0xA8FF
        if current_indic is not None and is_indic != current_indic:
            flush()
        current_indic = is_indic
        chunk.append(character)
    flush()
    return "".join(output)


def _rows(
    content: bytes, name: str, cap: int = 100
) -> tuple[list[str], list[list[str]], int | None]:
    if name.lower().endswith(".csv"):
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig", errors="replace")))
        header = next(reader, [])
        rows: list[list[str]] = []
        total = 0
        for row in reader:
            total += 1
            if len(rows) < cap:
                rows.append((row + [""] * len(header))[: len(header)])
        return header, rows, total
    if name.lower().endswith(".json"):
        value = json.loads(content)
        if (
            isinstance(value, dict)
            and isinstance(value.get("columns"), list)
            and isinstance(value.get("rows"), list)
        ):
            columns = [str(item) for item in value["columns"]]
            raw = value["rows"]
            rows = [
                (
                    [str(row.get(col, "")) for col in columns]
                    if isinstance(row, dict)
                    else [str(item) for item in row]
                )
                for row in raw[:cap]
            ]
            return columns, rows, len(raw)
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, dict) for item in value)
        ):
            columns = list(dict.fromkeys(str(key) for item in value for key in item))
            rows = [[str(item.get(col, "")) for col in columns] for item in value[:cap]]
            return columns, rows, len(value)
    return [], [], None


def _pdf_bytes(
    title: str,
    tables: list[tuple[Artifact, list[str], list[list[str]], int | None]],
    evidence: list[tuple[Evidence, str | None]],
    assumptions: list[str],
    limitations: list[str],
) -> bytes:
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.lib.enums import TA_LEFT  # type: ignore[import-untyped]
    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
    from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
    from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
    from reportlab.platypus import (  # type: ignore[import-untyped]
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    latin_font = "Helvetica"
    for path in (
        "/Users/hari/Library/Fonts/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ):
        try:
            pdfmetrics.registerFont(TTFont("ReportLatin", path, shapable=True))
            latin_font = "ReportLatin"
            break
        except Exception:
            continue
    indic_font = False
    for path in (
        "/Users/hari/Library/Fonts/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    ):
        try:
            pdfmetrics.registerFont(TTFont("ReportIndic", path, shapable=True))
            indic_font = True
            break
        except Exception:
            continue
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=latin_font,
        fontSize=20,
        leading=26,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#142A43"),
        spaceAfter=14,
    )
    heading_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName=latin_font,
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#176B67"),
        spaceBefore=16,
        spaceAfter=7,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=latin_font,
        fontSize=9,
        leading=14,
        spaceAfter=6,
        wordWrap="CJK",
    )
    small_style = ParagraphStyle(
        "ReportSmall",
        parent=body_style,
        fontSize=7,
        leading=10,
        textColor=colors.HexColor("#536273"),
    )
    story: list[Any] = [
        Paragraph(_pdf_markup(title, indic_font=indic_font), title_style)
    ]
    story.append(
        Paragraph(
            "Generated from retained calculation artifacts and saved evidence. Numeric cells below are copied from those artifacts.",
            body_style,
        )
    )
    story.append(Paragraph("Calculated results", heading_style))
    for artifact, columns, rows, total in tables:
        story.append(
            Paragraph(
                _pdf_markup(artifact.display_name, indic_font=indic_font), body_style
            )
        )
        story.append(
            Paragraph(
                _pdf_markup(
                    f"Artifact {artifact.id}; SHA256 {artifact.sha256}",
                    indic_font=indic_font,
                ),
                small_style,
            )
        )
        data = [
            [
                Paragraph(_pdf_markup(value, indic_font=indic_font), small_style)
                for value in columns
            ]
        ]
        data.extend(
            [
                [
                    Paragraph(_pdf_markup(value, indic_font=indic_font), small_style)
                    for value in row
                ]
                for row in rows
            ]
        )
        if data:
            table = Table(data, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F0F3")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#142A43")),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5DE")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.extend([table, Spacer(1, 7)])
        if total is not None and total > len(rows):
            story.append(
                Paragraph(
                    f"Showing {len(rows)} of {total} rows. Download the source artifact for the complete result.",
                    small_style,
                )
            )
    for heading, values in (("Assumptions", assumptions), ("Limitations", limitations)):
        if values:
            story.append(Paragraph(heading, heading_style))
            story.extend(
                Paragraph("- " + _pdf_markup(value, indic_font=indic_font), body_style)
                for value in values
            )
    story.append(Paragraph("Evidence and source locations", heading_style))
    for item, display_name in evidence:
        details = item.details
        location = details.get("location", {})
        excerpt = _plain(
            str(
                details.get(
                    "excerpt", details.get("query", "Saved calculation evidence")
                )
            )
        )
        source_name = (
            display_name or ", ".join(item.source_ids) or "Calculation artifact"
        )
        anchor = (
            json.dumps(location, ensure_ascii=False, sort_keys=True) if location else ""
        )
        versions = json.dumps(
            details.get("source_versions", {}), ensure_ascii=False, sort_keys=True
        )
        story.append(
            Paragraph(
                f"[{item.id}] {_pdf_markup(source_name, indic_font=indic_font)} {_pdf_markup(anchor, indic_font=indic_font)} versions {_pdf_markup(versions, indic_font=indic_font)}",
                small_style,
            )
        )
        story.append(Paragraph(_pdf_markup(excerpt, indic_font=indic_font), body_style))

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=42,
        leftMargin=42,
        topMargin=48,
        bottomMargin=44,
        title=title[:180],
        author="Agentic RAG Analyst",
    )

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(latin_font, 8)
        canvas.setFillColor(colors.HexColor("#536273"))
        canvas.drawString(42, 25, "Evidence-backed analysis")
        canvas.drawRightString(A4[0] - 42, 25, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _notebook_bytes(
    title: str,
    code: list[tuple[Artifact, bytes]],
    snapshots: list[_InputSnapshot],
    result_tables: list[tuple[Artifact, list[str], list[list[str]], int | None]],
    evidence: list[tuple[Evidence, str | None]],
    assumptions: list[str],
    limitations: list[str],
) -> bytes:
    cells: list[dict[str, Any]] = []

    def markdown(text: str) -> None:
        cells.append(
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": text.splitlines(keepends=True),
            }
        )

    markdown(
        f"# {_md_text(title)}\n\nThis notebook includes code and input snapshot metadata. Code has not been rerun during report creation. Review the exact code cell before executing it.\n"
    )
    snapshot_total = sum(len(item.content) for item in snapshots)
    embedded = snapshot_total <= 1_000_000
    metadata_snapshots = []
    for item in snapshots:
        descriptor: dict[str, Any] = {
            "artifact_id": item.artifact.id,
            "filename": item.artifact.display_name,
            "media_type": item.artifact.media_type,
            "sha256": item.artifact.sha256,
            "byte_size": item.artifact.byte_size,
            "lineage": list(item.artifact.lineage or []),
            "sandbox_guest_path": item.guest_path,
            "embedded_base64": (
                base64.b64encode(item.content).decode("ascii") if embedded else None
            ),
        }
        metadata_snapshots.append(descriptor)
    markdown(
        "## Inputs and replay\n\n"
        + (
            "Snapshots are embedded in notebook metadata as base64. Decode each `embedded_base64` value to its `filename` before running the cells. The saved code may refer to `/workspace/inputs/`; local replay requires mapping those paths to the extracted files.\n"
            if embedded and snapshots
            else ""
        )
    )
    if snapshots and not embedded:
        markdown(
            "Snapshot files exceed the notebook's 1 MB embedding bound. Download each exact listed artifact to a local `inputs/` directory; verify its SHA256 before replay. The saved code may refer to `/workspace/inputs/`; local replay requires mapping those paths to the extracted files. The required files remain retained as separate artifacts.\n"
        )
    for item in snapshots:
        markdown(
            f"- `{_md_text(item.artifact.display_name)}`; SHA256 `{item.artifact.sha256}`; {item.artifact.byte_size} bytes; artifact `{item.artifact.id}`.\n"
        )
    for artifact, source in code:
        text = source.decode("utf-8", errors="replace")
        markdown(
            f"## Exact code: {_md_text(artifact.display_name)}\n\nSaved SHA256 `{artifact.sha256}`. This cell is the exact retained sandbox source. It has not been changed or executed by report generation.\n"
        )
        cells.append(
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {"artifact_id": artifact.id, "sha256": artifact.sha256},
                "outputs": [],
                "source": text.splitlines(keepends=True),
            }
        )
    if result_tables:
        markdown(
            "## Saved outputs\n\nThese tables are captured outputs. Re-running the code is not required to inspect the saved results.\n"
        )
        for artifact, columns, rows, total in result_tables:
            head = (
                "| "
                + " | ".join(_md_text(item) for item in columns)
                + " |\n| "
                + " | ".join("---" for _ in columns)
                + " |\n"
            )
            body = "".join(
                "| " + " | ".join(_md_text(item) for item in row) + " |\n"
                for row in rows
            )
            markdown(
                f"### {_md_text(artifact.display_name)}\n\n{head}{body}\nSaved artifact `{artifact.id}` with SHA256 `{artifact.sha256}`.\n"
            )
            if total is not None and total > len(rows):
                markdown(
                    f"Preview contains {len(rows)} of {total} rows; download the artifact for all rows.\n"
                )
    if assumptions:
        markdown(
            "## Assumptions\n\n"
            + "\n".join(f"- {_md_text(value)}" for value in assumptions)
            + "\n"
        )
    if limitations:
        markdown(
            "## Limitations\n\n"
            + "\n".join(f"- {_md_text(value)}" for value in limitations)
            + "\n"
        )
    markdown(
        "## Evidence\n\n"
        + "\n".join(
            f"- `{item.id}` {_md_text(display_name or ', '.join(item.source_ids))}; location `{_md_text(json.dumps(item.details.get('location', {}), ensure_ascii=False, sort_keys=True))}`.\n"
            for item, display_name in evidence
        )
    )
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3"},
            "agentic_rag": {
                "format_version": 1,
                "replay_note": "Live database inputs require reconnection or an exact retained snapshot. Run in an isolated local environment after reviewing code.",
                "code_sha256": {artifact.id: artifact.sha256 for artifact, _ in code},
                "input_snapshots": metadata_snapshots,
                "evidence_ids": [item.id for item, _ in evidence],
                "generated_at_version": "phase06-report-v1",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, ensure_ascii=False, indent=2).encode("utf-8")


class ReportsTool:
    def __init__(self, runtime: RunRuntime):
        self.runtime = runtime

    async def execute(
        self, name: str, args: GenerateReportInput, tool_id: str
    ) -> ToolResult:
        if name != "generate_report":
            raise ValueError("unsupported report tool")
        if not args.artifact_ids and not args.evidence_ids:
            raise ValueError("reports require calculated artifacts or source evidence")
        return await asyncio.to_thread(self._generate, args, tool_id)

    def _generate(self, args: GenerateReportInput, tool_id: str) -> ToolResult:
        runtime = self.runtime
        with runtime.db() as session:
            run = runtime.guard(session)
            artifacts = runtime.accessible_artifacts(session, run)
            allowed = {item.id: item for item in artifacts}
            requested_ids = {
                str(item)
                for item in args.artifact_ids
                + args.code_artifact_ids
                + args.input_artifact_ids
            }
            if requested_ids - set(allowed):
                raise ValueError("report inputs must be accessible retained artifacts")
            report_artifacts = [allowed[str(item)] for item in args.artifact_ids]
            code_artifacts = [allowed[str(item)] for item in args.code_artifact_ids]
            input_artifacts = [allowed[str(item)] for item in args.input_artifact_ids]
            staged_guest_paths: dict[str, str] = {}
            producer_tool_ids = {
                item.tool_call_id for item in report_artifacts if item.tool_call_id
            }
            for call in session.scalars(
                select(ToolCall).where(ToolCall.id.in_(producer_tool_ids))
            ):
                call_data = (call.result or {}).get("data", {})
                code_id = call_data.get("code_artifact_id")
                if code_id in allowed:
                    code_artifacts.append(allowed[code_id])
                for input_id in call_data.get("input_artifact_ids", []):
                    if input_id in allowed:
                        input_artifacts.append(allowed[input_id])
                        staged_guest_paths[input_id] = f"/workspace/inputs/{input_id}"
                for staged in call_data.get("staged_input_artifacts", []):
                    staged_id = staged.get("id")
                    if staged_id in allowed:
                        input_artifacts.append(allowed[staged_id])
                        staged_guest_paths[staged_id] = str(
                            staged.get("guest_path", "")
                        )
            code_artifacts = list({item.id: item for item in code_artifacts}.values())
            input_artifacts = list({item.id: item for item in input_artifacts}.values())
            selected = set(run.selected_source_ids)
            versions = run.config.get("source_versions", {})
            evidence_items: list[Evidence] = []
            for evidence_id in args.evidence_ids:
                evidence = session.get(Evidence, str(evidence_id))
                producer = session.get(Run, evidence.run_id) if evidence else None
                if (
                    evidence is None
                    or producer is None
                    or producer.thread_id != run.thread_id
                    or evidence.run_id != run.id
                    and not set(producer.selected_source_ids).issubset(selected)
                    or not set(evidence.source_ids).issubset(selected)
                    or any(
                        versions.get(source_id) != version
                        for source_id, version in evidence.details.get(
                            "source_versions", {}
                        ).items()
                    )
                ):
                    raise ValueError(
                        "report evidence is unavailable for the selected source versions"
                    )
                evidence_items.append(evidence)
            source_names = {
                row.id: row.display_name
                for row in session.scalars(
                    select(Source).where(Source.id.in_(selected))
                )
            }
            snapshots = []
            all_inputs = {
                row.id: row
                for row in [*report_artifacts, *code_artifacts, *input_artifacts]
            }
            storage = get_storage(runtime.settings)
            for artifact in all_inputs.values():
                content = storage.read(
                    artifact.storage_key, runtime.settings.max_upload_bytes
                )
                if hashlib.sha256(content).hexdigest() != artifact.sha256:
                    raise ValueError("report input artifact integrity check failed")
                if artifact in input_artifacts:
                    snapshots.append(
                        _InputSnapshot(
                            artifact, content, staged_guest_paths.get(artifact.id)
                        )
                    )
            tables = []
            for artifact in report_artifacts:
                content = storage.read(
                    artifact.storage_key, runtime.settings.max_upload_bytes
                )
                columns, rows, total = _rows(content, artifact.display_name)
                if columns:
                    tables.append((artifact, columns, rows, total))
            codes = []
            for artifact in code_artifacts:
                content = storage.read(
                    artifact.storage_key, runtime.settings.max_upload_bytes
                )
                if artifact.media_type.startswith(
                    "text/"
                ) or artifact.display_name.endswith(".py"):
                    if re.search(
                        rb"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*['\"]?[^\s'\"]{8,}",
                        content,
                    ):
                        raise ValueError(
                            "code artifact appears to contain a secret; remove it before report export"
                        )
                    codes.append((artifact, content))
            evidence_refs = [
                (
                    item,
                    next(
                        (
                            source_names.get(source_id)
                            for source_id in item.source_ids
                            if source_names.get(source_id)
                        ),
                        None,
                    ),
                )
                for item in evidence_items
            ]
            title = _plain(args.title, 200)
            assumptions = [_plain(value, 1000) for value in args.assumptions]
            limitations = [_plain(value, 1000) for value in args.limitations]
            markdown = self._markdown(
                title, tables, evidence_refs, assumptions, limitations
            )
            pdf = _pdf_bytes(title, tables, evidence_refs, assumptions, limitations)
            notebook = _notebook_bytes(
                title, codes, snapshots, tables, evidence_refs, assumptions, limitations
            )
            derived_lineage = [
                *[
                    f"artifact:{item.id}@sha256:{item.sha256}"
                    for item in all_inputs.values()
                ],
                *[f"evidence:{item.id}" for item, _ in evidence_refs],
            ]
            return self._persist(
                run, tool_id, title, markdown, pdf, notebook, derived_lineage
            )

    @staticmethod
    def _markdown(
        title: str,
        tables: list[Any],
        evidence: list[Any],
        assumptions: list[str],
        limitations: list[str],
    ) -> bytes:
        lines = [
            f"# {_md_text(title)}",
            "",
            "Generated from retained calculation artifacts and saved evidence. Numeric cells below are copied from those artifacts.",
            "",
            "## Calculated results",
            "",
        ]
        for artifact, columns, rows, total in tables:
            lines.extend(
                [
                    f"### {_md_text(artifact.display_name)}",
                    "",
                    f"Artifact `{artifact.id}`; SHA256 `{artifact.sha256}`.",
                    "",
                    "| " + " | ".join(_md_text(item) for item in columns) + " |",
                    "| " + " | ".join("---" for _ in columns) + " |",
                ]
            )
            lines.extend(
                "| " + " | ".join(_md_text(item) for item in row) + " |" for row in rows
            )
            if total is not None and total > len(rows):
                lines.append(
                    f"\nShowing {len(rows)} of {total} rows. Download the source artifact for the complete result."
                )
            lines.append("")
        for heading, values in (
            ("Assumptions", assumptions),
            ("Limitations", limitations),
        ):
            if values:
                lines.extend(
                    [
                        f"## {heading}",
                        "",
                        *[f"- {_md_text(value)}" for value in values],
                        "",
                    ]
                )
        lines.extend(["## Evidence and source locations", ""])
        for item, display_name in evidence:
            location = json.dumps(
                item.details.get("location", {}), ensure_ascii=False, sort_keys=True
            )
            versions = json.dumps(
                item.details.get("source_versions", {}),
                ensure_ascii=False,
                sort_keys=True,
            )
            excerpt = _plain(
                str(
                    item.details.get(
                        "excerpt",
                        item.details.get("query", "Saved calculation evidence"),
                    )
                )
            )
            lines.extend(
                [
                    f"### [{item.id}] {_md_text(display_name or ', '.join(item.source_ids))}",
                    "",
                    f"Location: `{_md_text(location)}`; source versions `{_md_text(versions)}`",
                    "",
                    _md_text(excerpt),
                    "",
                ]
            )
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _persist(
        self,
        run: Run,
        tool_id: str,
        title: str,
        markdown: bytes,
        pdf: bytes,
        notebook: bytes,
        lineage: list[str],
    ) -> ToolResult:
        storage = get_storage(self.runtime.settings)
        stem = (
            re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-")[:60] or "analysis-report"
        )
        outputs = [
            (f"{stem}.md", "text/markdown", markdown),
            (f"{stem}.pdf", "application/pdf", pdf),
            (f"{stem}.ipynb", "application/x-ipynb+json", notebook),
        ]
        descriptors = []
        ids = []
        for filename, media_type, content in outputs:
            stored = storage.put(
                f"derived/{run.thread_id}/{run.id}/{tool_id}/{filename}", content
            )
            artifact_id = str(uuid4())
            ids.append(UUID(artifact_id))
            descriptors.append(
                {
                    "id": artifact_id,
                    "display_name": filename,
                    "storage_key": stored.key,
                    "media_type": media_type,
                    "byte_size": stored.byte_size,
                    "sha256": stored.sha256,
                    "lineage": lineage,
                }
            )
        return ToolResult(
            status="ok",
            summary="Created Markdown, PDF, and replay notebook from saved artifacts and evidence.",
            artifact_ids=ids,
            data={
                "artifacts": descriptors,
                "artifact_lineage": {item["id"]: lineage for item in descriptors},
            },
        )

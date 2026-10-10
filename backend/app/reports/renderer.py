"""ReportLab PDF renderer for trusted, hash-checked artifact references."""

from __future__ import annotations

import hashlib
import html
import io
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_CENTER  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import (  # type: ignore[import-untyped]
    ShapedFragWord,
    TTFont,
    makeShapedFragWord,
    shapeFragWord,
)
from reportlab.platypus import (  # type: ignore[import-untyped]
    Image,
    PageTemplate,
    Paragraph as _BaseParagraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Frame,
    Flowable,
)
from reportlab.platypus.paragraph import _getFragWords  # type: ignore[import-untyped]
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.graphics.shapes import (  # type: ignore[import-untyped]
    Circle,
    Drawing,
    Line,
    Rect,
    String,
    Wedge,
)

from app.artifacts.tabular import decode_table
from app.artifacts.chart import ChartSpec
from app.reports.schemas import FigureBlock, ParagraphBlock, ReportDocument, TableBlock


class ReportParagraph(_BaseParagraph):  # type: ignore[misc]
    """Paragraph adapter that shapes adjacent font runs independently."""

    def breakLines(self, width: Any) -> Any:
        frags = cast(list[Any], getattr(self, "frags", []))
        if self.style.shaping and frags:
            processed: list[Any] = []
            for word in _getFragWords(frags, width):
                if isinstance(word, ShapedFragWord) or not word:
                    processed.append(word)
                    continue
                pairs = word[1:]
                if not pairs or not all(
                    isinstance(pair, tuple)
                    and len(pair) == 2
                    and hasattr(pair[0], "fontName")
                    and isinstance(pair[1], str)
                    for pair in pairs
                ):
                    processed.append(word)
                    continue
                fonts = {(pair[0].fontName, pair[0].fontSize) for pair in pairs}
                if len(fonts) < 2:
                    processed.append(word)
                    continue

                runs: list[list[tuple[Any, str]]] = []
                for pair in pairs:
                    if (
                        not runs
                        or runs[-1][-1][0].fontName != pair[0].fontName
                        or runs[-1][-1][0].fontSize != pair[0].fontSize
                    ):
                        runs.append([])
                    runs[-1].append(pair)
                shaped_pairs: list[tuple[Any, str]] = []
                shaped_width = 0.0
                for run in runs:
                    run_width = sum(
                        stringWidth(text, frag.fontName, frag.fontSize)
                        for frag, text in run
                    )
                    shaped_run = shapeFragWord([run_width, *run])
                    shaped_width += float(shaped_run[0])
                    shaped_pairs.extend(shaped_run[1:])
                processed.append(
                    makeShapedFragWord(word)([shaped_width, *shaped_pairs])
                )
            self.frags = processed
        return super().breakLines(width)


# Keep the normal name available inside this module while exposing a descriptive
# alias for the existing report tool to import.
Paragraph = ReportParagraph

MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_TOTAL_ASSET_BYTES = 50 * 1024 * 1024
MAX_TABLE_SOURCE_ROWS = 10_001
_FONT_DIR = Path(__file__).with_name("fonts")
_FONT_PATHS = {
    "latin": ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"),
    "devanagari": (
        "NotoSansDevanagari-Regular.ttf",
        "NotoSansDevanagari-Bold.ttf",
    ),
}
_FONT_NAMES = {
    "latin": ("ReportNoto", "ReportNoto-Bold"),
    "devanagari": ("ReportNotoDeva", "ReportNotoDeva-Bold"),
}
_FONTS_READY = False


def capabilities() -> list[dict[str, str]]:
    """Return report languages backed by bundled shaping-capable fonts."""
    return [
        {"language": "en-IN", "label": "English (India)"},
        {"language": "hi-IN", "label": "Hindi (India)"},
    ]


def _register_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    for group, files in _FONT_PATHS.items():
        for filename, name in zip(files, _FONT_NAMES[group], strict=True):
            path = _FONT_DIR / filename
            pdfmetrics.registerFont(TTFont(name, str(path), shapable=True))
    pdfmetrics.registerFontFamily(
        "ReportNoto", normal="ReportNoto", bold="ReportNoto-Bold"
    )
    pdfmetrics.registerFontFamily(
        "ReportNotoDeva",
        normal="ReportNotoDeva",
        bold="ReportNotoDeva-Bold",
    )
    _FONTS_READY = True


def _has_glyph(font_name: str, char: str) -> bool:
    face = pdfmetrics.getFont(font_name).face
    return ord(char) in face.charWidths or char in "\n\r\t\u200c\u200d"


def _font_for_char(char: str, *, bold: bool = False) -> str:
    _register_fonts()
    latin = "ReportNoto-Bold" if bold else "ReportNoto"
    devanagari = "ReportNotoDeva-Bold" if bold else "ReportNotoDeva"
    codepoint = ord(char)
    if char in "\u200c\u200d":
        return devanagari
    preferred = devanagari if 0x0900 <= codepoint <= 0x097F else latin
    alternate = latin if preferred == devanagari else devanagari
    if _has_glyph(preferred, char):
        return preferred
    if _has_glyph(alternate, char):
        return alternate
    raise ValueError(f"unsupported report glyph U+{codepoint:04X}")


def _markup(value: str, *, bold: bool = False) -> str:
    """Escape plain text and add font runs so Indic shaping is preserved."""
    chunks: list[str] = []
    active_font: str | None = None
    active_text: list[str] = []

    def flush() -> None:
        nonlocal active_text
        if active_font is None or not active_text:
            return
        escaped = html.escape("".join(active_text), quote=False).replace("\n", "<br/>")
        chunks.append(f'<font name="{active_font}">{escaped}</font>')
        active_text = []

    for char in value:
        if char in "\r\t":
            char = " "
        if char in "\u200c\u200d" and active_font and "Deva" in active_font:
            font = active_font
        elif (
            (char.isspace() or unicodedata.category(char).startswith("P"))
            and active_font
            and "Deva" in active_font
            and _has_glyph(active_font, char)
        ):
            font = active_font
        else:
            font = _font_for_char(char, bold=bold)
        if active_font is not None and font != active_font:
            flush()
        active_font = font
        active_text.append(char)
    flush()
    return "".join(chunks)


def _styles() -> dict[str, ParagraphStyle]:
    _register_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="ReportNoto-Bold",
            shaping=1,
            fontSize=20,
            leading=26,
            textColor=colors.HexColor("#17324D"),
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading1"],
            fontName="ReportNoto-Bold",
            shaping=1,
            fontSize=14,
            leading=19,
            textColor=colors.HexColor("#174A6E"),
            spaceBefore=5 * mm,
            spaceAfter=3 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName="ReportNoto",
            shaping=1,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#263746"),
            spaceAfter=3 * mm,
            splitLongWords=True,
        ),
        "caption": ParagraphStyle(
            "ReportCaption",
            parent=base["BodyText"],
            fontName="ReportNoto",
            shaping=1,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#526575"),
            spaceBefore=1.5 * mm,
            spaceAfter=4 * mm,
        ),
        "table": ParagraphStyle(
            "ReportTableCell",
            parent=base["BodyText"],
            fontName="ReportNoto",
            shaping=1,
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#263746"),
            splitLongWords=True,
        ),
        "table_header": ParagraphStyle(
            "ReportTableHeader",
            parent=base["BodyText"],
            fontName="ReportNoto-Bold",
            shaping=1,
            fontSize=7.5,
            leading=10,
            textColor=colors.white,
            splitLongWords=True,
        ),
        "footnote": ParagraphStyle(
            "ReportFootnote",
            parent=base["BodyText"],
            fontName="ReportNoto",
            shaping=1,
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#657887"),
            spaceBefore=1.5 * mm,
            spaceAfter=3 * mm,
        ),
        "chart_label": ParagraphStyle(
            "ReportChartLabel",
            parent=base["BodyText"],
            fontName="ReportNoto",
            shaping=1,
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#526575"),
        ),
        "chart_title": ParagraphStyle(
            "ReportChartTitle",
            parent=base["BodyText"],
            fontName="ReportNoto-Bold",
            shaping=1,
            fontSize=10,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17324D"),
        ),
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _metadata_bytes(
    artifact_id: str,
    assets: dict[str, dict[str, Any]],
    read_asset: Callable[[dict[str, Any]], bytes],
) -> tuple[dict[str, Any], bytes]:
    if artifact_id not in assets:
        raise ValueError(f"report references undeclared artifact: {artifact_id}")
    metadata = assets[artifact_id]
    size = metadata.get("byte_size")
    digest = metadata.get("sha256")
    if type(size) is not int or size < 0 or size > MAX_ASSET_BYTES:
        raise ValueError("artifact size metadata exceeds safe bounds")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise ValueError("artifact SHA-256 metadata is invalid")
    content = read_asset(metadata)
    if not isinstance(content, bytes):
        raise ValueError("artifact reader must return bytes")
    if (
        len(content) != size
        or hashlib.sha256(content).hexdigest().lower() != digest.lower()
    ):
        raise ValueError("artifact integrity check failed")
    return metadata, content


def _figure(
    block: FigureBlock,
    metadata: dict[str, Any],
    content: bytes,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    media_type = str(metadata.get("media_type", "")).lower().split(";", 1)[0].strip()
    if media_type == "application/vnd.plotly.v1+json":
        spec = ChartSpec.from_json_bytes(content)
        flowable = _chart_drawing(spec, styles)
        chart_flowables: list[Any] = [flowable]
        if block.caption:
            chart_flowables.append(Paragraph(_markup(block.caption), styles["caption"]))
        return chart_flowables
    allowed = {"image/png", "image/jpeg"}
    if media_type not in allowed:
        raise ValueError("figure artifacts must be PNG or JPEG images")
    signature_ok = (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        if media_type == "image/png"
        else content.startswith(b"\xff\xd8\xff")
    )
    if not signature_ok:
        raise ValueError("figure bytes do not match the declared image type")
    image_reader = ImageReader(io.BytesIO(content))
    width, height = image_reader.getSize()
    if width <= 0 or height <= 0 or width * height > 40_000_000:
        raise ValueError("figure dimensions exceed safe bounds")
    max_width, max_height = 165 * mm, 115 * mm
    scale = min(max_width / width, max_height / height, 1.0)
    flowables: list[Any] = [
        Image(
            io.BytesIO(content),
            width=width * scale,
            height=height * scale,
            hAlign="CENTER",
        )
    ]
    if block.caption:
        flowables.append(Paragraph(_markup(block.caption), styles["caption"]))
    return flowables


class _DrawingFlowable(Flowable):  # type: ignore[misc]
    def __init__(
        self,
        drawing: Drawing,
        styles: dict[str, ParagraphStyle],
        labels: list[tuple[str, float, float, float, str, int]],
    ) -> None:
        super().__init__()
        self.drawing = drawing
        self.styles = styles
        self.labels = labels
        self.width = drawing.width
        self.height = drawing.height

    def draw(self) -> None:
        self.drawing.drawOn(self.canv, 0, 0)
        for text, x, y, width, style_name, rotation in self.labels:
            paragraph = Paragraph(_markup(text), self.styles[style_name])
            _, paragraph_height = paragraph.wrap(width, self.height)
            self.canv.saveState()
            if rotation:
                self.canv.translate(x, y)
                self.canv.rotate(rotation)
                paragraph.drawOn(self.canv, 0, -paragraph_height)
            else:
                paragraph.drawOn(self.canv, x, y - paragraph_height)
            self.canv.restoreState()


def _chart_drawing(spec: ChartSpec, styles: dict[str, ParagraphStyle]) -> Flowable:
    """Render a bounded and faithful subset of the safe chart schema."""
    traces = spec.data
    kinds = {trace.type for trace in traces}
    if len(kinds) != 1:
        raise ValueError("report charts cannot mix chart types in one figure")
    chart_kind = next(iter(kinds))
    if chart_kind == "bar" and spec.layout.barmode not in {None, "group"}:
        raise ValueError("report bar charts support grouped bars only")
    if chart_kind != "bar" and spec.layout.barmode is not None:
        raise ValueError("barmode is supported only for report bar charts")

    width, height = 455.0, 245.0
    drawing = Drawing(width, height)
    palette = [
        colors.HexColor(value)
        for value in ("#176B87", "#3A8D7C", "#E3A735", "#D46A5E", "#6C78A6", "#79A7C2")
    ]
    labels: list[tuple[str, float, float, float, str, int]] = []
    if spec.layout.title:
        labels.append(
            (spec.layout.title, 35, height - 18, width - 70, "chart_title", 0)
        )

    if chart_kind == "pie":
        if len(traces) != 1:
            raise ValueError("report pie charts support exactly one trace")
        trace = traces[0]
        if trace.labels is None or trace.values is None:
            raise ValueError("invalid pie chart structure")
        if len(trace.labels) > 8:
            raise ValueError("report pie charts support at most 8 legend entries")
        values = [float(value) for value in trace.values]
        total = sum(values)
        if total <= 0 or any(value < 0 for value in values):
            raise ValueError(
                "pie chart values must be nonnegative with a positive total"
            )
        angle = 0.0
        for index, value in enumerate(values):
            extent = 360.0 * value / total
            drawing.add(
                Wedge(
                    112,
                    119,
                    78,
                    angle,
                    angle + extent,
                    fillColor=palette[index % len(palette)],
                    strokeColor=colors.white,
                    strokeWidth=0.8,
                )
            )
            angle += extent
        for index, (label, value) in enumerate(zip(trace.labels, values, strict=True)):
            y = 181.0 - index * 19
            drawing.add(
                Rect(
                    230,
                    y - 7,
                    9,
                    9,
                    fillColor=palette[index % len(palette)],
                    strokeColor=None,
                )
            )
            labels.append((f"{label}: {value:g}", 245, y + 2, 195, "chart_label", 0))
    else:
        series_values = [
            [float(value) for value in (trace.y or [])] for trace in traces
        ]
        if not series_values or not series_values[0]:
            raise ValueError("chart has no numeric values")

        if chart_kind == "bar":
            categories: list[str | int | float] = list(
                traces[0].x or range(len(series_values[0]))
            )
            for index, trace in enumerate(traces):
                category_values = list(trace.x or range(len(series_values[index])))
                if category_values != categories or len(series_values[index]) != len(
                    categories
                ):
                    raise ValueError("grouped bar traces must use identical categories")
            x_numeric = False
        else:
            x_coordinates = [
                list(trace.x or range(len(series_values[index])))
                for index, trace in enumerate(traces)
            ]
            if any(values != x_coordinates[0] for values in x_coordinates[1:]):
                raise ValueError("scatter traces must use identical x coordinates")
            categories = list(x_coordinates[0])
            if any(len(series) != len(categories) for series in series_values):
                raise ValueError("scatter x and y values must have equal lengths")
            x_numeric = all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in categories
            )
            if any(isinstance(value, str) for value in categories) and not all(
                isinstance(value, str) for value in categories
            ):
                raise ValueError(
                    "scatter x coordinates must be all numeric or all text"
                )
        if not categories or len(categories) > 16:
            raise ValueError("report charts support 1 to 16 categories or points")

        all_y = [value for series in series_values for value in series]
        min_y, max_y = min(0.0, min(all_y)), max(0.0, max(all_y))
        if min_y == max_y:
            max_y = min_y + 1.0
        y_padding = (max_y - min_y) * 0.08
        min_y -= y_padding
        max_y += y_padding

        chart_left, chart_right = 58.0, 440.0
        chart_bottom, chart_top = 62.0, 195.0
        if chart_kind == "scatter" and x_numeric:
            numeric_x = [float(value) for value in categories]
            min_x, max_x = min(numeric_x), max(numeric_x)
            if min_x == max_x:
                max_x = min_x + 1.0
            x_padding = (max_x - min_x) * 0.04
            min_x -= x_padding
            max_x += x_padding
            x_positions = [
                chart_left
                + (value - min_x) / (max_x - min_x) * (chart_right - chart_left)
                for value in numeric_x
            ]
            x_labels = [f"{value:g}" for value in numeric_x]
        else:
            x_positions = [
                chart_left
                + (index + 0.5) * (chart_right - chart_left) / len(categories)
                for index in range(len(categories))
            ]
            x_labels = [str(value) for value in categories]

        for tick in range(5):
            y = chart_bottom + tick * (chart_top - chart_bottom) / 4
            value = min_y + tick * (max_y - min_y) / 4
            drawing.add(
                Line(
                    chart_left,
                    y,
                    chart_right,
                    y,
                    strokeColor=colors.HexColor("#D7E0E6"),
                    strokeWidth=0.5,
                )
            )
            drawing.add(
                String(
                    chart_left - 6,
                    y - 2,
                    f"{value:.2g}",
                    textAnchor="end",
                    fontName="ReportNoto",
                    fontSize=6,
                    fillColor=colors.HexColor("#657887"),
                )
            )
        drawing.add(
            Line(
                chart_left,
                chart_bottom,
                chart_left,
                chart_top,
                strokeColor=colors.HexColor("#657887"),
                strokeWidth=0.7,
            )
        )
        zero_y = chart_bottom + (0 - min_y) / (max_y - min_y) * (
            chart_top - chart_bottom
        )
        drawing.add(
            Line(
                chart_left,
                zero_y,
                chart_right,
                zero_y,
                strokeColor=colors.HexColor("#657887"),
                strokeWidth=0.8,
            )
        )

        if chart_kind == "bar":
            category_width = (chart_right - chart_left) / len(categories)
            group_width = category_width * 0.76
            bar_width = group_width / len(traces)
            for trace_index, series in enumerate(series_values):
                for index, value in enumerate(series):
                    x = (
                        chart_left
                        + category_width * index
                        + (category_width - group_width) / 2
                        + trace_index * bar_width
                    )
                    y = chart_bottom + (value - min_y) / (max_y - min_y) * (
                        chart_top - chart_bottom
                    )
                    drawing.add(
                        Rect(
                            x,
                            min(zero_y, y),
                            max(1.0, bar_width - 1),
                            abs(y - zero_y),
                            fillColor=palette[trace_index % len(palette)],
                            strokeColor=None,
                        )
                    )
        else:
            for trace_index, (trace, series) in enumerate(
                zip(traces, series_values, strict=True)
            ):
                points = [
                    (
                        x_positions[index],
                        chart_bottom
                        + (value - min_y)
                        / (max_y - min_y)
                        * (chart_top - chart_bottom),
                    )
                    for index, value in enumerate(series)
                ]
                if trace.mode in {None, "lines", "lines+markers"}:
                    for start, end in zip(points, points[1:]):
                        drawing.add(
                            Line(
                                start[0],
                                start[1],
                                end[0],
                                end[1],
                                strokeColor=palette[trace_index % len(palette)],
                                strokeWidth=1.4,
                            )
                        )
                if trace.mode in {None, "markers", "lines+markers"}:
                    for x, y in points:
                        drawing.add(
                            Circle(
                                x,
                                y,
                                2.6,
                                fillColor=palette[trace_index % len(palette)],
                                strokeColor=colors.white,
                                strokeWidth=0.5,
                            )
                        )
        for index, label in enumerate(x_labels):
            labels.append(
                (label, x_positions[index] - 30, chart_bottom - 4, 60, "chart_label", 0)
            )

    if chart_kind != "pie":
        for index, trace in enumerate(traces):
            if trace.name:
                x = 65.0 + index * 125.0
                y = 225.0
                drawing.add(
                    Rect(
                        x,
                        y - 6,
                        8,
                        8,
                        fillColor=palette[index % len(palette)],
                        strokeColor=None,
                    )
                )
                labels.append((trace.name, x + 12, y + 2, 105, "chart_label", 0))
    if spec.layout.xaxis and spec.layout.xaxis.title:
        labels.append((spec.layout.xaxis.title, 160, 18, 180, "chart_label", 0))
    if spec.layout.yaxis and spec.layout.yaxis.title:
        labels.append((spec.layout.yaxis.title, 10, 130, 100, "chart_label", 90))
    return _DrawingFlowable(drawing, styles, labels)


def _table(
    block: TableBlock,
    metadata: dict[str, Any],
    content: bytes,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    display_name = metadata.get("display_name")
    if not isinstance(display_name, str):
        raise ValueError("tabular artifacts require a display name")
    columns, rows = decode_table(content, display_name, max_rows=MAX_TABLE_SOURCE_ROWS)
    if block.columns:
        missing = [column for column in block.columns if column not in columns]
        if missing:
            raise ValueError("requested table columns are missing from the artifact")
        indices = [columns.index(column) for column in block.columns]
        columns = [columns[index] for index in indices]
        rows = [
            [row[index] if index < len(row) else None for index in indices]
            for row in rows
        ]
    if not columns:
        raise ValueError("tabular artifact has no columns")
    if len(columns) > 12:
        raise ValueError("report tables support at most 12 selected columns")
    shown_rows = rows[: block.max_rows]
    data: list[list[Any]] = [
        [
            Paragraph(_markup(column, bold=True), styles["table_header"])
            for column in columns
        ]
    ]
    for row in shown_rows:
        data.append([Paragraph(_markup(_text(cell)), styles["table"]) for cell in row])
    available_width = A4[0] - 36 * mm
    widths = [available_width / len(columns)] * len(columns)
    table = Table(
        data,
        colWidths=widths,
        repeatRows=1,
        splitByRow=1,
        splitInRow=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174A6E")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F2F6F8")],
                ),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C9D4DC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    result: list[Any] = []
    if block.caption:
        result.append(Paragraph(_markup(block.caption), styles["caption"]))
    result.append(table)
    if len(rows) > len(shown_rows):
        count = (
            f"at least {len(rows)}"
            if len(rows) == MAX_TABLE_SOURCE_ROWS
            else str(len(rows))
        )
        result.append(
            Paragraph(
                _markup(
                    f"Showing {len(shown_rows)} of {count} rows; additional rows were omitted."
                ),
                styles["footnote"],
            )
        )
    return result


def _page_chrome(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    page_width, page_height = A4
    canvas.setStrokeColor(colors.HexColor("#D7E0E6"))
    canvas.setLineWidth(0.5)
    canvas.line(
        18 * mm,
        page_height - 13 * mm,
        page_width - 18 * mm,
        page_height - 13 * mm,
    )
    canvas.line(18 * mm, 14 * mm, page_width - 18 * mm, 14 * mm)
    canvas.setFont("ReportNoto", 7.5)
    canvas.setFillColor(colors.HexColor("#657887"))
    canvas.drawString(18 * mm, page_height - 10 * mm, "Agentic RAG Analyst")
    canvas.drawRightString(page_width - 18 * mm, 9.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(
    document: ReportDocument,
    assets: dict[str, dict[str, Any]],
    read_asset: Callable[[dict[str, Any]], bytes],
) -> bytes:
    """Render a bounded report document using only its declared artifacts."""
    if not isinstance(document, ReportDocument):
        raise TypeError("document must be a validated ReportDocument")
    refs = list(
        dict.fromkeys(
            block.artifact_id
            for section in document.sections
            for block in section.blocks
            if isinstance(block, (FigureBlock, TableBlock))
        )
    )
    total = 0
    loaded: dict[str, tuple[dict[str, Any], bytes]] = {}
    for artifact_id in refs:
        metadata, content = _metadata_bytes(artifact_id, assets, read_asset)
        total += len(content)
        if total > MAX_TOTAL_ASSET_BYTES:
            raise ValueError("report artifact total exceeds safe bounds")
        loaded[artifact_id] = (metadata, content)

    styles = _styles()
    story: list[Any] = [Paragraph(_markup(document.title, bold=True), styles["title"])]
    for section in document.sections:
        story.append(Paragraph(_markup(section.heading, bold=True), styles["section"]))
        for block in section.blocks:
            if isinstance(block, ParagraphBlock):
                story.append(Paragraph(_markup(block.text), styles["body"]))
            elif isinstance(block, FigureBlock):
                metadata, content = loaded[block.artifact_id]
                story.extend(_figure(block, metadata, content, styles))
            elif isinstance(block, TableBlock):
                metadata, content = loaded[block.artifact_id]
                story.extend(_table(block, metadata, content, styles))
            story.append(Spacer(1, 2 * mm))

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=document.title,
        author="Agentic RAG Analyst",
        pageCompression=1,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=_page_chrome))
    doc.build(story)
    return output.getvalue()

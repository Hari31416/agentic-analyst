"""Generate image-only scans, mixed/rotated/noisy PDFs and a wide digital table."""

from __future__ import annotations
import io
import json
from pathlib import Path
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from PIL import Image, ImageFilter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "fixtures" / "v2"


def scanned_pdf(
    source: Path, target: Path, *, rotate: bool = False, noisy: bool = False
) -> None:
    pdf = pdfium.PdfDocument(str(source))
    stream = io.BytesIO()
    c = canvas.Canvas(stream, invariant=1)
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=3)
            image = bitmap.to_pil()
            if noisy:
                image = image.resize((image.width // 2, image.height // 2)).filter(
                    ImageFilter.GaussianBlur(0.65)
                )
            if rotate:
                image = image.rotate(90, expand=True)
            width, height = image.size
            c.setPageSize((width / 3, height / 3))
            c.drawImage(ImageReader(image), 0, 0, width=width / 3, height=height / 3)
            c.showPage()
            image.close()
            bitmap.close()
            page.close()
        c.save()
        target.write_bytes(stream.getvalue())
    finally:
        pdf.close()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for language in ("en", "hi"):
        scanned_pdf(
            ROOT / "fixtures" / "v1" / f"applications-{language}.pdf",
            OUTPUT / f"scanned-{language}.pdf",
        )
    scanned_pdf(
        ROOT / "fixtures" / "v1" / "applications-en.pdf",
        OUTPUT / "rotated-en.pdf",
        rotate=True,
    )
    scanned_pdf(
        ROOT / "fixtures" / "v1" / "applications-hi.pdf",
        OUTPUT / "noisy-hi.pdf",
        noisy=True,
    )
    writer = PdfWriter()
    writer.add_page(
        PdfReader(ROOT / "fixtures" / "v1" / "applications-en.pdf").pages[0]
    )
    writer.add_page(PdfReader(OUTPUT / "scanned-hi.pdf").pages[0])
    with (OUTPUT / "mixed.pdf").open("wb") as handle:
        writer.write(handle)
    c = canvas.Canvas(str(OUTPUT / "wide-table.pdf"), pagesize=(900, 600), invariant=1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, 555, "1. Approved grants")
    headers = [
        "application_id",
        "name",
        "district",
        "grant_inr",
        "income_inr",
        "status",
    ]
    rows = [headers] + [
        [
            f"APP-{index:03d}",
            f"Applicant {index}",
            "Delhi",
            str(index * 5000),
            "180000",
            "active",
        ]
        for index in range(1, 15)
    ]
    xs = [40, 170, 300, 430, 560, 690, 850]
    top = 510
    for row_index, row in enumerate(rows):
        y = top - row_index * 26
        c.setFont("Helvetica-Bold" if row_index == 0 else "Helvetica", 11)
        for col, value in enumerate(row):
            c.drawString(xs[col] + 5, y - 18, value)
    for x in xs:
        c.line(x, top, x, top - len(rows) * 26)
    for row in range(len(rows) + 1):
        c.line(xs[0], top - row * 26, xs[-1], top - row * 26)
    c.save()
    truth = {
        "version": "v2",
        "review": "Synthetic strings and coordinates are the source of ground truth. Human corpus review remains separate.",
        "wide_table": {"headers": headers, "rows": rows[1:], "grant_total": 525000},
        "scans": {},
    }
    for language in ("en", "hi"):
        reader = PdfReader(ROOT / "fixtures" / "v1" / f"applications-{language}.pdf")
        truth["scans"][language] = "\n".join(
            page.extract_text(extraction_mode="layout") or "" for page in reader.pages
        )
    (OUTPUT / "ground-truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()

"""Bounded multilingual PDF report rendering."""

from app.reports.renderer import ReportParagraph, capabilities, render_pdf
from app.reports.schemas import ReportDocument

__all__ = ["ReportDocument", "ReportParagraph", "capabilities", "render_pdf"]

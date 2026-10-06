"""Embed comparison data into a self-contained offline HTML viewer."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evals/reviews/real-v1-krutrim-matrix-2026-10-05"

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Model Evaluation Matrix | Real-v1 Benchmark</title>
<style>
:root {
  --bg: #f8fafc;
  --surface: #ffffff;
  --surface-raised: #f1f5f9;
  --surface-subtle: #f8fafc;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-dim: #94a3b8;
  --primary: #2563eb;
  --primary-subtle: #eff6ff;
  --primary-border: #bfdbfe;
  --complete: #059669;
  --complete-bg: #ecfdf5;
  --complete-border: #a7f3d0;
  --complete-text: #065f46;
  --partial: #d97706;
  --partial-bg: #fffbeb;
  --partial-border: #fde68a;
  --partial-text: #92400e;
  --failed: #dc2626;
  --failed-bg: #fef2f2;
  --failed-border: #fecaca;
  --failed-text: #991b1b;
  --badge-blue: #0284c7;
  --badge-blue-bg: #f0f9ff;
  --badge-blue-border: #bae6fd;
  --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.05);
  --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.05);
  --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -4px rgba(0,0,0,0.04);
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace;
}
*, *:before, *:after { box-sizing: border-box; }
body {
  margin: 0;
  background-color: var(--bg);
  color: var(--text-main);
  font-family: var(--font-sans);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }
button, input, select { font-family: inherit; font-size: inherit; }
button { cursor: pointer; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.app-container {
  max-width: 1480px;
  margin: 0 auto;
  padding: 24px 28px 72px;
}
/* Top App Header */
.top-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
  gap: 16px;
  flex-wrap: wrap;
}
.brand-group {
  display: flex;
  align-items: center;
  gap: 14px;
}
.brand-title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-main);
}
.brand-tag {
  display: inline-flex;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--surface-raised);
  color: var(--text-muted);
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
}
.top-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.meta-chip {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 4px 10px;
  border-radius: 9999px;
  box-shadow: var(--shadow-sm);
}
.btn-scope {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  padding: 5px 12px;
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}
.btn-scope:hover {
  background: var(--surface-raised);
  color: var(--text-main);
}
/* Page Hero */
.dashboard-hero {
  padding: 24px 0 20px;
}
.hero-headline {
  margin: 0 0 8px;
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.03em;
  color: var(--text-main);
}
.hero-desc {
  margin: 0;
  font-size: 14px;
  color: var(--text-muted);
  max-width: 900px;
  line-height: 1.6;
}
/* Executive Model Grid */
.model-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin: 20px 0 32px;
}
.model-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
  position: relative;
  display: flex;
  flex-direction: column;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.model-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-md);
}
.model-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.model-name {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-main);
  overflow-wrap: anywhere;
}
.model-sub {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-dim);
  margin-top: 2px;
}
.rank-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 9999px;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.score-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 10px;
}
.score-rate {
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1;
  color: var(--text-main);
}
.score-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.score-bar {
  height: 8px;
  border-radius: 9999px;
  background: var(--surface-raised);
  display: flex;
  overflow: hidden;
  margin-bottom: 14px;
}
.score-bar span { height: 100%; }
.pill-counts {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  font-family: var(--font-mono);
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.pill-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.pill-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-top: auto;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.stat-box {
  display: flex;
  flex-direction: column;
}
.stat-val {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 700;
  color: var(--text-main);
}
.stat-sub {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-top: 2px;
}
/* Section Headers */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 32px 0 16px;
  flex-wrap: wrap;
  gap: 12px;
}
.section-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-main);
}
.section-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}
/* Visualizations Grid */
.charts-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 32px;
}
.chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
}
.chart-header {
  margin-bottom: 14px;
}
.chart-title {
  margin: 0 0 3px;
  font-size: 14px;
  font-weight: 700;
  color: var(--text-main);
}
.chart-subtitle {
  margin: 0;
  font-size: 11px;
  color: var(--text-muted);
}
.chart-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  justify-content: center;
}
.bar-row {
  display: grid;
  grid-template-columns: 120px 1fr 65px;
  align-items: center;
  gap: 10px;
  font-size: 11px;
}
.bar-label {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar-track {
  height: 14px;
  border-radius: 4px;
  background: var(--surface-raised);
  overflow: hidden;
  display: flex;
  position: relative;
}
.bar-fill {
  height: 100%;
}
.bar-value {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  text-align: right;
  color: var(--text-muted);
}
.chart-legend {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--surface-raised);
  font-size: 11px;
  color: var(--text-muted);
  flex-wrap: wrap;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.legend-sq {
  width: 9px;
  height: 9px;
  border-radius: 2px;
}
/* Tradeoff Table Card */
.tradeoff-card {
  grid-column: span 3;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 32px;
}
.table-simple {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 12px;
}
.table-simple th {
  text-align: left;
  padding: 8px 12px;
  font-size: 11px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: var(--surface-raised);
}
.table-simple td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.table-simple tr:hover td {
  background: var(--surface-subtle);
}
/* Explorer Controls & Toolbar */
.explorer-section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.explorer-toolbar {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.toolbar-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.view-toggle {
  display: inline-flex;
  background: var(--surface-raised);
  padding: 3px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
}
.view-btn {
  border: none;
  background: transparent;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 12px;
  border-radius: 4px;
  color: var(--text-muted);
  transition: all 0.15s ease;
}
.view-btn.active {
  background: var(--surface);
  color: var(--text-main);
  box-shadow: var(--shadow-sm);
}
.search-box {
  flex: 1;
  max-width: 440px;
  position: relative;
}
.search-input {
  width: 100%;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  background: var(--surface);
  color: var(--text-main);
  transition: border-color 0.15s ease;
}
.search-input:focus {
  border-color: var(--primary);
}
.filters-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
}
.filter-select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 11px;
  color: var(--text-main);
}
.chip-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.chip-btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-muted);
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 9999px;
  transition: all 0.12s ease;
}
.chip-btn:hover {
  border-color: var(--border-strong);
  color: var(--text-main);
}
.chip-btn.active {
  background: var(--primary-subtle);
  border-color: var(--primary-border);
  color: var(--primary);
  font-weight: 600;
}
.btn-reset {
  margin-left: auto;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-muted);
  font-size: 11px;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
}
.btn-reset:hover {
  background: var(--surface-raised);
  color: var(--text-main);
}
.explorer-summary-bar {
  padding: 8px 20px;
  background: var(--surface-subtle);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
}
/* Matrix Grid View */
.matrix-container {
  overflow-x: auto;
}
.matrix-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 1080px;
}
.matrix-table th {
  padding: 10px 14px;
  text-align: left;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--text-muted);
  background: var(--surface-raised);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 1;
}
.matrix-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.matrix-table tbody tr {
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.matrix-table tbody tr:hover td {
  background: #f1f5f9;
}
.case-cell {
  max-width: 320px;
}
.case-title {
  font-weight: 700;
  font-size: 13px;
  color: var(--text-main);
  margin-bottom: 2px;
}
.case-prompt {
  font-size: 11px;
  color: var(--text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.4;
  margin-bottom: 4px;
}
.case-tags {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.tag-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  background: var(--surface-raised);
  color: var(--text-muted);
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--border);
}
.model-outcome-cell {
  min-width: 170px;
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  transition: background 0.12s ease;
}
.model-outcome-cell:hover {
  background: #e2e8f0;
}
.badge-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  text-transform: uppercase;
}
.b-complete { background: var(--complete-bg); color: var(--complete-text); border: 1px solid var(--complete-border); }
.b-partial { background: var(--partial-bg); color: var(--partial-text); border: 1px solid var(--partial-border); }
.b-failed { background: var(--failed-bg); color: var(--failed-text); border: 1px solid var(--failed-border); }
.b-unresolved { background: var(--surface-raised); color: var(--text-muted); border: 1px solid var(--border); }
.b-auto { background: var(--badge-blue-bg); color: var(--badge-blue); border: 1px solid var(--badge-blue-border); }
.cell-meta {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 4px;
  display: flex;
  gap: 6px;
}
.btn-inspect {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--primary);
  font-size: 11px;
  font-weight: 600;
  padding: 5px 9px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  transition: all 0.12s ease;
}
.btn-inspect:hover {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
/* List Runs View */
.runs-table-wrap {
  overflow-x: auto;
}
.runs-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 1000px;
}
.runs-table th {
  padding: 10px 12px;
  text-align: left;
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  background: var(--surface-raised);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 1;
}
.runs-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  vertical-align: top;
}
.runs-table tbody tr {
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.runs-table tbody tr:hover td {
  background: #f1f5f9;
}
.empty-state {
  padding: 48px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}
/* Modal Dialogs */
dialog {
  width: min(1320px, 96vw);
  max-height: 92vh;
  padding: 0;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--text-main);
  box-shadow: var(--shadow-lg);
}
dialog::backdrop {
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(2px);
}
.modal-header {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 16px 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}
.modal-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-main);
}
.modal-sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}
.modal-close {
  border: 1px solid var(--border);
  background: var(--surface);
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  font-size: 18px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-close:hover {
  background: var(--surface-raised);
  color: var(--text-main);
}
.modal-body {
  padding: 20px 24px 28px;
  overflow-y: auto;
}
/* Detail Question Box */
.question-box {
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-left: 4px solid var(--primary);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  margin-bottom: 20px;
}
.question-label {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 700;
  color: var(--primary);
  margin-bottom: 4px;
}
#modalQuestionText {
  font-size: 13.5px;
  line-height: 1.5;
  color: var(--text-main);
  white-space: pre-wrap;
  word-break: break-word;
}
/* Side-by-side Answer Grid */
.detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(280px, 1fr));
  gap: 16px;
  overflow-x: auto;
}
.answer-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.answer-header {
  padding: 12px 14px;
  background: var(--surface-raised);
  border-bottom: 1px solid var(--border);
}
.answer-model-name {
  font-weight: 700;
  font-size: 13px;
  color: var(--text-main);
  margin-bottom: 6px;
}
.answer-chips {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.answer-content {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rendered-markdown {
  max-height: 340px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-main);
  padding: 8px 10px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.rendered-markdown p { margin: 6px 0; }
.rendered-markdown p:first-child { margin-top: 0; }
.rendered-markdown p:last-child { margin-bottom: 0; }
.rendered-markdown h3, .rendered-markdown h4 { margin: 10px 0 4px; font-size: 13px; }
.rendered-markdown pre {
  background: #1e293b;
  color: #f8fafc;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  overflow-x: auto;
}
.rendered-markdown code {
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--surface-raised);
  padding: 1px 4px;
  border-radius: 3px;
}
.rendered-markdown pre code { background: transparent; padding: 0; }
.rendered-markdown table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  margin: 6px 0;
}
.rendered-markdown th, .rendered-markdown td {
  padding: 4px 6px;
  border: 1px solid var(--border);
  text-align: left;
}
.rendered-markdown th { background: var(--surface-raised); }
.review-callout {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  font-size: 11px;
}
.review-callout.complete {
  background: var(--complete-bg);
  border-left: 3px solid var(--complete);
  color: var(--complete-text);
}
.review-callout.partial {
  background: var(--partial-bg);
  border-left: 3px solid var(--partial);
  color: var(--partial-text);
}
.review-callout.failed {
  background: var(--failed-bg);
  border-left: 3px solid var(--failed);
  color: var(--failed-text);
}
.review-title {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 10px;
  text-transform: uppercase;
  margin-bottom: 4px;
}
.review-reasons {
  margin: 4px 0 0;
  padding-left: 14px;
}
.telemetry-box {
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.5;
}
.trace-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.trace-title {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--text-muted);
}
.trace-list {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 10px;
  font-family: var(--font-mono);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 160px;
  overflow-y: auto;
}
.trace-list li {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
}
.trace-list li.is-rejected {
  background: var(--failed-bg);
  border-left: 3px solid var(--failed);
}
.trace-list li.is-allowed {
  border-left: 3px solid var(--complete);
}
.trace-list li:last-child { border-bottom: none; }
.tool-header {
  color: var(--primary);
  font-weight: 600;
}
.tool-detail {
  color: var(--text-muted);
  margin-top: 2px;
  font-family: var(--font-sans);
  font-size: 10px;
  word-break: break-word;
}
.artifact-img {
  max-width: 100%;
  max-height: 200px;
  object-fit: contain;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff;
  margin-top: 6px;
}
.links-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 10px;
  font-family: var(--font-mono);
  padding-top: 4px;
}
/* Scope Modal Styling */
.scope-content {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-main);
}
.scope-content h3 {
  font-size: 15px;
  margin: 16px 0 6px;
}
.scope-content h3:first-child { margin-top: 0; }
.scope-content ul {
  padding-left: 20px;
  margin: 6px 0 16px;
}
.scope-content li { margin-bottom: 6px; }
/* App Footer */
.app-footer {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
  flex-wrap: wrap;
  gap: 12px;
}
@media (max-width: 1200px) {
  .model-grid { grid-template-columns: repeat(2, 1fr); }
  .charts-grid { grid-template-columns: repeat(2, 1fr); }
  .tradeoff-card { grid-column: span 2; }
}
@media (max-width: 768px) {
  .app-container { padding: 16px 14px 40px; }
  .model-grid { grid-template-columns: 1fr; }
  .charts-grid { grid-template-columns: 1fr; }
  .tradeoff-card { grid-column: 1; }
  .detail-grid { grid-template-columns: 1fr; }
  .hero-headline { font-size: 22px; }
}
</style>
</head>
<body>
<div class="app-container">
  <header class="top-nav">
    <div class="brand-group">
      <span class="brand-title">Agentic RAG Analyst</span>
    </div>
    <div class="top-meta">
      <span class="meta-chip">30 Cases</span>
      <span class="meta-chip">4 Models · 120 Runs</span>
      <span class="meta-chip">Evaluator: GPT-6 Luna</span>
      <button class="btn-scope" id="openScopeBtn" type="button">Scope & Methodology</button>
    </div>
  </header>

  <section class="dashboard-hero">
    <h1 class="hero-headline">Model Evaluation & Benchmark Matrix</h1>
    <p class="hero-desc">Comparative analysis across 30 identical retail analytics and macroeconomic survey tasks. Evaluating task completion accuracy, latency profiles, token efficiency, and agentic tool traces.</p>
  </section>

  <!-- Executive Model Grid -->
  <section aria-label="Model Scorecards">
    <div class="model-grid" id="modelGrid"></div>
  </section>

  <!-- Visualizations & Comparative Analytics -->
  <div class="section-header">
    <div>
      <h2 class="section-title">Comparative Analytics</h2>
      <p class="section-desc">Task completion, latency profiles, token consumption, domain categories, and multilingual resilience</p>
    </div>
  </div>

  <section class="charts-grid" id="chartsContainer"></section>

  <section class="tradeoff-card">
    <div class="chart-header">
      <h3 class="chart-title">Key Model Performance Summary & Trade-offs</h3>
      <p class="chart-subtitle">Direct comparison of accuracy, latency, token consumption, and agent behavior</p>
    </div>
    <div style="overflow-x: auto;">
      <table class="table-simple" id="summaryTable">
        <thead>
          <tr>
            <th>Model</th>
            <th>Task Complete</th>
            <th>Acceptable (Comp + Part)</th>
            <th>Latency (p50 / p95)</th>
            <th>Total Tokens</th>
            <th>Avg Tokens/Task</th>
            <th>Tool Calls</th>
            <th>Key Characteristics</th>
          </tr>
        </thead>
        <tbody id="summaryTableBody"></tbody>
      </table>
    </div>
  </section>

  <!-- Agent Tool Call & Guardrail Telemetry Section -->
  <div class="section-header">
    <div>
      <h2 class="section-title">Tool Execution & Guardrail Telemetry</h2>
      <p class="section-desc">Tool invocation volume, allowed vs. rejected calls, action allowlist compliance, and agent tool discipline across models</p>
    </div>
  </div>

  <section class="charts-grid" id="toolChartsContainer"></section>

  <section class="tradeoff-card">
    <div class="chart-header">
      <h3 class="chart-title">Tool Guardrail & Allowlist Compliance Matrix</h3>
      <p class="chart-subtitle">Direct comparison of tool volume, rejection rates, and security policy violations</p>
    </div>
    <div style="overflow-x: auto;">
      <table class="table-simple" id="toolSummaryTable">
        <thead>
          <tr>
            <th>Model</th>
            <th>Dispatched</th>
            <th>Retained Calls</th>
            <th>Allowed Calls</th>
            <th>Rejected Calls</th>
            <th>Allowlist Violations</th>
            <th>Top Blocked Tool</th>
            <th>Guardrail & Tool Discipline Profile</th>
          </tr>
        </thead>
        <tbody id="toolSummaryTableBody"></tbody>
      </table>
    </div>
  </section>

  <!-- Case Explorer & Matrix Grid -->
  <div class="section-header">
    <div>
      <h2 class="section-title">Case Explorer & Comparison Matrix</h2>
      <p class="section-desc">Switch between the side-by-side Matrix Grid and Detailed Run List. Open any case to inspect answers and execution traces.</p>
    </div>
  </div>

  <section class="explorer-section">
    <div class="explorer-toolbar">
      <div class="toolbar-top">
        <div class="view-toggle" role="tablist" aria-label="View Mode">
          <button class="view-btn active" id="btnViewMatrix" type="button" role="tab" aria-selected="true">Matrix Grid View</button>
          <button class="view-btn" id="btnViewList" type="button" role="tab" aria-selected="false">All Runs List</button>
        </div>
        <div class="search-box">
          <input class="search-input" id="searchInput" type="search" placeholder="Search case ID, prompt text, answers, or tags..." aria-label="Search cases">
        </div>
      </div>

      <div class="filters-row">
        <div class="chip-group" id="quickChips">
          <button class="chip-btn active" data-filter="all" type="button">All 30 Cases</button>
          <button class="chip-btn" data-filter="hard" type="button">Challenging Cases (Any Partial / Fail)</button>
          <button class="chip-btn" data-filter="tool-rejections" type="button">Has Rejected Tools</button>
          <button class="chip-btn" data-filter="allowlist-violation" type="button">Allowlist Violations</button>
          <button class="chip-btn" data-filter="unanimous" type="button">All Models Passed</button>
          <button class="chip-btn" data-filter="multilingual" type="button">Multilingual (Hindi / Hinglish)</button>
          <button class="chip-btn" data-filter="artifacts" type="button">Artifact & Chart Tasks</button>
        </div>

        <div class="filter-group">
          <label for="categoryFilter">Category:</label>
          <select class="filter-select" id="categoryFilter">
            <option value="">All Categories</option>
          </select>
        </div>

        <div class="filter-group">
          <label for="languageFilter">Language:</label>
          <select class="filter-select" id="languageFilter">
            <option value="">All Languages</option>
          </select>
        </div>

        <div class="filter-group" id="modelFilterGroup" style="display: none;">
          <label for="modelFilter">Model:</label>
          <select class="filter-select" id="modelFilter">
            <option value="">All Models</option>
          </select>
        </div>

        <div class="filter-group" id="statusFilterGroup" style="display: none;">
          <label for="statusFilter">Status:</label>
          <select class="filter-select" id="statusFilter">
            <option value="">All Statuses</option>
          </select>
        </div>

        <button class="btn-reset" id="resetBtn" type="button">Reset Filters</button>
      </div>
    </div>

    <div class="explorer-summary-bar">
      <span id="resultCount">Showing 30 cases</span>
      <span id="resultModeHint">Click any row or cell to compare all 4 model answers</span>
    </div>

    <!-- Matrix View -->
    <div class="matrix-container" id="matrixView">
      <table class="matrix-table">
        <thead>
          <tr>
            <th style="width: 320px;">Case & Prompt</th>
            <th>gpt-oss-120b</th>
            <th>gemma-4-31b-it</th>
            <th>gemma-4-26B-A4B-it</th>
            <th>Qwen3.6-35B-A3B</th>
            <th style="text-align: right; width: 110px;">Compare</th>
          </tr>
        </thead>
        <tbody id="matrixTableBody"></tbody>
      </table>
    </div>

    <!-- List View -->
    <div class="runs-table-wrap" id="listView" style="display: none;">
      <table class="runs-table">
        <thead>
          <tr>
            <th>Case ID</th>
            <th>Model</th>
            <th>Category</th>
            <th>Language</th>
            <th>AI Task Label</th>
            <th>Auto Check</th>
            <th>Latency</th>
            <th>Tokens</th>
            <th>Tool Calls</th>
            <th style="text-align: right;">Action</th>
          </tr>
        </thead>
        <tbody id="runsTableBody"></tbody>
      </table>
    </div>

    <div class="empty-state" id="emptyState" style="display: none;">
      No cases or runs match the selected filters.
    </div>
  </section>

  <footer class="app-footer">
    <span>Evaluation Matrix · Real-v1 · AI-Assisted First-Pass Review</span>
    <span>Dataset: 30 Retail Analytics & Economic Survey Tasks · Qwen3.5-9B Excluded</span>
  </footer>
</div>

<!-- Case Deep Comparison Modal -->
<dialog id="caseModal" aria-labelledby="caseModalTitle">
  <div class="modal-header">
    <div>
      <h2 class="modal-title" id="caseModalTitle">Case Comparison</h2>
      <p class="modal-sub" id="caseModalSub"></p>
    </div>
    <button class="modal-close" id="closeCaseModal" type="button" aria-label="Close dialog">×</button>
  </div>
  <div class="modal-body">
    <div class="question-box">
      <div class="question-label">Task Prompt</div>
      <p class="question-text" id="modalQuestionText"></p>
    </div>
    <div class="detail-grid" id="modalAnswerGrid"></div>
  </div>
</dialog>

<!-- Scope & Methodology Modal -->
<dialog id="scopeModal" aria-labelledby="scopeModalTitle">
  <div class="modal-header">
    <div>
      <h2 class="modal-title" id="scopeModalTitle">Evaluation Scope & Methodology</h2>
      <p class="modal-sub">Evaluation bounds, review provenance, and telemetry notes</p>
    </div>
    <button class="modal-close" id="closeScopeModal" type="button" aria-label="Close dialog">×</button>
  </div>
  <div class="modal-body scope-content">
    <h3>Evaluation Methodology</h3>
    <p>Each model was evaluated across 30 identical tasks from the Real-v1 benchmark suite, covering complex multi-step retail analytics and macroeconomic document retrieval.</p>
    
    <h3>Review Provenance</h3>
    <p id="scopeProvenance"></p>

    <h3>Known Limitations & Boundary Conditions</h3>
    <ul id="scopeLimitationsList"></ul>

    <h3>Model Selection & Exclusions</h3>
    <p id="scopeExclusionText"></p>
  </div>
</dialog>

<script>
const DATA = __DATA__;
const models = DATA.models;
const allRuns = models.flatMap(m => m.cases);

// Unique cases list
const uniqueCaseMap = new Map();
models[0].cases.forEach(c => {
  uniqueCaseMap.set(c.case_id, {
    case_id: c.case_id,
    question: c.question,
    language: c.language,
    task_type: c.task_type,
    tags: c.tags || [],
    runsByModel: {}
  });
});
models.forEach(m => {
  m.cases.forEach(c => {
    if (uniqueCaseMap.has(c.case_id)) {
      uniqueCaseMap.get(c.case_id).runsByModel[m.model] = c;
    }
  });
});
const uniqueCases = Array.from(uniqueCaseMap.values());

const palette = {
  complete: '#059669',
  partial: '#d97706',
  failed: '#dc2626',
  unresolved: '#94a3b8'
};

function fmt(n, d = 1) {
  if (n == null || !Number.isFinite(Number(n))) return '—';
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: d });
}

function badge(label, kind) {
  const span = document.createElement('span');
  span.className = `badge-pill b-${kind}`;
  span.textContent = label;
  return span;
}

// Render Scope Modal
function initScopeModal() {
  const btn = document.getElementById('openScopeBtn');
  const modal = document.getElementById('scopeModal');
  const close = document.getElementById('closeScopeModal');
  btn.addEventListener('click', () => modal.showModal());
  close.addEventListener('click', () => modal.close());
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.close(); });

  document.getElementById('scopeProvenance').textContent = DATA.review_provenance || 'AI-assisted task evaluation.';
  const limList = document.getElementById('scopeLimitationsList');
  limList.innerHTML = '';
  (DATA.limitations || []).forEach(l => {
    const li = document.createElement('li');
    li.textContent = l;
    limList.appendChild(li);
  });
  const ex = DATA.excluded_models?.[0];
  document.getElementById('scopeExclusionText').textContent = ex 
    ? `${ex.model} was excluded at user request and is absent from all benchmark aggregates.` 
    : 'No models excluded.';
}

// Render Model Scorecards
function renderModelCards() {
  const container = document.getElementById('modelGrid');
  container.innerHTML = '';

  const modelCharacteristics = {
    'gpt-oss-120b': 'High-capacity dense open-source model. Strong on complex reasoning.',
    'gemma-4-31b-it': 'Ultra-fast dense model with lowest latency and minimal token consumption.',
    'gemma-4-26B-A4B-it': 'MoE (26B total, 4B active). Balanced speed and high completion.',
    'Qwen3.6-35B-A3B': 'MoE (35B total, 3B active). Benchmark leader in overall task completion.'
  };

  models.forEach(m => {
    const total = m.trials || 30;
    const comp = m.review_counts?.complete || 0;
    const part = m.review_counts?.partial || 0;
    const fail = m.review_counts?.failed || 0;
    const compRate = ((comp / total) * 100).toFixed(1);
    const acceptRate = (((comp + part) / total) * 100).toFixed(1);

    const card = document.createElement('div');
    card.className = 'model-card';

    const header = document.createElement('div');
    header.className = 'model-card-header';
    header.innerHTML = `
      <div>
        <h3 class="model-name">${m.model}</h3>
      </div>
      <span class="rank-badge">${comp}/30 Complete</span>
    `;
    card.appendChild(header);

    const scoreRow = document.createElement('div');
    scoreRow.className = 'score-row';
    scoreRow.innerHTML = `
      <div class="score-rate">${compRate}%</div>
      <div class="score-label">Complete (${acceptRate}% Acceptable)</div>
    `;
    card.appendChild(scoreRow);

    const bar = document.createElement('div');
    bar.className = 'score-bar';
    bar.innerHTML = `
      <span style="width: ${(comp / total) * 100}%; background: var(--complete);" title="Complete: ${comp}"></span>
      <span style="width: ${(part / total) * 100}%; background: var(--partial);" title="Partial: ${part}"></span>
      <span style="width: ${(fail / total) * 100}%; background: var(--failed);" title="Failed: ${fail}"></span>
    `;
    card.appendChild(bar);

    const pills = document.createElement('div');
    pills.className = 'pill-counts';
    pills.innerHTML = `
      <span class="pill-item"><span class="pill-dot" style="background: var(--complete);"></span>${comp} Complete</span>
      <span class="pill-item"><span class="pill-dot" style="background: var(--partial);"></span>${part} Partial</span>
      <span class="pill-item"><span class="pill-dot" style="background: var(--failed);"></span>${fail} Failed</span>
    `;
    card.appendChild(pills);

    const stats = document.createElement('div');
    stats.className = 'stats-grid';
    const totalTokens = m.model_usage?.total_tokens || 0;
    const avgTokens = Math.round(totalTokens / total);
    const avgTools = (m.tool_call_count / total).toFixed(1);

    stats.innerHTML = `
      <div class="stat-box">
        <span class="stat-val">${fmt(m.median_seconds)}s</span>
        <span class="stat-sub">p50 Latency (p95: ${fmt(m.p95_seconds)}s)</span>
      </div>
      <div class="stat-box">
        <span class="stat-val">${fmt(totalTokens, 0)}</span>
        <span class="stat-sub">Total Tokens (~${fmt(avgTokens, 0)}/task)</span>
      </div>
      <div class="stat-box">
        <span class="stat-val">${m.tool_call_count}</span>
        <span class="stat-sub">Tool Calls (~${avgTools}/task)</span>
      </div>
      <div class="stat-box">
        <span class="stat-val">${m.model_attempt_count}</span>
        <span class="stat-sub">Model Attempts</span>
      </div>
    `;
    card.appendChild(stats);

    container.appendChild(card);
  });
}

// Render Comparative Analytics Charts
function renderAnalyticsCharts() {
  const container = document.getElementById('chartsContainer');
  container.innerHTML = '';

  // 1. Task Completion Breakdown Chart
  const card1 = document.createElement('div');
  card1.className = 'chart-card';
  card1.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Task Completion Distribution</h3>
      <p class="chart-subtitle">Evaluated outcomes across 30 identical tasks</p>
    </div>
    <div class="chart-body" id="chartTaskOutcomes"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: var(--complete);"></span>Complete</span>
      <span class="legend-item"><span class="legend-sq" style="background: var(--partial);"></span>Partial</span>
      <span class="legend-item"><span class="legend-sq" style="background: var(--failed);"></span>Failed</span>
    </div>
  `;
  container.appendChild(card1);

  const body1 = card1.querySelector('#chartTaskOutcomes');
  models.forEach(m => {
    const total = m.trials || 30;
    const c = m.review_counts?.complete || 0;
    const p = m.review_counts?.partial || 0;
    const f = m.review_counts?.failed || 0;
    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${(c / total) * 100}%; background: var(--complete);" title="Complete: ${c}"></span>
        <span class="bar-fill" style="width: ${(p / total) * 100}%; background: var(--partial);" title="Partial: ${p}"></span>
        <span class="bar-fill" style="width: ${(f / total) * 100}%; background: var(--failed);" title="Failed: ${f}"></span>
      </div>
      <span class="bar-value">${c}/30 (${((c / total) * 100).toFixed(0)}%)</span>
    `;
    body1.appendChild(row);
  });

  // 2. Query Latency Comparison Chart (p50 & p95)
  const card2 = document.createElement('div');
  card2.className = 'chart-card';
  card2.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Query Latency Profile</h3>
      <p class="chart-subtitle">Median (p50) and 95th-percentile (p95) latency</p>
    </div>
    <div class="chart-body" id="chartLatency"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: #2563eb;"></span>Median (p50)</span>
      <span class="legend-item"><span class="legend-sq" style="background: #93c5fd;"></span>Tail (p95)</span>
    </div>
  `;
  container.appendChild(card2);

  const body2 = card2.querySelector('#chartLatency');
  const maxP95 = Math.max(...models.map(m => m.p95_seconds || 1));
  models.forEach(m => {
    const row = document.createElement('div');
    row.className = 'bar-row';
    const p50w = ((m.median_seconds / maxP95) * 100).toFixed(1);
    const p95w = ((m.p95_seconds / maxP95) * 100).toFixed(1);
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div style="display: flex; flex-direction: column; gap: 3px; width: 100%;">
        <div style="height: 7px; border-radius: 2px; background: var(--surface-raised); overflow: hidden;">
          <div style="height: 100%; width: ${p50w}%; background: #2563eb;" title="p50: ${fmt(m.median_seconds)}s"></div>
        </div>
        <div style="height: 7px; border-radius: 2px; background: var(--surface-raised); overflow: hidden;">
          <div style="height: 100%; width: ${p95w}%; background: #93c5fd;" title="p95: ${fmt(m.p95_seconds)}s"></div>
        </div>
      </div>
      <span class="bar-value" style="font-size: 10px;">${fmt(m.median_seconds)}s / ${fmt(m.p95_seconds, 0)}s</span>
    `;
    body2.appendChild(row);
  });

  // 3. Token Consumption & Efficiency Chart
  const card3 = document.createElement('div');
  card3.className = 'chart-card';
  card3.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Token Consumption</h3>
      <p class="chart-subtitle">Total tokens used across all 30 benchmark tasks</p>
    </div>
    <div class="chart-body" id="chartTokens"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: #4f46e5;"></span>Prompt Tokens</span>
      <span class="legend-item"><span class="legend-sq" style="background: #c7d2fe;"></span>Completion Tokens</span>
    </div>
  `;
  container.appendChild(card3);

  const body3 = card3.querySelector('#chartTokens');
  const maxTok = Math.max(...models.map(m => m.model_usage?.total_tokens || 1));
  models.forEach(m => {
    const u = m.model_usage || {};
    const promptW = (((u.prompt_tokens || 0) / maxTok) * 100).toFixed(1);
    const compW = (((u.completion_tokens || 0) / maxTok) * 100).toFixed(1);
    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${promptW}%; background: #4f46e5;" title="Prompt: ${fmt(u.prompt_tokens, 0)}"></span>
        <span class="bar-fill" style="width: ${compW}%; background: #c7d2fe;" title="Completion: ${fmt(u.completion_tokens, 0)}"></span>
      </div>
      <span class="bar-value">${fmt(u.total_tokens / 1000, 0)}k</span>
    `;
    body3.appendChild(row);
  });

  // 4. Performance by Domain Category
  const card4 = document.createElement('div');
  card4.className = 'chart-card';
  card4.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Accuracy by Task Category</h3>
      <p class="chart-subtitle">Completion rate across domain categories</p>
    </div>
    <div class="chart-body" id="chartCategory"></div>
    <div class="chart-legend">
      <span class="legend-item">Retail Analysis (17 tasks)</span>
      <span class="legend-item">Doc Retrieval (9 tasks)</span>
    </div>
  `;
  container.appendChild(card4);

  const body4 = card4.querySelector('#chartCategory');
  const cats = ['retail data analysis', 'document retrieval'];
  models.forEach(m => {
    const retailTotal = m.cases.filter(c => c.task_type === 'retail data analysis');
    const retailPass = retailTotal.filter(c => c.review.overall_task === 'complete').length;
    const docTotal = m.cases.filter(c => c.task_type === 'document retrieval');
    const docPass = docTotal.filter(c => c.review.overall_task === 'complete').length;

    const row = document.createElement('div');
    row.className = 'bar-row';
    const rPct = Math.round((retailPass / retailTotal.length) * 100);
    const dPct = Math.round((docPass / docTotal.length) * 100);
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${rPct}%; background: #0891b2;" title="Retail: ${retailPass}/${retailTotal.length}"></span>
      </div>
      <span class="bar-value">${retailPass}/${retailTotal.length} (${rPct}%)</span>
    `;
    body4.appendChild(row);
  });

  // 5. Multilingual Performance Breakdown
  const card5 = document.createElement('div');
  card5.className = 'chart-card';
  card5.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Multilingual Resilience</h3>
      <p class="chart-subtitle">Task completion across English vs Hindi/Hinglish</p>
    </div>
    <div class="chart-body" id="chartMulti"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: #059669;"></span>English (23)</span>
      <span class="legend-item"><span class="legend-sq" style="background: #0284c7;"></span>Hindi / Hinglish (7)</span>
    </div>
  `;
  container.appendChild(card5);

  const body5 = card5.querySelector('#chartMulti');
  models.forEach(m => {
    const enTasks = m.cases.filter(c => c.language === 'en-IN');
    const enPass = enTasks.filter(c => c.review.overall_task === 'complete').length;
    const hiTasks = m.cases.filter(c => c.language !== 'en-IN');
    const hiPass = hiTasks.filter(c => c.review.overall_task === 'complete').length;

    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${(enPass / 23) * 50}%; background: #059669;" title="English: ${enPass}/23"></span>
        <span class="bar-fill" style="width: ${(hiPass / 7) * 50}%; background: #0284c7;" title="Hindi/Hinglish: ${hiPass}/7"></span>
      </div>
      <span class="bar-value">EN ${enPass} · HI ${hiPass}</span>
    `;
    body5.appendChild(row);
  });

  // 6. Tool Interactions & Reasoning Depth
  const card6 = document.createElement('div');
  card6.className = 'chart-card';
  card6.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Agent Tool Calls & Reasoning Depth</h3>
      <p class="chart-subtitle">Average tool calls executed per task</p>
    </div>
    <div class="chart-body" id="chartTools"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: #d97706;"></span>Tool Calls / Task</span>
    </div>
  `;
  container.appendChild(card6);

  const body6 = card6.querySelector('#chartTools');
  const maxToolsAvg = 8.0;
  models.forEach(m => {
    const avg = m.tool_call_count / (m.trials || 30);
    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${(avg / maxToolsAvg) * 100}%; background: #d97706;" title="Average ${avg.toFixed(1)} tools/task"></span>
      </div>
      <span class="bar-value">${avg.toFixed(1)} / task</span>
    `;
    body6.appendChild(row);
  });
}

// Render Summary Table
function renderSummaryTable() {
  const tbody = document.getElementById('summaryTableBody');
  tbody.innerHTML = '';

  const modelNotes = {
    'gpt-oss-120b': 'High reasoning capability on multi-step SQL queries; higher median latency; 18/30 completion.',
    'gemma-4-31b-it': 'Fastest response time (12.4s p50); lowest token usage (945k); strong baseline for cost-sensitive workloads.',
    'gemma-4-26B-A4B-it': 'MoE efficiency (4B active parameters); robust 73.3% completion; balanced latency and cost.',
    'Qwen3.6-35B-A3B': 'Top benchmark performer (80.0% complete, 93.3% acceptable); highest multilingual accuracy; higher token consumption.'
  };

  models.forEach(m => {
    const tr = document.createElement('tr');
    const total = m.trials || 30;
    const comp = m.review_counts?.complete || 0;
    const part = m.review_counts?.partial || 0;
    const totalTok = m.model_usage?.total_tokens || 0;
    const avgTok = Math.round(totalTok / total);

    tr.innerHTML = `
      <td style="font-weight: 700; font-family: var(--font-mono);">${m.model}</td>
      <td><span class="badge-pill b-complete">${comp}/${total} (${((comp / total) * 100).toFixed(1)}%)</span></td>
      <td><span class="badge-pill b-auto">${comp + part}/${total} (${(((comp + part) / total) * 100).toFixed(1)}%)</span></td>
      <td style="font-family: var(--font-mono);">${fmt(m.median_seconds)}s / ${fmt(m.p95_seconds, 0)}s</td>
      <td style="font-family: var(--font-mono);">${fmt(totalTok, 0)}</td>
      <td style="font-family: var(--font-mono);">${fmt(avgTok, 0)}</td>
      <td style="font-family: var(--font-mono);">${m.tool_call_count} (${(m.tool_call_count / total).toFixed(1)}/task)</td>
      <td style="color: var(--text-muted); font-size: 11px;">${modelNotes[m.model] || ''}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Render Tool Call & Rejection Analytics
function renderToolAnalytics() {
  const container = document.getElementById('toolChartsContainer');
  if (!container) return;
  container.innerHTML = '';

  // 1. Tool Call Decisions: Allowed vs Rejected
  const card1 = document.createElement('div');
  card1.className = 'chart-card';
  card1.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Tool Invocations: Allowed vs. Rejected</h3>
      <p class="chart-subtitle">Security guardrail and validation decisions across retained calls</p>
    </div>
    <div class="chart-body" id="chartToolDecisions"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: var(--complete);"></span>Allowed</span>
      <span class="legend-item"><span class="legend-sq" style="background: var(--failed);"></span>Rejected</span>
    </div>
  `;
  container.appendChild(card1);

  const body1 = card1.querySelector('#chartToolDecisions');
  models.forEach(m => {
    let allowed = 0, rejected = 0;
    m.cases.forEach(c => {
      (c.tool_calls || []).forEach(tc => {
        if (tc.decision === 'rejected' || tc.status === 'rejected') rejected++;
        else allowed++;
      });
    });
    const total = allowed + rejected || 1;
    const allowPct = ((allowed / total) * 100).toFixed(0);
    const rejPct = ((rejected / total) * 100).toFixed(0);

    const row = document.createElement('div');
    row.className = 'bar-row';
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${(allowed / total) * 100}%; background: var(--complete);" title="Allowed: ${allowed} (${allowPct}%)"></span>
        <span class="bar-fill" style="width: ${(rejected / total) * 100}%; background: var(--failed);" title="Rejected: ${rejected} (${rejPct}%)"></span>
      </div>
      <span class="bar-value">${rejected} rej (${rejPct}%)</span>
    `;
    body1.appendChild(row);
  });

  // 2. Most Frequently Blocked / Rejected Tool Types
  const card2 = document.createElement('div');
  card2.className = 'chart-card';
  card2.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Most Blocked / Rejected Tool Types</h3>
      <p class="chart-subtitle">Rejection frequency across all 120 benchmark runs</p>
    </div>
    <div class="chart-body" id="chartRejectedTypes"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: #dc2626;"></span>Rejected Invocations</span>
    </div>
  `;
  container.appendChild(card2);

  const body2 = card2.querySelector('#chartRejectedTypes');
  const rejectedToolCounts = {};
  models.forEach(m => {
    m.cases.forEach(c => {
      (c.tool_calls || []).forEach(tc => {
        if (tc.decision === 'rejected' || tc.status === 'rejected') {
          const name = tc.name || 'unknown';
          rejectedToolCounts[name] = (rejectedToolCounts[name] || 0) + 1;
        }
      });
    });
  });
  const sortedRej = Object.entries(rejectedToolCounts).sort((a, b) => b[1] - a[1]);
  const maxRej = sortedRej[0]?.[1] || 1;

  sortedRej.forEach(([name, count]) => {
    const row = document.createElement('div');
    row.className = 'bar-row';
    const w = ((count / maxRej) * 100).toFixed(1);
    row.innerHTML = `
      <span class="bar-label" title="${name}">${name}</span>
      <div class="bar-track">
        <span class="bar-fill" style="width: ${w}%; background: #dc2626;" title="${name}: ${count} rejections"></span>
      </div>
      <span class="bar-value">${count} blocked</span>
    `;
    body2.appendChild(row);
  });

  // 3. Model Attempts vs. Retained Tool Calls
  const card3 = document.createElement('div');
  card3.className = 'chart-card';
  card3.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Model Attempts vs. Retained Tools</h3>
      <p class="chart-subtitle">Telemetry comparison highlighting agent retry loops and tool retention</p>
    </div>
    <div class="chart-body" id="chartLoopOverhead"></div>
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-sq" style="background: #0284c7;"></span>Model Attempts</span>
      <span class="legend-item"><span class="legend-sq" style="background: #38bdf8;"></span>Retained Tools</span>
    </div>
  `;
  container.appendChild(card3);

  const body3 = card3.querySelector('#chartLoopOverhead');
  const maxAttempts = Math.max(...models.map(m => m.model_attempt_count || 1));
  models.forEach(m => {
    const attempts = m.model_attempt_count || 0;
    const retained = m.retained_tool_call_count || m.tool_call_count || 0;
    const row = document.createElement('div');
    row.className = 'bar-row';
    const aw = ((attempts / maxAttempts) * 100).toFixed(1);
    const rw = ((retained / maxAttempts) * 100).toFixed(1);
    row.innerHTML = `
      <span class="bar-label" title="${m.model}">${m.model}</span>
      <div style="display: flex; flex-direction: column; gap: 3px; width: 100%;">
        <div style="height: 7px; border-radius: 2px; background: var(--surface-raised); overflow: hidden;">
          <div style="height: 100%; width: ${aw}%; background: #0284c7;" title="Model Attempts: ${attempts}"></div>
        </div>
        <div style="height: 7px; border-radius: 2px; background: var(--surface-raised); overflow: hidden;">
          <div style="height: 100%; width: ${rw}%; background: #38bdf8;" title="Retained Tools: ${retained}"></div>
        </div>
      </div>
      <span class="bar-value" style="font-size: 10px;">${attempts} / ${retained}</span>
    `;
    body3.appendChild(row);
  });
}

// Render Tool Summary Table
function renderToolSummaryTable() {
  const tbody = document.getElementById('toolSummaryTableBody');
  if (!tbody) return;
  tbody.innerHTML = '';

  const guardrailProfiles = {
    'gpt-oss-120b': 'High rejection rate (30.9%) due to repeated exploratory search_documents and premature artifact inspection; 0 allowlist policy violations.',
    'gemma-4-31b-it': 'Highest tool discipline (92.2% allowed calls, only 7 rejections); zero allowlist policy violations; concise tool chains.',
    'gemma-4-26B-A4B-it': 'Moderate rejection rate (13.6%); 1 allowlist violation (called register_dataset on retail-sales-chart-csv).',
    'Qwen3.6-35B-A3B': 'Highest tool activity (209 dispatched, 185 retained); 11.4% rejection rate (chiefly finish_answer & search_documents); 2 allowlist violations (register_dataset, generate_report).'
  };

  models.forEach(m => {
    let allowed = 0, rejected = 0;
    const toolRejCounts = {};
    m.cases.forEach(c => {
      (c.tool_calls || []).forEach(tc => {
        if (tc.decision === 'rejected' || tc.status === 'rejected') {
          rejected++;
          const name = tc.name || 'unknown';
          toolRejCounts[name] = (toolRejCounts[name] || 0) + 1;
        } else {
          allowed++;
        }
      });
    });
    const totalRetained = allowed + rejected || m.retained_tool_call_count || 1;
    const allowPct = ((allowed / totalRetained) * 100).toFixed(1);
    const rejPct = ((rejected / totalRetained) * 100).toFixed(1);

    // Allowlist violations
    let allowlistViolations = 0;
    m.cases.forEach(c => {
      (c.metrics || []).forEach(met => {
        if (met.name === 'action_allowlist' && met.status === 'fail') {
          allowlistViolations++;
        }
      });
    });

    // Top rejected tool
    const topRejEntry = Object.entries(toolRejCounts).sort((a, b) => b[1] - a[1])[0];
    const topRejStr = topRejEntry ? `${topRejEntry[0]} (${topRejEntry[1]})` : 'None';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-weight: 700; font-family: var(--font-mono);">${m.model}</td>
      <td style="font-family: var(--font-mono);">${m.tool_call_count}</td>
      <td style="font-family: var(--font-mono);">${totalRetained}</td>
      <td><span class="badge-pill b-complete">${allowed} (${allowPct}%)</span></td>
      <td><span class="badge-pill ${rejected > 20 ? 'b-failed' : 'b-partial'}">${rejected} (${rejPct}%)</span></td>
      <td><span class="badge-pill ${allowlistViolations > 0 ? 'b-failed' : 'b-auto'}">${allowlistViolations} violation${allowlistViolations === 1 ? '' : 's'}</span></td>
      <td style="font-family: var(--font-mono); font-size: 11px;">${topRejStr}</td>
      <td style="color: var(--text-muted); font-size: 11px;">${guardrailProfiles[m.model] || ''}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Explorer Filters & Search Logic
let activeFilterMode = 'all'; // 'all', 'hard', 'unanimous', 'multilingual', 'artifacts'
let activeView = 'matrix'; // 'matrix' or 'list'

function initFilters() {
  // Populate category filter
  const catFilter = document.getElementById('categoryFilter');
  const cats = [...new Set(uniqueCases.map(c => c.task_type))].sort();
  cats.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    catFilter.appendChild(opt);
  });

  // Populate language filter
  const langFilter = document.getElementById('languageFilter');
  const langs = [...new Set(uniqueCases.map(c => c.language))].sort();
  langs.forEach(l => {
    const opt = document.createElement('option');
    opt.value = l;
    opt.textContent = l;
    langFilter.appendChild(opt);
  });

  // Populate model filter (for list view)
  const mFilter = document.getElementById('modelFilter');
  models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.model;
    opt.textContent = m.model;
    mFilter.appendChild(opt);
  });

  // Populate status filter (for list view)
  const sFilter = document.getElementById('statusFilter');
  ['complete', 'partial', 'failed'].forEach(s => {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
    sFilter.appendChild(opt);
  });

  // View toggle
  document.getElementById('btnViewMatrix').addEventListener('click', () => setView('matrix'));
  document.getElementById('btnViewList').addEventListener('click', () => setView('list'));

  // Quick chips
  const chips = document.querySelectorAll('#quickChips .chip-btn');
  chips.forEach(btn => {
    btn.addEventListener('click', () => {
      chips.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeFilterMode = btn.dataset.filter;
      applyFilters();
    });
  });

  // Inputs
  ['searchInput', 'categoryFilter', 'languageFilter', 'modelFilter', 'statusFilter'].forEach(id => {
    document.getElementById(id).addEventListener('input', applyFilters);
  });

  // Reset
  document.getElementById('resetBtn').addEventListener('click', () => {
    document.getElementById('searchInput').value = '';
    document.getElementById('categoryFilter').value = '';
    document.getElementById('languageFilter').value = '';
    document.getElementById('modelFilter').value = '';
    document.getElementById('statusFilter').value = '';
    chips.forEach(b => b.classList.remove('active'));
    chips[0].classList.add('active');
    activeFilterMode = 'all';
    applyFilters();
  });
}

function setView(view) {
  activeView = view;
  const btnMatrix = document.getElementById('btnViewMatrix');
  const btnList = document.getElementById('btnViewList');
  const matrixContainer = document.getElementById('matrixView');
  const listContainer = document.getElementById('listView');
  const modelFilterGroup = document.getElementById('modelFilterGroup');
  const statusFilterGroup = document.getElementById('statusFilterGroup');

  if (view === 'matrix') {
    btnMatrix.classList.add('active');
    btnList.classList.remove('active');
    matrixContainer.style.display = 'block';
    listContainer.style.display = 'none';
    modelFilterGroup.style.display = 'none';
    statusFilterGroup.style.display = 'none';
    document.getElementById('resultModeHint').textContent = 'Click any row or cell to compare all 4 model answers';
  } else {
    btnMatrix.classList.remove('active');
    btnList.classList.add('active');
    matrixContainer.style.display = 'none';
    listContainer.style.display = 'block';
    modelFilterGroup.style.display = 'flex';
    statusFilterGroup.style.display = 'flex';
    document.getElementById('resultModeHint').textContent = 'Click Compare to inspect run details side-by-side';
  }
  applyFilters();
}

function caseMatchesFilters(c, query, cat, lang) {
  if (cat && c.task_type !== cat) return false;
  if (lang && c.language !== lang) return false;

  if (query) {
    const q = query.toLowerCase();
    const matchId = c.case_id.toLowerCase().includes(q);
    const matchPrompt = c.question.toLowerCase().includes(q);
    const matchTags = (c.tags || []).some(t => t.toLowerCase().includes(q));
    const matchAnswers = Object.values(c.runsByModel).some(r => 
      (r.answer_text || '').toLowerCase().includes(q) ||
      (r.review.comment || '').toLowerCase().includes(q)
    );
    if (!matchId && !matchPrompt && !matchTags && !matchAnswers) return false;
  }

  // Quick filter chips
  if (activeFilterMode === 'hard') {
    const hasFailOrPart = Object.values(c.runsByModel).some(r => 
      r.review.overall_task === 'failed' || r.review.overall_task === 'partial'
    );
    if (!hasFailOrPart) return false;
  } else if (activeFilterMode === 'tool-rejections') {
    const hasRej = Object.values(c.runsByModel).some(r => 
      (r.tool_calls || []).some(tc => tc.decision === 'rejected' || tc.status === 'rejected')
    );
    if (!hasRej) return false;
  } else if (activeFilterMode === 'allowlist-violation') {
    const hasViolation = Object.values(c.runsByModel).some(r => 
      (r.metrics || []).some(m => m.name === 'action_allowlist' && m.status === 'fail')
    );
    if (!hasViolation) return false;
  } else if (activeFilterMode === 'unanimous') {
    const allComp = Object.values(c.runsByModel).every(r => r.review.overall_task === 'complete');
    if (!allComp) return false;
  } else if (activeFilterMode === 'multilingual') {
    if (c.language === 'en-IN') return false;
  } else if (activeFilterMode === 'artifacts') {
    if (c.task_type !== 'artifact generation' && !(c.tags || []).includes('chart') && !(c.tags || []).includes('artifact')) {
      return false;
    }
  }

  return true;
}

function applyFilters() {
  const query = document.getElementById('searchInput').value.trim();
  const cat = document.getElementById('categoryFilter').value;
  const lang = document.getElementById('languageFilter').value;

  if (activeView === 'matrix') {
    const filteredCases = uniqueCases.filter(c => caseMatchesFilters(c, query, cat, lang));
    renderMatrixRows(filteredCases);
    document.getElementById('resultCount').textContent = `Showing ${filteredCases.length} of ${uniqueCases.length} cases`;
    document.getElementById('emptyState').style.display = filteredCases.length === 0 ? 'block' : 'none';
  } else {
    const model = document.getElementById('modelFilter').value;
    const status = document.getElementById('statusFilter').value;
    const filteredRuns = allRuns.filter(r => {
      const parentCase = uniqueCaseMap.get(r.case_id);
      if (!parentCase || !caseMatchesFilters(parentCase, query, cat, lang)) return false;
      if (model && r.model !== model) return false;
      if (status && r.review.overall_task !== status) return false;
      return true;
    });
    renderListRows(filteredRuns);
    document.getElementById('resultCount').textContent = `Showing ${filteredRuns.length} of ${allRuns.length} runs`;
    document.getElementById('emptyState').style.display = filteredRuns.length === 0 ? 'block' : 'none';
  }
}

function renderMatrixRows(cases) {
  const tbody = document.getElementById('matrixTableBody');
  tbody.innerHTML = '';

  cases.forEach(c => {
    const tr = document.createElement('tr');
    tr.addEventListener('click', () => openCaseModal(c.case_id));

    // Case Info Column
    const tdCase = document.createElement('td');
    tdCase.className = 'case-cell';
    tdCase.innerHTML = `
      <div class="case-title">${c.case_id}</div>
      <div class="case-prompt" title="${c.question.replace(/"/g, '&quot;')}">${c.question}</div>
      <div class="case-tags">
        <span class="tag-badge">${c.language}</span>
        <span class="tag-badge">${c.task_type}</span>
      </div>
    `;
    tr.appendChild(tdCase);

    // 4 Model Outcome Columns
    models.forEach(m => {
      const td = document.createElement('td');
      const r = c.runsByModel[m.model];
      if (!r) {
        td.innerHTML = '<span class="badge-pill b-unresolved">—</span>';
      } else {
        const outcome = r.review.overall_task;
        const cellBox = document.createElement('div');
        cellBox.className = 'model-outcome-cell';
        cellBox.title = `Click to inspect: ${r.review.comment || ''}`;
        cellBox.innerHTML = `
          <div><span class="badge-pill b-${outcome}">${outcome}</span></div>
          <div class="cell-meta">
            <span>${fmt(r.query_seconds)}s</span>
            <span>·</span>
            <span>${fmt(r.tokens / 1000, 1)}k tok</span>
          </div>
        `;
        td.appendChild(cellBox);
      }
      tr.appendChild(td);
    });

    // Action Column
    const tdAction = document.createElement('td');
    tdAction.style.textAlign = 'right';
    const btn = document.createElement('button');
    btn.className = 'btn-inspect';
    btn.type = 'button';
    btn.textContent = 'Compare';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openCaseModal(c.case_id);
    });
    tdAction.appendChild(btn);
    tr.appendChild(tdAction);

    tbody.appendChild(tr);
  });
}

function renderListRows(runs) {
  const tbody = document.getElementById('runsTableBody');
  tbody.innerHTML = '';

  runs.forEach(r => {
    const tr = document.createElement('tr');
    tr.addEventListener('click', () => openCaseModal(r.case_id));
    tr.innerHTML = `
      <td>
        <div style="font-weight: 700; font-family: var(--font-mono);">${r.case_id}</div>
        <div style="font-size: 11px; color: var(--text-muted); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${r.question}</div>
      </td>
      <td style="font-weight: 600; font-family: var(--font-mono);">${r.model}</td>
      <td><span class="tag-badge">${r.task_type}</span></td>
      <td style="font-family: var(--font-mono);">${r.language}</td>
      <td><span class="badge-pill b-${r.review.overall_task}">${r.review.overall_task}</span></td>
      <td><span class="badge-pill b-auto">${r.auto_status}</span></td>
      <td style="font-family: var(--font-mono);">${fmt(r.query_seconds)}s</td>
      <td style="font-family: var(--font-mono);">${fmt(r.tokens, 0)}</td>
      <td style="font-family: var(--font-mono);">${r.tool_calls?.length || 0}</td>
      <td style="text-align: right;">
        <button class="btn-inspect" type="button">Compare</button>
      </td>
    `;
    const btn = tr.querySelector('.btn-inspect');
    if (btn) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        openCaseModal(r.case_id);
      });
    }
    tbody.appendChild(tr);
  });
}

// Markdown rendering
function appendInline(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*.+?\*\*|\*[^*\n]+\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  let last = 0, match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) parent.append(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith('`')) {
      node = document.createElement('code');
      node.textContent = token.slice(1, -1);
    } else if (token.startsWith('**')) {
      node = document.createElement('strong');
      node.textContent = token.slice(2, -2);
    } else if (token.startsWith('*')) {
      node = document.createElement('em');
      node.textContent = token.slice(1, -1);
    } else {
      const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(token);
      if (link) {
        node = document.createElement('a');
        node.textContent = link[1];
        node.href = link[2];
        node.target = '_blank';
        node.rel = 'noopener noreferrer';
      }
    }
    if (node) parent.append(node);
    last = pattern.lastIndex;
  }
  if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
}

function markdownTableCells(line) {
  let val = line.trim();
  if (val.startsWith('|')) val = val.slice(1);
  if (val.endsWith('|')) val = val.slice(0, -1);
  return val.split(/(?<!\\)\|/).map(x => x.replace(/\\\|/g, '|').trim());
}

function renderMarkdown(text) {
  const root = document.createElement('div');
  root.className = 'rendered-markdown';
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  let paragraph = [], i = 0;

  const flush = () => {
    if (!paragraph.length) return;
    const p = document.createElement('p');
    appendInline(p, paragraph.join(' '));
    root.append(p);
    paragraph = [];
  };

  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      if (i < lines.length) i++;
      const pre = document.createElement('pre');
      const c = document.createElement('code');
      c.textContent = code.join('\n');
      pre.append(c);
      root.append(pre);
      continue;
    }
    if (!line.trim()) {
      flush();
      i++;
      continue;
    }
    const heading = /^\s{0,3}(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length, 4);
      const h = document.createElement(level <= 2 ? 'h3' : 'h4');
      appendInline(h, heading[2].replace(/\s+#+\s*$/, ''));
      root.append(h);
      i++;
      continue;
    }
    if (i + 1 < lines.length && line.includes('|') && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      flush();
      const heads = markdownTableCells(line);
      const table = document.createElement('table');
      const thead = document.createElement('thead');
      const tr = document.createElement('tr');
      heads.forEach(x => {
        const th = document.createElement('th');
        appendInline(th, x);
        tr.append(th);
      });
      thead.append(tr);
      table.append(thead);
      i += 2;
      const tbody = document.createElement('tbody');
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const cells = markdownTableCells(lines[i]);
        const row = document.createElement('tr');
        heads.forEach((_, col) => {
          const td = document.createElement('td');
          appendInline(td, cells[col] || '');
          row.append(td);
        });
        tbody.append(row);
        i++;
      }
      table.append(tbody);
      root.append(table);
      continue;
    }
    const list = /^\s*(?:[-+*]\s+|\d+[.)]\s+)/.exec(line);
    if (list) {
      flush();
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length) {
        const m = /^\s*(?:[-+*]\s+|\d+[.)]\s+)(.*)$/.exec(lines[i]);
        if (!m) break;
        items.push(m[1]);
        i++;
      }
      const listEl = document.createElement(ordered ? 'ol' : 'ul');
      items.forEach(x => {
        const li = document.createElement('li');
        appendInline(li, x);
        listEl.append(li);
      });
      root.append(listEl);
      continue;
    }
    paragraph.push(line.trim());
    i++;
  }
  flush();
  return root;
}

function safeArtifactHref(a) {
  const p = a.cached_path || '';
  return /^\.\.\/\.\.\/runs\/real-v1-krutrim-matrix-2026-10-05\/model-[a-f0-9]+\/artifacts\/[A-Za-z0-9._-]+$/.test(p) ? p : null;
}

// Deep Comparison Modal
function openCaseModal(caseId) {
  const caseObj = uniqueCaseMap.get(caseId);
  if (!caseObj) return;

  const modal = document.getElementById('caseModal');
  document.getElementById('caseModalTitle').textContent = caseId;
  document.getElementById('caseModalSub').textContent = `${caseObj.task_type} · ${caseObj.language} · ${(caseObj.tags || []).join(' · ')}`;
  document.getElementById('modalQuestionText').textContent = (caseObj.question || '').trim();

  const grid = document.getElementById('modalAnswerGrid');
  grid.innerHTML = '';

  models.forEach(m => {
    const r = caseObj.runsByModel[m.model];
    if (!r) return;

    const panel = document.createElement('div');
    panel.className = 'answer-panel';

    const header = document.createElement('div');
    header.className = 'answer-header';
    header.innerHTML = `
      <div class="answer-model-name">${r.model}</div>
      <div class="answer-chips">
        <span class="badge-pill b-${r.review.overall_task}">${r.review.overall_task}</span>
        <span class="badge-pill b-auto">${r.auto_status}</span>
        <span class="badge-pill b-${r.run_state === 'completed' ? 'complete' : 'failed'}">${r.run_state || 'unknown'}</span>
      </div>
    `;
    panel.appendChild(header);

    const body = document.createElement('div');
    body.className = 'answer-content';

    // 1. Answer text
    body.appendChild(renderMarkdown(r.answer_text || '(No final answer recorded)'));

    // 2. Luna review commentary
    const reviewBox = document.createElement('div');
    reviewBox.className = `review-callout ${r.review.overall_task}`;
    reviewBox.innerHTML = `
      <div class="review-title">Evaluator Review · ${r.review.overall_task}</div>
      <div>${r.review.comment || 'No specific reviewer note.'}</div>
      ${(r.review.reasons && r.review.reasons.length) ? `<ul class="review-reasons">${r.review.reasons.map(x => `<li>${x}</li>`).join('')}</ul>` : ''}
    `;
    body.appendChild(reviewBox);

    // 3. Telemetry Box
    const telem = document.createElement('div');
    telem.className = 'telemetry-box';
    telem.innerHTML = `
      <div><strong>Query:</strong> ${fmt(r.query_seconds)}s | <strong>Queue:</strong> ${fmt(r.queue_seconds)}s | <strong>Ingestion:</strong> ${fmt(r.ingestion_seconds)}s</div>
      <div><strong>Tokens:</strong> ${fmt(r.tokens, 0)} (${r.usage_coverage?.usage_status || 'measured'})</div>
      <div><strong>Model Calls:</strong> ${fmt(r.usage_coverage?.attempts || r.model_calls, 0)} attempts, ${fmt(r.usage_coverage?.responses || 0, 0)} responses</div>
      <div style="word-break: break-all;"><strong>Answer Hash:</strong> ${r.answer_hash.slice(0, 24)}...</div>
    `;
    body.appendChild(telem);

    // 4. Tool Calls Trace
    const traceSec = document.createElement('div');
    traceSec.className = 'trace-section';
    traceSec.innerHTML = `<div class="trace-title">Tool Calls (${r.tool_calls?.length || 0})</div>`;
    const traceList = document.createElement('ul');
    traceList.className = 'trace-list';
    if (r.tool_calls && r.tool_calls.length) {
      r.tool_calls.forEach((t, idx) => {
        const isRejected = t.decision === 'rejected' || t.status === 'rejected';
        const li = document.createElement('li');
        li.className = isRejected ? 'is-rejected' : 'is-allowed';
        li.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
            <span class="tool-header">${idx + 1}. ${t.name || 'tool'}</span>
            <span class="badge-pill ${isRejected ? 'b-failed' : 'b-complete'}">${isRejected ? 'Rejected' : 'Allowed'}</span>
          </div>
          ${t.result?.summary ? `<div class="tool-detail">${t.result.summary}</div>` : ''}
          ${t.result?.error ? `<div class="tool-detail" style="color: var(--failed); font-weight: 500;">${t.result.error.message || 'error'}</div>` : ''}
        `;
        traceList.appendChild(li);
      });
    } else {
      traceList.innerHTML = '<li style="color: var(--text-muted);">No persisted tool execution records.</li>';
    }
    traceSec.appendChild(traceList);
    body.appendChild(traceSec);

    // 5. Artifacts
    if (r.artifacts && r.artifacts.length) {
      const artSec = document.createElement('div');
      artSec.className = 'trace-section';
      artSec.innerHTML = `<div class="trace-title">Generated Artifacts (${r.artifacts.length})</div>`;
      r.artifacts.forEach(a => {
        const href = safeArtifactHref(a);
        const artItem = document.createElement('div');
        artItem.style.fontSize = '11px';
        artItem.style.marginTop = '4px';
        if (href) {
          const link = document.createElement('a');
          link.href = href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = `${a.display_name || a.id} (${a.media_type || 'file'})`;
          artItem.appendChild(link);
          if (a.media_type === 'image/png' && a.cache_sha256_matches_expected) {
            const img = document.createElement('img');
            img.src = href;
            img.className = 'artifact-img';
            img.alt = a.display_name || a.id;
            artItem.appendChild(img);
          }
        } else {
          artItem.textContent = `${a.display_name || a.id} (${a.media_type || 'file'})`;
        }
        artSec.appendChild(artItem);
      });
      body.appendChild(artSec);
    }

    // 6. Source Report Links
    const links = document.createElement('div');
    links.className = 'links-row';
    if (r.automatic_report_ref) {
      links.innerHTML += `<a href="${r.automatic_report_ref}" target="_blank" rel="noopener noreferrer">Auto Report</a>`;
    }
    if (r.answer_review_ref) {
      links.innerHTML += ` · <a href="${r.answer_review_ref}" target="_blank" rel="noopener noreferrer">Review JSON</a>`;
    }
    if (r.model_slug) {
      links.innerHTML += ` · <a href="${r.model_slug}/reviewed-report.json" target="_blank" rel="noopener noreferrer">Full Reviewed Report</a>`;
    }
    body.appendChild(links);

    panel.appendChild(body);
    grid.appendChild(panel);
  });

  modal.showModal();
}

document.getElementById('closeCaseModal').addEventListener('click', () => {
  document.getElementById('caseModal').close();
});
document.getElementById('caseModal').addEventListener('click', (e) => {
  if (e.target === document.getElementById('caseModal')) {
    document.getElementById('caseModal').close();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const cModal = document.getElementById('caseModal');
    if (cModal && cModal.open) cModal.close();
    const sModal = document.getElementById('scopeModal');
    if (sModal && sModal.open) sModal.close();
  }
});

// Initialization
initScopeModal();
renderModelCards();
renderAnalyticsCharts();
renderSummaryTable();
renderToolAnalytics();
renderToolSummaryTable();
initFilters();
applyFilters();
</script>
</body>
</html>"""


def main():
    data = json.loads((OUT / "comparison.json").read_text(encoding="utf-8"))
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    serialized = (
        serialized.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    (OUT / "index.html").write_text(
        HTML.replace("__DATA__", serialized), encoding="utf-8"
    )
    print(f"Wrote {OUT / 'index.html'} ({(OUT / 'index.html').stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

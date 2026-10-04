# UI and UX Modernization Plan

This plan translates the comparison between Nexus (`apps/web`) and the current analyst application (`frontend`) into an actionable architectural and design overhaul. It addresses critical friction points where the current user experience is clunky, rigid, and disruptive.

## Executive Summary

The current application requires excessive ceremony before users can ask questions, buries analysis under redundant sidebars, disconnects file ingestion and outputs from conversation flow, and breaks reading continuity during streaming.

This modernization plan transitions the interface to a cohesive, conversation-centric workspace modeled on Nexus's layout patterns, while maintaining Agentic Analyst's distinct bilingual, evidence-backed verification strengths.

## Core UX Problems and Objectives

- **Frictionless Onboarding:** Eliminate the mandatory "Create Workspace -> Create Thread" gate. Provide a default workspace and an immediate empty-state composer that provisions a thread upon the first message send.
- **Unified Intake and Scope:** Move document upload and attachment directly into the composer. Replace the intrusive accordion checklist with contextual attachment pills and an `@` reference menu.
- **Dedicated Spatial Layout:** Replace the crowded, persistent 3-column split with a collapsible navigation sidebar, an expansive central chat canvas, and a contextual right inspector that opens on demand for evidence snippets, tool execution traces, or generated artifacts.
- **Stable Reading and Scrolling:** Decouple streaming updates from disruptive `scrollIntoView` jumps. Maintain user scroll position when reading historical content and offer an intuitive "Jump to latest" control.
- **First-Class Artifact and Evidence Inspection:** Display reports, interactive data tables, and visualizations first. Shift raw SHA-256 hashes, database IDs, and JSON traces to secondary tabs or technical detail accordions.
- **Addressable Deep Routing:** Integrate `react-router-dom` to support direct URLs for conversations, runs, and artifacts, preserving context across browser reloads.

## Improvement Matrix

| Priority | Area | Current Limitation | Comparison with Nexus | Planned Enhancement |
| - | - | - | - | - |
| P0 | Session Initialization | Mandatory workspace selection followed by thread creation dialog before asking questions. | Nexus provides a ready composer on the home route, provisioning the thread on submit. | Provide a default workspace; activate composer immediately; auto-create thread on first send; allow inline renaming. |
| P0 | Document Attachment | Ingestion requires switching to workbench or using inspector forms; text/audio only in composer. | Nexus supports drag-and-drop and attachment chips directly in the composer. | Add drag-and-drop and file attachment inside composer with inline upload and processing status chips. |
| P0 | Source Scope Management | Fragmented across inspector list, workbench list, and an intrusive composer accordion checklist. | Nexus keeps file and scope selection close to composer without dominating layout. | Separate library management (dedicated view) from chat analysis scope (composer chips and `@` mentions) and inspector preview. |
| P0 | Screen Space Allocation | Sidebars reserve 280px left and 360px right by default, squeezing the chat canvas. | Nexus right panel opens conditionally for outputs and thread files, preserving chat width. | Auto-collapse inspector on default view; open dynamically on evidence or artifact selection; enable fluid desktop resizing. |
| P0 | Streaming Scroll Behavior | Continuous `scrollIntoView` forces window down on every token, progress step, and message chunk. | Nexus pins scroll to bottom only when user is already at bottom; preserves reading position otherwise. | Implement smart auto-scroll with threshold detection; preserve position on manual scroll-up; show "Jump to latest" pill. |
| P1 | Composer Machinery | Retrieval mode, language, voice, and offline capability notices permanently occupy composer surface. | Nexus nests secondary controls in inline popovers or minimalist footer icons. | Keep textarea, attachment chips, and send button primary; tuck retrieval and language choices into a compact popover. |
| P1 | Source Intake Flow | Ingestion forms, chunking settings, and crawler forms dominate workbench catalog. | Nexus offers dedicated document and connection tabs with clean catalog tables. | Default to browsing existing sources; provide unified "Add source" modal with tabs (Files, Database, Web); hide chunking in advanced accordion. |
| P1 | Artifact Presentation | Output previews lead with run IDs, SHA-256 hashes, and raw lineage JSON before content. | Nexus displays rendered markdown, interactive tables, and charts first with preview/raw toggle. | Display rendered charts, tables, and reports front-and-center; tuck technical provenance and hashes into collapsible tabs. |
| P1 | Artifact Context | Viewing outputs requires navigating away from chat into the workspace-wide browser. | Nexus displays generated outputs and thread files in a panel alongside active chat. | Open outputs in the right contextual panel beside active chat; keep outputs view for cross-workspace browsing. |
| P1 | Navigation and Persistence | All state (active view, IDs, selection) stored in component memory; reloads reset view. | Nexus uses declarative routes (`/threads/:id`, `/documents`, `/artifacts/:id`). | Introduce React Router; map workspace, thread, and artifact views to addressable URLs; support browser history. |
| P1 | Actionable Empty States | Empty screens show informational cards asking users to create threads without suggestions. | Nexus displays prompt suggestions that prefill composer based on workspace data. | Suggest contextual starter prompts based on ingested files (e.g. "Summarize document", "Compare sheets", "Profile data"). |
| P2 | Typography and Visual Density | Redundant headers (top breadcrumb + thread header), tiny 10-12px labels, excessive borders. | Nexus uses clean hierarchy and subtle border treatments with high contrast. | Unify into single conversation header; enlarge body text to 14-15px; remove superfluous uppercase labels and heavy borders. |
| P2 | Accessibility and Quick Actions | Thread rename/delete hidden behind hover-only icons; no message copy button. | Nexus provides quick-action bars (copy, branch, retry) and keyboard navigation. | Add answer copy button; enable keyboard focus visibility on action buttons; provide keyboard shortcuts (`Cmd+K`, `Esc`). |

## Architectural and Component Redesign

### Routing and Layout Hierarchy

Replace state-based view switching with a structured route hierarchy in `frontend/src/App.tsx`:

```text
/                                   -> Home (default workspace, ready composer)
/workspaces/:workspaceId            -> Workspace root (redirects to latest thread or home composer)
/workspaces/:workspaceId/threads/:threadId -> Active chat thread
/workspaces/:workspaceId/sources    -> Dedicated Source and Dataset Catalog
/workspaces/:workspaceId/outputs    -> Workspace-wide Output Gallery
```

The shell architecture adopts a three-zone structure:
- **Left Navigation (`LeftSidebar.tsx`):** Workspace switcher, search bar, new thread button, thread history list with keyboard-accessible action menus, and links to Sources and Outputs. Collapsible to a slim 52px icon bar.
- **Center Canvas:** The primary working area. Houses either the active chat thread (`ChatPanel.tsx`), the source catalog (`SourceCatalog.tsx`), or cross-workspace outputs.
- **Contextual Right Panel (`ContextInspector.tsx`):** A shared drawer that remains closed by default. It opens automatically when the user clicks:
  - An evidence citation (displaying source excerpts, verified locations, and calculation records).
  - An artifact link (displaying rich table, chart, or code previews alongside conversation).
  - A live execution step (displaying detailed tool inputs, stdout, and execution duration).

### Composer Restructuring

Refactor `frontend/src/ChatPanel.tsx`'s composer into modular subcomponents:
- `ComposerInput.tsx`: Auto-resizing textarea supporting drag-and-drop files and `Enter` / `Shift+Enter` handling.
- `ComposerAttachments.tsx`: Horizontal chip list above the input showing pending files, upload progress bars, and remove buttons.
- `ComposerScopeTag.tsx`: Compact pill showing active source scope (e.g., "All Sources", "3 Sources Selected") with a popover checklist instead of a permanent accordion.
- `ComposerFooter.tsx`: Minimalist bottom bar containing:
  - Attachment trigger button (paperclip icon).
  - Scope / `@` mention menu.
  - Model and retrieval settings popover (combining retrieval profile, Hindi/English target language, and voice STT).
  - Send / Stop button.

### Smart Scroll Engine

Replace unconstrained `scrollIntoView` calls with a scroll controller:
- Track scroll container scroll position via `onScroll`.
- If `scrollHeight - scrollTop - clientHeight < 80px`, pin to bottom as new tokens or progress events arrive.
- If user has manually scrolled up, freeze position and display a floating "Jump to latest" badge with an unread indicator.
- Automatically snap to bottom when the user submits a new prompt.

### First-Class Viewers for Artifacts and Evidence

Replace handwritten SVG chart previewing and unformatted table outputs:
- **Tabular Data Viewer:** Implement virtualized grid rendering with column sorting, cell search, row counts, and TSV/CSV export.
- **Code Viewer:** Syntax-highlighted code viewer with line numbers, language badge, and copy button.
- **Chart Viewer:** Structured chart renderer mapping Plotly/Vega specs to accessible SVG/Canvas visualizations.
- **Provenance Accordion:** Move SHA-256 hashes, run IDs, and raw execution metadata into a bottom expandable panel labeled "Technical Lineage", prioritizing the visual output.

## Detailed Feature Specifications

### 1. Zero-Friction Thread Creation

- On application launch, if no workspace exists, automatically create or select "Default Workspace".
- The root view displays the composer immediately with starter prompt chips.
- Typing a question and pressing Send immediately creates the thread in the background, appends the user turn, updates the browser URL to `/workspaces/:workspaceId/threads/:newThreadId`, and streams the assistant response without page reload.
- The thread title is automatically generated from the first prompt and can be edited inline.

### 2. Composer File Drag and Drop

- Dragging any supported file (PDF, DOCX, CSV, XLSX, TXT) over the chat area triggers a drop zone overlay.
- Dropping files immediately initiates upload and source registration.
- A progress pill appears inside `ComposerAttachments` displaying file name, size, and ingestion state (`Uploading -> Processing -> Ready`).
- Prompts submitted while files are processing automatically wait for ingestion completion or scope the run appropriately.

### 3. Contextual Dual-Pane Inspection

- When reading an answer with citations `[1]`, clicking a citation does not jump away from the message. Instead, it smoothly opens the `ContextInspector` on the right side.
- Clicking an artifact chip in the answer displays the full interactive table or chart in the `ContextInspector` while the conversation stays interactive on the left.
- Closing the inspector (`Esc` or close button) returns full width to the chat canvas.

### 4. Consolidated Source Intake

- Remove individual upload forms scattered across `SourceWorkbench.tsx`.
- Introduce a single "Add Source" modal with three tabs:
  - **Upload Files:** Drag-and-drop for documents and spreadsheets with automatic format detection.
  - **Connect Database:** Clean form for PostgreSQL/MySQL connection parameters with latency test.
  - **Web Ingestion:** URL input with crawl depth and page limits tucked into an "Advanced settings" disclosure.

## Implementation Sequence

### Milestone 1: Routing and Layout Shell

- Install and configure `react-router-dom`.
- Refactor `frontend/src/App.tsx` into declarative route shells.
- Redesign `LeftSidebar.tsx` with unified workspace and thread tree.
- Create `ContextInspector.tsx` for on-demand evidence and artifact viewing.

### Milestone 2: Composer and Chat Experience

- Extract composer into dedicated component tree with auto-resizing textarea.
- Implement composer file drop zone and upload attachment chips.
- Replace permanent source checklist accordion with a compact popover selector.
- Implement the smart auto-scroll hook with manual scroll freeze and "Jump to latest" button.

### Milestone 3: Observability and Artifact Viewers

- Replace inline message card audit trail with an expandable trace panel inside `ContextInspector`.
- Upgrade artifact display to prioritize rendered output over raw technical hashes.
- Add message copy buttons and accessible keyboard shortcuts (`Cmd+K`, `Esc`).

### Milestone 4: Source Catalog and Intake Consolidation

- Refactor `SourceWorkbench.tsx` into a clean catalog browser with a unified "Add Source" dialog.
- Relocate chunking strategy and crawl depth options to advanced collapsible disclosures.
- Remove redundant source listings from sidebars.

## Verification and Acceptance Criteria

- **Zero-Friction Send:** A fresh browser session loads directly to an active composer; sending a query automatically provisions workspace and thread with updated URL within 200ms.
- **In-Composer Attachments:** Dropping a file onto the composer initiates upload and displays ingestion progress chip without page switching.
- **Reading Continuity:** Scrolling up during active assistant streaming stops automatic view jumping; clicking "Jump to latest" smoothly returns to bottom.
- **Deep Linking:** Reloading a thread URL (`/workspaces/:ws/threads/:id`) preserves exact conversation history and open inspector state.
- **Artifact Preview:** Clicking an artifact opens a rich preview in the right drawer alongside the active conversation without unmounting chat.
- **Accessibility:** All thread actions (rename, delete) are navigable via Tab/Enter keys; all icon buttons include tooltips.

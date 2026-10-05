# Artifact visibility

The right sidebar shows final deliverables from the active chat by default.
Its counts, search and **Show intermediate files** toggle stay within that chat.
Switching chats clears artifact selection and filters; late fetches cannot replace
another chat's list. With no active chat, the sidebar has no artifacts.

The Outputs page lists final deliverables across the workspace. Turn on **Show
intermediate files** there to inspect all retained workspace files. Search applies within the
chosen view, and its counts reflect that view. Filtering does not delete files
or change downloads, table inspection, provenance, or chat references.

## Roles and deliverables

The backend assigns artifact roles at creation:

| Role | Assigned to |
| --- | --- |
| `execution_code` | Exact executed Python and SQL |
| `input_snapshot` | Canonical datasets staged into the sandbox |
| `metadata` | Internal file-SQL query metadata |
| `intermediate` | Collected analysis results and generated reports before final selection |
| `output` | Final deliverables selected in an accepted answer |

`finish_answer.output_artifact_ids` selects deliverables separately from
`artifact_ids`, which also includes supporting references. Each output must be
in `artifact_ids`, be durable, and pass existing thread, source-selection, and
source-version validation. Execution code, input snapshots, and metadata cannot
be promoted. Short `artifact_1` references resolve through the existing model
reference adapter.

Promotion happens in the transaction that persists the answer. Failed answer
persistence rolls it back. Unselected intermediate files, including salvaged
files from failed or cancelled runs, remain inspectable through the toggle.
Previously selected outputs remain outputs when referenced in follow-up answers.
An empty deliverable list is valid when the answer needs no file.

Roles are included in manifest APIs and preserved by workspace export/import.
Migration `b19d4a2e730f` adds the role column and allowed-value constraint. It does
not classify historical data: existing rows receive `intermediate` and remain
available through the toggle. No files are deleted.

The agent prompt now asks for final deliverable selection. Deterministic checks
validate classification, promotion, reference boundaries, and persistence; live
model compliance with selecting the best deliverables remains unmeasured.

The workspace manifest endpoint accepts an optional `thread_id`. When provided,
it verifies that the chat belongs to the workspace and filters all runs of that
chat before ordering and applying the 500-item listing limit. Omitting the filter
keeps the workspace-wide listing used by the Outputs page. Sources remain shared
at workspace level.

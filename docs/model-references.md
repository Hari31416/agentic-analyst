# Short references for model tools and citations

The agent uses conversation-stable references such as `source_1`, `dataset_1`,
`artifact_1`, `chunk_1` and `e1`. Database keys, public APIs, evidence records,
source versions and artifact provenance retain their full UUIDs.

The model receives short references in tool schemas, resource descriptors, thread
context metadata and tool results. Human-readable filenames, sheet names,
locations and excerpts remain alongside them. Source excerpts, rows, sample
values, SQL literals and Python programs are not rewritten.

Tool arguments resolve to UUIDs before Pydantic validation and the existing
selection, thread and source-version checks. Unknown and wrong-type references
produce bounded repair diagnostics. A reference mapping grants no access by
itself. UUID arguments remain supported for compatibility.

## SQL and Python

File SQL uses actual registered DuckDB table names such as `dataset_1`.
Database SQL retains its original schema/table identifiers. The application
registers the selected tables directly; it does not substitute identifiers in
model SQL text. Standalone legacy probes retain their `data_UUID` aliases.

Python can read selected datasets at `/workspace/inputs/dataset_1.csv` and imported
artifacts at `/workspace/inputs/artifact_1`. The guest also retains the canonical
UUID paths for existing application-generated programs. Both paths contain the
same working copy, and source originals remain read-only. Retained code is exact.
Replay notebook input metadata records canonical paths and short guest aliases.

## Citations and persistence

The model cites a specific retained evidence record as `[e1]` and includes `e1`
in `evidence_ids`. The application resolves this to `[evidence:UUID]` before
existing answer validation and storage. The frontend continues to show numbered,
clickable citations. Artifact links use `[label](artifact:artifact_1)` or
`![caption](artifact:artifact_1)` and resolve to the existing UUID destinations.
Inline references still require declarations, and stale source versions or
unselected resources still fail validation.

Each run checkpoints its mapping in `config.reference_aliases` before a model
request and after tool results. Later runs in that thread restore the union of
retained maps, including failed runs, without renumbering or reusing references.
Completed messages also retain the mapping with their canonical references.
Workspace archive export/import preserves aliases and remaps their UUID values.
No database migration is needed for this reference change.

These checks establish reference identity and access. They do not prove that a
cited passage supports a claim or that a valid dataset is the intended dataset.
No live reliability improvement is claimed. Invalid references and wrong-resource
selections should remain separate evaluation metrics.

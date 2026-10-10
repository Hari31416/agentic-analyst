# Saved pins

Pins are personal to the signed-in user and grouped by workspace. Use **Pin** on a
thread in the sidebar, on a persisted chat message, or on a generated artifact.
Pinned threads sort above other threads. Open **Saved pins** in the sidebar to
search, filter by type, inspect, edit, or remove saved items.

Each pin has a title, optional notes, and optional comma-separated tags. Editing a
pin changes its metadata. Removing a pin leaves the original content available.

A message pin shows the question and answer from the same run, when the message
has a run. A thread pin opens the full conversation. An artifact pin opens only
the selected generated file in the existing artifact viewer, with its preview,
download, and lineage controls. Artifact pins do not require pinning a message
or its thread.

Pins reference live resources. They do not copy messages or artifact bytes.
Deleting a target, its thread, or its workspace removes the associated pins.
An artifact whose bytes are unavailable reports that state in the viewer; its
pin can still be removed. Pins are unique per user, target type, and target ID.

## API

All routes require authentication. A user cannot read, update, or remove another
user's pins. Workspace access follows the app's existing shared-workspace model.

| Method | Route | Action |
| --- | --- | --- |
| GET | `/api/workspaces/{workspace_id}/pins` | List the current user's pins. |
| POST | `/api/workspaces/{workspace_id}/pins` | Create a pin. |
| GET | `/api/pins/{pin_id}` | Read metadata and linked message or artifact details. |
| PATCH | `/api/pins/{pin_id}` | Edit title, notes, or tags. |
| DELETE | `/api/pins/{pin_id}` | Remove a pin. |

Creation accepts `kind` as `thread`, `message`, or `artifact`, a `target_id` UUID,
a required `title`, and optional `notes` and `tags`. Updates accept any subset of
`title`, `notes`, and `tags`. Explicit nulls and unknown fields are rejected.

Titles are trimmed and must contain 1 to 200 characters. Notes allow 4,000
characters. Up to 30 tags are accepted, each at most 80 characters; tags are
trimmed, deduplicated, and empty tags removed. Listing supports optional `kind`
and `target_id` filters, `limit` from 1 to 200, and `offset`. Results sort by
creation time descending with a stable ID tie-breaker.

## Setup and verification

Apply migration `e42c851ab901` with `make migrate` before starting the updated API.
No worker or model call is needed to create or manage pins.

Run the focused backend tests with:

```sh
cd backend
uv run pytest tests/test_pins.py tests/test_auth.py tests/test_resource_lifecycle.py tests/test_artifact_thread_scope.py
```

`pnpm --dir frontend test:artifacts` includes pin lifecycle checks for creation,
edits, removal, stale refresh responses, pagination, artifact filtering, search,
and routing.

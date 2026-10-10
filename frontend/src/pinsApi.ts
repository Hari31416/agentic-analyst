import { apiFetch } from './lib/apiFetch'
import type { ArtifactManifest } from './phase06Api'

export type PinKind = 'thread' | 'message' | 'artifact'
export type PinFields = { title: string; notes: string; tags: string[] }
export type SavedPin = PinFields & {
  id: string
  kind: PinKind
  target_id: string
  workspace_id: string
  thread_id: string
  thread_label: string
  created_at: string
  updated_at: string
}
export type PinDetail = SavedPin & {
  artifact?: ArtifactManifest
  messages?: {
    id: string
    role: string
    content: string
    references: { artifact_ids?: string[]; evidence_ids?: string[] }
  }[]
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown
    }
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `Pin request failed (${response.status})`,
    )
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>)
}
export const pinsApi = {
  list: (workspaceId: string, offset = 0, signal?: AbortSignal) =>
    request<SavedPin[]>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/pins?limit=200&offset=${offset}`,
      { signal },
    ),
  create: (
    workspaceId: string,
    kind: PinKind,
    targetId: string,
    fields: PinFields,
  ) =>
    request<SavedPin>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/pins`,
      {
        method: 'POST',
        body: JSON.stringify({ kind, target_id: targetId, ...fields }),
      },
    ),
  read: (id: string) =>
    request<PinDetail>(`/api/pins/${encodeURIComponent(id)}`),
  update: (id: string, fields: PinFields) =>
    request<SavedPin>(`/api/pins/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
  remove: (id: string) =>
    request<void>(`/api/pins/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

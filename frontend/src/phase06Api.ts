export type ArtifactManifest = {
  id: string
  display_name: string
  media_type: string
  byte_size: number
  sha256: string
  run_id: string
  lineage: Record<string, unknown> | unknown[]
  durable: boolean
  artifact_type: string
  metadata: Record<string, unknown>
}

export type ArtifactRows = {
  artifact_id: string
  columns: { name: string; type: string }[]
  rows: Record<string, unknown>[]
  offset: number
  limit: number
  total_rows: number
  truncated: boolean
}

export type PlotlySpec = {
  data: Record<string, unknown>[]
  layout?: Record<string, unknown>
  config?: Record<string, unknown>
}

export type WorkspaceImportResult = {
  workspace: { id: string; label: string }
  id_map: Record<string, string>
  reconnection_required: {
    source_id: string
    display_name: string
    dialect: string
    host: string
    port: number
    database_name: string
    username: string
  }[]
  reindex_required: string[]
}

export type RegisteredArtifactDataset = {
  source_id: string
  dataset_ids: string[]
  display_name: string
  reused: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const detail = body.detail
        if (typeof detail === 'string') message = detail
      }
    } catch {
      // Keep the HTTP status when the response is not JSON.
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

export const phase06Api = {
  artifacts: (workspaceId: string) =>
    request<ArtifactManifest[]>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/artifacts`,
    ),
  artifact: (artifactId: string) =>
    request<ArtifactManifest>(
      `/api/artifacts/${encodeURIComponent(artifactId)}`,
    ),
  rows: (artifactId: string, offset: number, limit = 50) =>
    request<ArtifactRows>(
      `/api/artifacts/${encodeURIComponent(artifactId)}/rows?offset=${offset}&limit=${limit}`,
    ),
  chart: (artifactId: string) =>
    request<PlotlySpec>(
      `/api/artifacts/${encodeURIComponent(artifactId)}/chart`,
    ),
  registerDataset: (artifactId: string) =>
    request<RegisteredArtifactDataset>(
      `/api/artifacts/${encodeURIComponent(artifactId)}/dataset`,
      { method: 'POST' },
    ),
  previewUrl: (artifactId: string) =>
    `/api/artifacts/${encodeURIComponent(artifactId)}/content`,
  downloadUrl: (artifactId: string, format = 'original') =>
    `/api/artifacts/${encodeURIComponent(artifactId)}/download?format=${encodeURIComponent(format)}`,
  exportWorkspace: (workspaceId: string) =>
    `/api/workspaces/${encodeURIComponent(workspaceId)}/export`,
  importWorkspace: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<WorkspaceImportResult>('/api/portability/import', {
      method: 'POST',
      body,
    })
  },
}
import { apiFetch } from './lib/apiFetch'

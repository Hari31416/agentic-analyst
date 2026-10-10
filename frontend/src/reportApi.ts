import { apiFetch } from './lib/apiFetch'

export type ReportLanguage = { language: 'en-IN' | 'hi-IN'; label: string }
export type ReportBlock =
  | { id: string; type: 'paragraph'; text: string }
  | {
      id: string
      type: 'figure'
      artifact_id: string
      caption: string
    }
  | {
      id: string
      type: 'table'
      artifact_id: string
      caption: string
      columns: string[]
      max_rows: number
    }
export type ReportSection = {
  id: string
  heading: string
  blocks: ReportBlock[]
}
export type ReportDocument = {
  title: string
  language: string
  sections: ReportSection[]
}
export type ReportVersion = {
  id: string
  number: number
  state: 'queued' | 'generating' | 'ready' | 'failed'
  language: string
  created_at: string
  error: string | null
  document: ReportDocument | null
  feedback: string
  mode: string
  assets?: Record<
    string,
    {
      display_name: string
      media_type: string
      byte_size: number
      sha256: string
    }
  >
}
export type ReportTable = {
  columns: string[]
  rows: unknown[][]
  truncated: boolean
}
export type Report = {
  id: string
  workspace_id: string
  title: string
  created_at: string
  updated_at: string
  latest_version?: ReportVersion
  versions?: ReportVersion[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // Keep the HTTP status when the response is not JSON.
    }
    throw new Error(message)
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>)
}

export const reportApi = {
  languages: (signal?: AbortSignal) =>
    request<ReportLanguage[]>('/api/report-languages', { signal }),
  list: async (workspaceId: string, signal?: AbortSignal) => {
    const all: Report[] = []
    let page: Report[]
    do {
      page = await request<Report[]>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/reports?limit=100&offset=${all.length}`,
        { signal },
      )
      all.push(...page)
    } while (page.length === 100 && !signal?.aborted)
    return all
  },
  read: (id: string, signal?: AbortSignal) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}`, { signal }),
  create: (
    workspaceId: string,
    input: {
      title?: string | null
      language: string
      pin_ids: string[]
      instructions: string
    },
  ) =>
    request<Report>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/reports`,
      { method: 'POST', body: JSON.stringify(input) },
    ),
  rename: (id: string, title: string) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  remove: (id: string) =>
    request<void>(`/api/reports/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  regenerate: (
    id: string,
    input: {
      version_id: string
      feedback: string
      mode: 'wording' | 'restructure'
    },
  ) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  download: async (id: string, versionId: string) => {
    const response = await apiFetch(
      `/api/reports/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}/download`,
    )
    if (!response.ok) throw new Error(`Download failed (${response.status})`)
    return response.blob()
  },
  document: async (id: string, versionId: string) => {
    const response = await apiFetch(
      `/api/reports/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}/document`,
    )
    if (!response.ok)
      throw new Error(`Document download failed (${response.status})`)
    return response.blob()
  },
  table: async (
    id: string,
    versionId: string,
    artifactId: string,
    columns: string[],
    maxRows: number,
    signal?: AbortSignal,
  ) =>
    request<ReportTable>(
      `/api/reports/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}/assets/${encodeURIComponent(artifactId)}/table?max_rows=${Math.min(30, maxRows)}${columns.map((column) => `&columns=${encodeURIComponent(column)}`).join('')}`,
      { signal },
    ),
}

export function reportAssetUrl(
  reportId: string,
  versionId: string,
  artifactId: string,
) {
  return `/api/reports/${encodeURIComponent(reportId)}/versions/${encodeURIComponent(versionId)}/assets/${encodeURIComponent(artifactId)}`
}

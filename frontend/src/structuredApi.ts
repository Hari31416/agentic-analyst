export type DatasetSummary = {
  id: string
  source_id: string
  source_version: number
  identity: string | Record<string, unknown>
  schema_version: string
  designation: string
  details: Record<string, unknown>
}

export type DatasetColumn = {
  name: string
  position: number
  type: string
  duckdb_type?: string
  nullable: boolean
  missing_count?: number
  examples?: unknown[]
  hints?: string[]
}

export type DatasetProfile = DatasetSummary & {
  details: {
    sheet_name?: string
    row_count?: number | null
    row_count_exact?: boolean
    encoding?: string
    delimiter?: string
    columns: DatasetColumn[]
    sample?: Record<string, unknown>[]
    warnings?: string[]
    [key: string]: unknown
  }
}

export type DatasetRows = {
  dataset_id: string
  offset: number
  limit: number
  total_rows: number
  rows: Record<string, unknown>[]
  truncated: boolean
}

export type SourceView = {
  id: string
  display_name: string
  kind: string
  state: string
  version: number
  schema_version?: string | null
  content_hash?: string | null
  description?: string | null
  metric_hints?: Record<string, string>
  details?: Record<string, unknown>
  datasets?: DatasetSummary[]
}

export type ConnectionDraft = {
  dialect: 'mysql' | 'postgresql'
  host: string
  port: number
  database_name: string
  username: string
  password: string
  options: {
    ssl_mode: 'disable' | 'prefer' | 'require' | 'verify-ca' | 'verify-full'
  }
}

export type ConnectionTestResult = {
  ok: boolean
  status: string
  message: string
  latency_ms?: number
}

export type SourceSchema = {
  source_id: string
  source_version: number
  schema_version: string | null
  datasets: DatasetSummary[]
}

export type SourceRefreshResult = {
  source: SourceView
  datasets: DatasetSummary[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const detail = body.detail
        if (typeof detail === 'string') message = detail
        else if (
          detail &&
          typeof detail === 'object' &&
          'message' in detail &&
          typeof detail.message === 'string'
        ) {
          message = detail.message
        }
      }
    } catch {
      // Keep the HTTP status when an API response is not JSON.
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

export const structuredApi = {
  updateSourceMetadata: (
    workspaceId: string,
    sourceId: string,
    metadata: {
      description: string | null
      metric_hints: Record<string, string>
    },
  ) =>
    request<SourceView>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/sources/${encodeURIComponent(sourceId)}`,
      { method: 'PATCH', body: JSON.stringify(metadata) },
    ),
  uploadFile: (workspaceId: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<SourceView>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/sources/files`,
      { method: 'POST', body },
    )
  },
  datasets: (sourceId: string) =>
    request<DatasetSummary[]>(
      `/api/sources/${encodeURIComponent(sourceId)}/datasets`,
    ),
  profile: (datasetId: string) =>
    request<DatasetProfile>(
      `/api/datasets/${encodeURIComponent(datasetId)}/profile`,
    ),
  rows: (datasetId: string, offset: number, limit = 50) =>
    request<DatasetRows>(
      `/api/datasets/${encodeURIComponent(datasetId)}/rows?offset=${offset}&limit=${limit}`,
    ),
  testConnection: (workspaceId: string, connection: ConnectionDraft) =>
    request<ConnectionTestResult>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/connections/test`,
      { method: 'POST', body: JSON.stringify(connection) },
    ),
  saveConnection: (
    workspaceId: string,
    connection: ConnectionDraft & { display_name: string },
  ) =>
    request<{
      source: SourceView
      connection: Record<string, unknown>
      datasets: DatasetSummary[]
    }>(`/api/workspaces/${encodeURIComponent(workspaceId)}/connections`, {
      method: 'POST',
      body: JSON.stringify(connection),
    }),
  schema: (sourceId: string) =>
    request<SourceSchema>(
      `/api/sources/${encodeURIComponent(sourceId)}/schema`,
    ),
  refreshSchema: (sourceId: string) =>
    request<SourceRefreshResult>(
      `/api/sources/${encodeURIComponent(sourceId)}/refresh`,
      { method: 'POST' },
    ),
}

export type DocumentView = {
  id: string
  source_id: string
  source_version: number
  display_name: string
  kind?: string
  state: string
  stage?: string | null
  progress?: number | null
  extractor_version?: string | null
  chunker_version?: string | null
  index_generation_id?: string | null
  details: Record<string, unknown>
}

export type DocumentBlock = {
  id: string
  ordinal: number
  kind: string
  text: string
  heading?: string | null
  location: Record<string, unknown>
  language?: string | null
  scripts?: string[]
}

export type DocumentBlocks = {
  document_id: string
  offset: number
  limit: number
  total: number
  blocks: DocumentBlock[]
}

export type DocumentTableCell = {
  value: unknown
  type?: string | null
  provenance?: Record<string, unknown>
}

export type DocumentTableCandidate = {
  table_id: string
  title?: string | null
  page?: number | null
  row_count: number
  column_count: number
  columns: { name: string; type?: string | null }[]
  preview: { row_index: number; cells: DocumentTableCell[] }[]
  warnings: string[]
  accepted_dataset_id?: string | null
}

export type DocumentTables = {
  document_id: string
  state: string
  tables: DocumentTableCandidate[]
}

export type EvidenceView = {
  id: string
  kind?: string
  source_state?: 'active' | 'archived' | string
  source_ids: string[]
  details: Record<string, unknown>
  document_id?: string | null
  document_version?: number | null
  display_name?: string | null
  excerpt?: string | null
  location?: Record<string, unknown> | null
  context?: string | null
  score?: number | null
  rank?: number | null
  retrieval_mode?: string | null
  trace?: unknown
}

export type DocumentUpload = {
  source: {
    id: string
    display_name: string
    kind: string
    state: string
    version: number
  }
  document: DocumentView
}

export type IngestionCapabilities = {
  crawl: { enabled: boolean; approved_hosts: string[] }
  chunk_strategies: string[]
  ocr: { enabled: boolean; languages: string[]; profile: string }
}

export type IngestionJob = {
  id: string
  state: string
  attempts: number
  result?: {
    pages?: {
      url: string
      state: string
      message?: string
      document_id?: string
    }[]
    document_ids?: string[]
    partial?: boolean
  } | null
}

function uploadWithProgress<T>(
  path: string,
  body: FormData,
  onProgress?: (progress: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', path)
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress?.(Math.round((event.loaded / event.total) * 100))
      }
    }
    request.onerror = () => reject(new Error('The upload connection failed.'))
    request.onload = () => {
      let payload: unknown
      try {
        payload = request.responseText ? JSON.parse(request.responseText) : {}
      } catch {
        payload = {}
      }
      if (request.status < 200 || request.status >= 300) {
        const detail =
          payload && typeof payload === 'object' && 'detail' in payload
            ? (payload as { detail: unknown }).detail
            : undefined
        const message =
          typeof detail === 'string'
            ? detail
            : detail &&
                typeof detail === 'object' &&
                'message' in detail &&
                typeof (detail as { message: unknown }).message === 'string'
              ? (detail as { message: string }).message
              : `Upload failed (${request.status})`
        reject(new Error(message))
        return
      }
      resolve(payload as T)
    }
    request.send(body)
  })
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
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
      // Keep the HTTP status when the API response is not JSON.
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

export const documentApi = {
  upload: (
    workspaceId: string,
    file: File,
    onProgress?: (progress: number) => void,
    chunkStrategy = 'structure',
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('chunk_strategy', chunkStrategy)
    return uploadWithProgress<DocumentUpload>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/documents`,
      body,
      onProgress,
    )
  },
  list: (workspaceId: string) =>
    request<DocumentView[]>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/documents`,
    ),
  blocks: (documentId: string, offset = 0, limit = 100) =>
    request<DocumentBlocks>(
      `/api/documents/${encodeURIComponent(documentId)}/blocks?offset=${offset}&limit=${limit}`,
    ),
  tables: (documentId: string) =>
    request<DocumentTables>(
      `/api/documents/${encodeURIComponent(documentId)}/tables`,
    ),
  acceptTable: (documentId: string, tableId: string) =>
    request<{ document_id: string; table_id: string; dataset_id: string }>(
      `/api/documents/${encodeURIComponent(documentId)}/tables/${encodeURIComponent(tableId)}/accept`,
      { method: 'POST' },
    ),
  retry: (documentId: string) =>
    request<{ document_id: string; state: string }>(
      `/api/documents/${encodeURIComponent(documentId)}/retry`,
      { method: 'POST' },
    ),
  reindex: (documentId: string) =>
    request<{ document_id: string; state: string }>(
      `/api/documents/${encodeURIComponent(documentId)}/reindex`,
      { method: 'POST' },
    ),
  archive: (documentId: string) =>
    request<{ document_id: string; state: string }>(
      `/api/documents/${encodeURIComponent(documentId)}`,
      { method: 'DELETE' },
    ),
  capabilities: () =>
    request<IngestionCapabilities>('/api/ingestion-capabilities'),
  queueCrawl: (
    workspaceId: string,
    requestBody: {
      url: string
      max_pages: number
      max_depth: number
      max_bytes: number
      sitemap: boolean
    },
  ) =>
    request<{ job_id: string; state: string }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/crawl`,
      { method: 'POST', body: JSON.stringify(requestBody) },
    ),
  ingestionJob: (jobId: string) =>
    request<IngestionJob>(`/api/ingestion-jobs/${encodeURIComponent(jobId)}`),
  evidence: (evidenceId: string) =>
    request<EvidenceView>(`/api/evidence/${encodeURIComponent(evidenceId)}`),
}
import { apiFetch } from './lib/apiFetch'

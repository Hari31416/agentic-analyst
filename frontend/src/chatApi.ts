export type AnswerLanguage = 'en-IN' | 'hi-IN'

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  run_id: string | null
  references: { evidence_ids: string[]; artifact_ids: string[] }
}

export type RunState =
  | 'queued'
  | 'running'
  | 'awaiting_clarification'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'budget_exhausted'
  | string

export type AnalysisRun = {
  id: string
  state: RunState
  created_at: string
  outcome: {
    text?: string
    artifact_ids?: string[]
    cleanup?: 'pending' | 'complete' | 'failed' | string
  } | null
  retrieval_profile?: 'basic' | 'advanced'
  answer_language?: AnswerLanguage
  selected_source_ids?: string[]
  selected_dataset_ids?: string[]
}

export type RunEvent = {
  id: string
  run_id: string
  sequence: number
  type:
    | 'status'
    | 'tool_started'
    | 'tool_finished'
    | 'answer'
    | 'error'
    | 'terminal'
    | string
  payload: Record<string, unknown>
  created_at: string
  schema_version: number
}

export type RunArtifact = {
  id: string
  display_name: string
  media_type: string
  byte_size: number
  sha256: string
}

export type ArtifactPreview = {
  id: string
  display_name: string
  media_type: string
  byte_size: number
  text: string | null
  truncated: boolean
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
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
      }
    } catch {
      // Keep the HTTP status when the API response is not JSON.
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const chatApi = {
  messages: (threadId: string) =>
    request<ChatMessage[]>(
      `/api/threads/${encodeURIComponent(threadId)}/messages`,
    ),
  runs: (threadId: string) =>
    request<AnalysisRun[]>(`/api/threads/${encodeURIComponent(threadId)}/runs`),
  createRun: (
    threadId: string,
    input: {
      text: string
      selected_source_ids: string[]
      selected_dataset_ids: string[]
      retrieval_profile?: 'basic' | 'advanced'
      answer_language: AnswerLanguage
      request_id: string
    },
  ) =>
    request<AnalysisRun>(`/api/threads/${encodeURIComponent(threadId)}/runs`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  cancelRun: (runId: string) =>
    request<AnalysisRun>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    }),
  run: (runId: string) =>
    request<AnalysisRun>(`/api/runs/${encodeURIComponent(runId)}`),
  retrieval: (runId: string) =>
    request<Record<string, unknown>>(
      `/api/runs/${encodeURIComponent(runId)}/retrieval`,
    ),
  artifacts: (runId: string) =>
    request<RunArtifact[]>(`/api/runs/${encodeURIComponent(runId)}/artifacts`),
  previewArtifact: (artifactId: string, maxCharacters = 4000) =>
    request<ArtifactPreview>(
      `/api/artifacts/${encodeURIComponent(artifactId)}/preview?max_characters=${maxCharacters}`,
    ),
  artifactUrl: (artifactId: string) =>
    `/api/artifacts/${encodeURIComponent(artifactId)}/content`,
  subscribe: (
    runId: string,
    onEvent: (event: RunEvent) => void,
    onError: () => void,
  ) => {
    const stream = new EventSource(
      `/api/runs/${encodeURIComponent(runId)}/events`,
    )
    stream.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data) as RunEvent)
      } catch {
        onError()
      }
    }
    stream.onerror = onError
    return () => stream.close()
  },
}

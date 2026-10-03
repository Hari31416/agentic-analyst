export type AuditEntry = {
  id: string
  kind: 'event' | 'tool_call' | 'audit'
  action: string
  state: string
  created_at: string | null
  details: Record<string, unknown>
}

export type AuditPage = {
  run_id: string
  state: string
  total: number
  cursor: number
  limit: number
  next_cursor: number | null
  entries: AuditEntry[]
}

async function get<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Audit request failed (${response.status})`)
  return (await response.json()) as T
}

export const auditApi = {
  page: (runId: string, action = '', state = '') => {
    const query = new URLSearchParams({ limit: '100' })
    if (action) query.set('action', action)
    if (state) query.set('state', state)
    return get<AuditPage>(
      `/api/runs/${encodeURIComponent(runId)}/audit?${query}`,
    )
  },
  exportUrl: (runId: string) =>
    `/api/runs/${encodeURIComponent(runId)}/audit/export`,
}

export const SESSION_EXPIRED_EVENT = 'agentic-analyst:session-expired'

export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const response = await fetch(input, {
    ...init,
    credentials: init?.credentials ?? 'same-origin',
  })
  const url =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.pathname
        : input.url
  if (
    response.status === 401 &&
    url.startsWith('/api/') &&
    !/^\/api\/auth\/(login|me|logout)(?:$|[?])/.test(url)
  ) {
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
  }
  return response
}

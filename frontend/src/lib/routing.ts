export type ActiveView = 'chat' | 'workbench' | 'outputs' | 'pins'

export interface RouteParams {
  workspaceId?: string
  threadId?: string
  view: ActiveView
}

export function parsePath(pathname: string): RouteParams {
  const segments = pathname.split('/').filter(Boolean)
  if (segments[0] === 'workspaces' && segments[1]) {
    const wsId = decodeURIComponent(segments[1])
    const section = segments[2]
    if (section === 'threads' && segments[3]) {
      return {
        workspaceId: wsId,
        threadId: decodeURIComponent(segments[3]),
        view: 'chat',
      }
    }
    if (section === 'sources' || section === 'workbench') {
      return {
        workspaceId: wsId,
        view: 'workbench',
      }
    }
    if (section === 'pins') return { workspaceId: wsId, view: 'pins' }
    if (section === 'outputs') {
      return {
        workspaceId: wsId,
        view: 'outputs',
      }
    }
    return {
      workspaceId: wsId,
      view: 'chat',
    }
  }
  return {
    view: 'chat',
  }
}

export function formatPath(params: {
  workspaceId?: string
  threadId?: string
  view?: ActiveView
}): string {
  const ws = params.workspaceId ? encodeURIComponent(params.workspaceId) : ''
  if (!ws) return '/'
  if (params.view === 'workbench') return `/workspaces/${ws}/sources`
  if (params.view === 'pins') return `/workspaces/${ws}/pins`
  if (params.view === 'outputs') return `/workspaces/${ws}/outputs`
  if (params.threadId) {
    return `/workspaces/${ws}/threads/${encodeURIComponent(params.threadId)}`
  }
  return `/workspaces/${ws}`
}

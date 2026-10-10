import assert from 'node:assert/strict'
import { act, create } from 'react-test-renderer'
import { PinsProvider } from '../src/components/PinsContext'
import Reports from '../src/Reports'
import { formatPath, parsePath } from '../src/lib/routing'
import { reportApi } from '../src/reportApi'
import type { SavedPin } from '../src/pinsApi'

const pin: SavedPin = {
  id: 'artifact-pin',
  kind: 'artifact',
  target_id: 'artifact-1',
  title: 'Chart export',
  notes: '',
  tags: [],
  workspace_id: 'ws',
  thread_id: 'thread-1',
  thread_label: 'Market study',
  created_at: '2026-10-10T00:00:00Z',
  updated_at: '2026-10-10T00:00:00Z',
}
const readyVersion = {
  id: 'version-1',
  number: 1,
  state: 'ready' as const,
  language: 'en-IN',
  created_at: '2026-10-10T00:00:00Z',
  error: null,
  feedback: '',
  mode: 'initial',
  assets: {
    'artifact-1': {
      display_name: 'Sales chart',
      media_type: 'application/vnd.plotly.v1+json',
      byte_size: 128,
      sha256: 'abc',
    },
    'table-1': {
      display_name: 'Sales table',
      media_type: 'text/csv',
      byte_size: 64,
      sha256: 'def',
    },
  },
  document: {
    title: 'Chart notes',
    language: 'en-IN',
    sections: [
      {
        id: 's1',
        heading: 'Results',
        blocks: [
          { id: 'b1', type: 'paragraph', text: 'Evidence summary.' },
          {
            id: 'b2',
            type: 'figure',
            artifact_id: 'artifact-1',
            caption: 'Sales chart',
          },
          {
            id: 'b3',
            type: 'table',
            artifact_id: 'table-1',
            caption: 'Revenue by month',
            columns: ['Month', 'Revenue'],
            max_rows: 12,
          },
        ],
      },
    ],
  },
}
const report = {
  id: 'report-1',
  workspace_id: 'ws',
  title: 'Chart notes',
  created_at: '2026-10-10T00:00:00Z',
  updated_at: '2026-10-10T00:00:00Z',
  latest_version: readyVersion,
  versions: [readyVersion],
}
const earlierReport = {
  ...report,
  id: 'report-earlier',
  title: 'Earlier analysis',
  versions: [readyVersion],
}
const originalFetch = globalThis.fetch
const originalConsoleError = console.error
const consoleErrors: unknown[][] = []
console.error = (...args: unknown[]) => consoleErrors.push(args)
const requests: { url: string; method: string; body?: any }[] = []
let releaseStaleDetail: ((response: Response) => void) | null = null
let holdStaleDetail = false
let releaseRename: ((response: Response) => void) | null = null
globalThis.fetch = async (input, options) => {
  const url = String(input)
  const method = options?.method ?? 'GET'
  const body = options?.body ? JSON.parse(String(options.body)) : undefined
  requests.push({ url, method, body })
  if (url.endsWith('/pins?limit=200&offset=0')) return Response.json([pin])
  if (url === '/api/report-languages')
    return Response.json([
      { language: 'en-IN', label: 'English (India)' },
      { language: 'hi-IN', label: 'हिन्दी' },
    ])
  if (url.startsWith('/api/workspaces/ws/reports?') && method === 'GET')
    return Response.json([earlierReport])
  if (url === '/api/workspaces/ws/reports' && method === 'POST')
    return Response.json(report, { status: 201 })
  if (url === '/api/reports/report-1' && method === 'GET' && holdStaleDetail)
    return new Promise((resolve) => {
      releaseStaleDetail = resolve
    })
  if (url === '/api/reports/report-1' && method === 'GET')
    return Response.json(report)
  if (url === '/api/reports/report-earlier' && method === 'GET')
    return Response.json(earlierReport)
  if (url.includes('/assets/table-1/table?'))
    return Response.json({
      columns: ['Month', 'Revenue'],
      rows: [
        ['January', 120],
        ['February', 145],
      ],
      truncated: false,
    })
  if (url === '/api/reports/report-1/regenerate')
    return Response.json(
      {
        ...report,
        versions: [
          {
            ...readyVersion,
            id: 'version-2',
            number: 2,
            state: 'queued',
            document: null,
            feedback: body.feedback,
            mode: body.mode,
          },
          readyVersion,
        ],
      },
      { status: 202 },
    )
  if (url === '/api/reports/report-1' && method === 'PATCH')
    return new Promise((resolve) => {
      releaseRename = resolve
    })
  throw new Error(`Unexpected request ${method} ${url}`)
}
let renderer: ReturnType<typeof create>
try {
  await act(async () => {
    renderer = create(
      <PinsProvider workspaceId="ws">
        <Reports workspaceId="ws" />
      </PinsProvider>,
    )
  })
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  await act(async () => {
    renderer!.root.findByProps({ 'aria-label': 'New report' }).props.onClick()
  })
  const artifactCheckbox = renderer!.root
    .findAllByType('input')
    .find((input) => input.props.type === 'checkbox')!
  await act(async () => {
    artifactCheckbox.props.onChange()
  })
  const createForm = renderer!.root
    .findAllByType('form')
    .find((form) => form.props.className === 'report-create')!
  const titleInput = createForm
    .findAllByType('input')
    .find((input) => input.props.maxLength === 180)!
  assert.equal(titleInput.props.required, undefined)
  assert.equal(titleInput.props.value, '')
  assert.equal(
    createForm
      .findAllByType('button')
      .find((button) => button.props.type === 'submit')!.props.disabled,
    false,
  )
  await act(async () => {
    holdStaleDetail = true
    await createForm.props.onSubmit({ preventDefault() {} })
  })
  const createRequest = requests.find(
    (request) => request.method === 'POST' && request.url.endsWith('/reports'),
  )!
  assert.deepEqual(createRequest.body.pin_ids, ['artifact-pin'])
  assert.equal(createRequest.body.title, '')
  assert.equal(createRequest.body.language, 'en-IN')
  assert.ok(
    releaseStaleDetail,
    'create should trigger the selected report detail request',
  )
  assert.ok(
    renderer!.root
      .findAllByType('p')
      .some((paragraph) => paragraph.children.join('') === 'Evidence summary.'),
  )
  assert.ok(
    renderer!.root
      .findAllByType('a')
      .some(
        (link) =>
          link.props.className === 'report-retained-asset' &&
          link.props.href.includes('/assets/artifact-1'),
      ),
  )
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  assert.ok(
    renderer!.root
      .findAllByType('td')
      .some((cell) => cell.children.join('') === 'January'),
  )
  assert.ok(
    requests.some((request) => request.url.includes('table?max_rows=12')),
  )
  const regenForm = renderer!.root
    .findAllByType('form')
    .find((form) => form.props.className === 'report-regenerate')!
  const regenTextarea = regenForm.findByType('textarea')
  await act(async () => {
    regenTextarea.props.onChange({
      target: { value: 'Keep the chart references unchanged.' },
    })
  })
  const selects = renderer!.root.findAllByType('select')
  const modeSelect = selects.find((select) => select.props.value === 'wording')!
  await act(async () => {
    modeSelect.props.onChange({ target: { value: 'restructure' } })
  })
  await act(async () => {
    await regenForm.props.onSubmit({ preventDefault() {} })
  })
  const regenRequest = requests.find((request) =>
    request.url.endsWith('/regenerate'),
  )!
  assert.deepEqual(regenRequest.body, {
    version_id: 'version-1',
    feedback: 'Keep the chart references unchanged.',
    mode: 'restructure',
  })
  await act(async () => {
    releaseStaleDetail!(Response.json(report))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  assert.equal(
    renderer!.root.findByProps({ 'aria-label': 'Choose report version' }).props
      .value,
    'version-2',
    'an older detail response must not replace the regenerated version',
  )

  await act(async () => {
    renderer!.root
      .findByProps({ className: 'report-text-action' })
      .props.onClick()
  })
  const renameForm = renderer!.root
    .findAllByType('form')
    .find((form) => form.props.className === 'report-rename')!
  await act(async () => {
    await renameForm.props.onSubmit({ preventDefault() {} })
  })
  const earlierButton = renderer!.root
    .findAllByType('button')
    .find(
      (button) =>
        button.props.className?.startsWith('report-library-item') &&
        button
          .findAllByType('strong')
          .some((label) => label.children.join('') === 'Earlier analysis'),
    )!
  await act(async () => {
    earlierButton.props.onClick()
  })
  const earlierRegenerate = renderer!.root
    .findAllByType('form')
    .find((form) => form.props.className === 'report-regenerate')!
    .findAllByType('button')
    .find((button) => button.children.includes('Regenerate'))!
  assert.equal(earlierRegenerate.props.disabled, false)
  assert.ok(
    renderer!.root
      .findAllByType('button')
      .some(
        (button) =>
          button.props.className === 'report-text-action' &&
          button.children.join('') === 'Rename',
      ),
    'switching reports should clear busy and rename state',
  )
  await act(async () => {
    releaseRename!(Response.json({ ...report, title: 'Late rename' }))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  assert.ok(
    renderer!.root
      .findAllByType('h2')
      .some((heading) => heading.children.join('') === 'Earlier analysis'),
  )
  assert.equal(parsePath('/workspaces/ws/reports').view, 'reports')
  assert.equal(
    formatPath({ workspaceId: 'ws', view: 'reports' }),
    '/workspaces/ws/reports',
  )
  const reportRequests = requests.filter((request) =>
    request.url.startsWith('/api/workspaces/ws/reports?'),
  )
  assert.equal(
    reportRequests[0].url,
    '/api/workspaces/ws/reports?limit=100&offset=0',
  )
  await act(async () => {
    renderer!.unmount()
  })
  assert.deepEqual(consoleErrors, [])
  const reportListCalls: string[] = []
  globalThis.fetch = async (input) => {
    const url = String(input)
    reportListCalls.push(url)
    const offset = Number(
      new URL(url, 'http://localhost').searchParams.get('offset'),
    )
    return Response.json(
      Array.from({ length: offset === 0 ? 100 : 1 }, (_, index) => ({
        ...report,
        id: `report-${offset + index}`,
      })),
    )
  }
  const allReports = await reportApi.list('ws')
  assert.equal(allReports.length, 101)
  assert.deepEqual(reportListCalls, [
    '/api/workspaces/ws/reports?limit=100&offset=0',
    '/api/workspaces/ws/reports?limit=100&offset=100',
  ])
  console.log(
    'Report creation, artifact-only selection, version feedback, preview, and routing checks passed',
  )
} finally {
  globalThis.fetch = originalFetch
  console.error = originalConsoleError
}

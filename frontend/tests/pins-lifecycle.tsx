import assert from 'node:assert/strict'
import { act, create } from 'react-test-renderer'
import { PinsProvider, usePins } from '../src/components/PinsContext'
import { PinButton } from '../src/components/PinButton'
import SavedPins from '../src/SavedPins'
import { formatPath, parsePath } from '../src/lib/routing'
import type { SavedPin } from '../src/pinsApi'

const target = '12345678-1234-1234-1234-123456789abc'
const initial: SavedPin = {
  id: target,
  kind: 'thread',
  target_id: target,
  title: 'Saved thread',
  notes: '',
  tags: ['sales'],
  workspace_id: 'ws',
  thread_id: target,
  thread_label: 'Sales',
  created_at: '2026-10-10T00:00:00Z',
  updated_at: '2026-10-10T00:00:00Z',
}
const originalFetch = globalThis.fetch
let latest: ReturnType<typeof usePins>
function Probe() {
  latest = usePins()
  return <PinButton kind="thread" targetId={target} title="Sales" />
}
let pendingList: ((response: Response) => void) | null = null
let holdList = false
const requests: { url: string; method: string; body?: unknown }[] = []
globalThis.fetch = async (url, options) => {
  const path = String(url)
  const method = options?.method ?? 'GET'
  requests.push({
    url: path,
    method,
    body: options?.body ? JSON.parse(String(options.body)) : undefined,
  })
  if (method === 'PATCH')
    return Response.json({ ...initial, title: 'Edited title' })
  if (method === 'DELETE') return new Response(null, { status: 204 })
  if (method === 'POST') return Response.json(initial, { status: 201 })
  if (holdList)
    return new Promise((resolve) => {
      pendingList = resolve
    })
  return Response.json([initial])
}
let renderer: ReturnType<typeof create>
try {
  await act(async () => {
    renderer = create(
      <PinsProvider workspaceId="ws">
        <Probe />
      </PinsProvider>,
    )
  })
  assert.equal(latest!.pins.length, 1)
  assert.equal(renderer!.root.findByType('button').props['aria-pressed'], true)
  await act(async () => {
    await latest!.save(
      'thread',
      target,
      { title: 'Edited title', notes: '', tags: [] },
      initial.id,
    )
  })
  assert.equal(latest!.pins[0].title, 'Edited title')
  // A list response that started before a mutation must not resurrect a removed pin.
  holdList = true
  let refresh: Promise<void>
  await act(async () => {
    refresh = latest!.refresh()
  })
  await act(async () => {
    await latest!.remove(initial.id)
  })
  await act(async () => {
    pendingList!(Response.json([initial]))
    await refresh!
  })
  assert.equal(latest!.pins.length, 0)
  assert.equal(renderer!.root.findByType('button').props['aria-pressed'], false)
  await act(async () => {
    await latest!.save('thread', target, {
      title: 'Saved thread',
      notes: '',
      tags: ['sales'],
    })
  })
  assert.equal(latest!.pins.length, 1)
  await act(async () => {
    renderer!.unmount()
  })
  // All pages load, so a pin beyond the first 200 remains editable.
  holdList = false
  globalThis.fetch = async (url) =>
    Response.json(
      String(url).includes('offset=200')
        ? [{ ...initial, id: 'last' }]
        : Array.from({ length: 200 }, (_, i) => ({
            ...initial,
            id: String(i),
          })),
    )
  await act(async () => {
    renderer = create(
      <PinsProvider workspaceId="ws">
        <Probe />
      </PinsProvider>,
    )
  })
  assert.equal(latest!.pins.length, 201)
  await act(async () => {
    renderer!.unmount()
  })
  const artifact = {
    ...initial,
    kind: 'artifact' as const,
    id: 'artifact-pin',
    title: 'Monthly report',
    tags: ['finance'],
  }
  const message = {
    ...initial,
    kind: 'message' as const,
    id: 'answer-pin',
    title: 'Sales answer',
  }
  globalThis.fetch = async () => Response.json([initial, artifact, message])
  await act(async () => {
    renderer = create(
      <PinsProvider workspaceId="ws">
        <SavedPins onOpenThread={() => {}} />
      </PinsProvider>,
    )
  })
  assert.equal(
    renderer!.root.findAllByProps({ className: 'pin-entry-open' }).length,
    3,
  )
  await act(async () => {
    renderer!.root
      .findByProps({ 'aria-label': 'Filter pins by type' })
      .props.onChange({ target: { value: 'artifact' } })
  })
  assert.equal(
    renderer!.root.findAllByProps({ className: 'pin-entry-open' }).length,
    1,
  )
  assert.equal(
    renderer!.root.findByType('strong').children.join(''),
    'Monthly report',
  )
  await act(async () => {
    renderer!.root
      .findByProps({ 'aria-label': 'Search pins' })
      .props.onChange({ target: { value: 'finance' } })
  })
  assert.equal(
    renderer!.root.findAllByProps({ className: 'pin-entry-open' }).length,
    1,
  )
  await act(async () => {
    renderer!.root
      .findByProps({ 'aria-label': 'Search pins' })
      .props.onChange({ target: { value: 'missing' } })
  })
  assert.equal(
    renderer!.root.findAllByProps({ className: 'pin-entry-open' }).length,
    0,
  )
  await act(async () => {
    renderer!.unmount()
  })
  assert.equal(
    requests.find((request) => request.method === 'POST')?.url,
    '/api/workspaces/ws/pins',
  )
  assert.equal(
    requests.find((request) => request.method === 'DELETE')?.url,
    `/api/pins/${initial.id}`,
  )
  assert.deepEqual(parsePath('/workspaces/ws/pins'), {
    workspaceId: 'ws',
    view: 'pins',
  })
  assert.equal(
    formatPath({ workspaceId: 'ws', view: 'pins' }),
    '/workspaces/ws/pins',
  )
  console.log(
    'Pin CRUD state, stale refresh, pagination, artifact filtering, search, and routing checks passed',
  )
} finally {
  globalThis.fetch = originalFetch
}

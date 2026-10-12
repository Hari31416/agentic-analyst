import assert from 'node:assert/strict'
import { act, create } from 'react-test-renderer'
import { ReportEditor, ReportComparison } from '../src/ReportEditor'
import { reportApi, type ReportVersion, type Report } from '../src/reportApi'

const version: ReportVersion = {
  id: 'v1',
  number: 1,
  state: 'ready',
  language: 'en-IN',
  created_at: '',
  error: null,
  feedback: '',
  mode: 'initial',
  document: {
    title: 'Original',
    language: 'en-IN',
    sections: [
      {
        id: 's1',
        heading: 'Summary',
        blocks: [
          { id: 'p1', type: 'paragraph', text: 'Revenue was 42 [source].' },
          {
            id: 't1',
            type: 'table',
            artifact_id: 'a1',
            caption: 'Revenue',
            columns: ['Total'],
            max_rows: 30,
          },
        ],
      },
    ],
  },
}
const originalEdit = reportApi.edit
const originalWindow = globalThis.window
const events = new EventTarget()
globalThis.window = events as unknown as Window & typeof globalThis
let saved: Report | undefined
let calls = 0
reportApi.edit = async (id, baseId, document) => {
  calls++
  assert.equal(id, 'r1')
  assert.equal(baseId, 'v1')
  assert.equal(document.sections[0].blocks[0].type, 'paragraph')
  assert.deepEqual(
    document.sections[0].blocks[1],
    version.document!.sections[0].blocks[1],
  )
  if (calls === 1) throw new Error('Rendering queue unavailable')
  return {
    id: 'r1',
    workspace_id: 'w1',
    title: 'Original',
    created_at: '',
    updated_at: '',
    versions: [{ ...version, id: 'v2', number: 2, document }],
  }
}
let renderer: ReturnType<typeof create>
function button(text: string) {
  return renderer.root
    .findAllByType('button')
    .find((node) => node.children.join('') === text)!
}
try {
  await act(async () => {
    renderer = create(
      <ReportEditor
        reportId="r1"
        version={version}
        onCancel={() => {}}
        onSaved={(report) => {
          saved = report
        }}
      />,
    )
  })
  assert.equal(button('Review changes').props.disabled, true)
  await act(async () => {
    renderer.root
      .findAllByType('textarea')[0]
      .props.onChange({ target: { value: 'Revenue was 54 [source].' } })
  })
  assert.equal(
    version.document!.sections[0].blocks[0].type === 'paragraph' &&
      version.document!.sections[0].blocks[0].text,
    'Revenue was 42 [source].',
  )
  const unload = new Event('beforeunload', { cancelable: true })
  events.dispatchEvent(unload)
  assert.equal(unload.defaultPrevented, true)
  await act(async () => {
    button('Review changes').props.onClick()
  })
  assert.equal(calls, 0)
  assert.equal(
    renderer.root.findAllByProps({ className: 'report-diff' }).length,
    1,
  )
  assert.equal(
    renderer.root
      .findByProps({ className: 'report-diff-before' })
      .children.join(''),
    'Revenue was 42 [source].',
  )
  assert.equal(
    renderer.root
      .findByProps({ className: 'report-diff-after' })
      .children.join(''),
    'Revenue was 54 [source].',
  )
  await act(async () => {
    button('Save new version').props.onClick()
  })
  assert.equal(
    renderer.root.findByProps({ role: 'alert' }).children.join(''),
    'Rendering queue unavailable',
  )
  assert.equal(saved, undefined)
  await act(async () => {
    button('Back to editing').props.onClick()
  })
  assert.equal(
    renderer.root.findAllByType('textarea')[0].props.value,
    'Revenue was 54 [source].',
  )
  await act(async () => {
    button('Review changes').props.onClick()
  })
  await act(async () => {
    button('Save new version').props.onClick()
  })
  assert.equal(saved!.versions![0].number, 2)
  await act(async () => {
    renderer.unmount()
  })
  const after = structuredClone(version.document!)
  after.sections[0].blocks.reverse()
  await act(async () => {
    renderer = create(
      <ReportComparison
        before={version.document!}
        after={after}
        beforeLabel="v1"
        afterLabel="v2"
      />,
    )
  })
  assert.equal(
    renderer.root.findAllByProps({ className: 'report-diff' }).length,
    1,
  )
  await act(async () => {
    renderer.unmount()
  })
  console.log(
    'Direct editing, draft review, save recovery, protected assets, and version comparison passed',
  )
} finally {
  reportApi.edit = originalEdit
  globalThis.window = originalWindow
}

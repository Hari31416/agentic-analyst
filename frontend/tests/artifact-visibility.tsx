import assert from 'node:assert/strict'
import { RightSidebar } from '../src/components/RightSidebar'
import { ArtifactManifest } from '../src/phase06Api'
import { outputArtifactCount } from '../src/lib/artifactVisibility'

const artifacts = [
  { id: 'out', display_name: 'Summary.csv', role: 'output' },
  { id: 'code', display_name: 'analysis.py', role: 'execution_code' },
  { id: 'input', display_name: 'input.json', role: 'input_snapshot' },
].map((item) => ({
  ...item,
  media_type: 'text/plain',
  byte_size: 10,
  sha256: '',
  run_id: 'run',
  lineage: {},
  durable: true,
  artifact_type: 'file',
  metadata: {},
})) as ArtifactManifest[]

assert.equal(outputArtifactCount(artifacts), 1)
const { act, create } = await import('react-test-renderer')
const renderer = create(
  <RightSidebar
    isOpen
    onToggleOpen={() => {}}
    sources={[]}
    datasets={[]}
    selectedSourceId={null}
    onSelectSource={() => {}}
    onOpenInWorkbench={() => {}}
    onUploadFile={async () => {}}
    artifacts={artifacts}
  />,
)
await act(async () => {
  renderer.root
    .findAllByType('button')
    .find((button) =>
      button
        .findAllByType('span')
        .some((span) => String(span.children.join('')).startsWith('Artifacts')),
    )
    ?.props.onClick()
})
assert.match(JSON.stringify(renderer.toJSON()), /Summary\.csv/)
assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /analysis\.py/)
const checkbox = renderer.root.findByType('input')
await act(async () => checkbox.props.onChange({ target: { checked: true } }))
assert.match(JSON.stringify(renderer.toJSON()), /analysis\.py/)
const findSearch = () =>
  renderer.root
    .findAllByType('input')
    .find((input) => input.props.type === 'text')!
await act(async () =>
  findSearch().props.onChange({ target: { value: 'analysis' } }),
)
assert.match(JSON.stringify(renderer.toJSON()), /analysis\.py/)
await act(async () => checkbox.props.onChange({ target: { checked: false } }))
assert.equal(findSearch().props.value, 'analysis')
assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /analysis\.py/)
await act(async () => findSearch().props.onChange({ target: { value: '' } }))
assert.match(JSON.stringify(renderer.toJSON()), /Summary\.csv/)
assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /analysis\.py/)
console.log(
  'Artifact visibility, toggle, search, and output count checks passed',
)

const { default: ArtifactBrowser } = await import('../src/ArtifactBrowser')
const originalFetch = globalThis.fetch
const standaloneArtifacts = artifacts.map((artifact) => ({
  ...artifact,
  id: `standalone-${artifact.id}`,
}))
globalThis.fetch = async () => Response.json(standaloneArtifacts)
let browser: ReturnType<typeof create>
try {
  await act(async () => {
    browser = create(
      <ArtifactBrowser
        workspaceId="workspace"
        onWorkspaceImported={() => {}}
        onSourcesChanged={async () => {}}
      />,
    )
  })
  const outputRow = () =>
    browser!.root
      .findAllByType('button')
      .find((button) =>
        button
          .findAllByType('strong')
          .some((node) =>
            String(node.children.join('')).includes('Summary.csv'),
          ),
      )!
  assert.equal(outputRow().props.className.includes('selected'), true)
  assert.equal(
    browser!.root
      .findAllByType('button')
      .some((button) =>
        button
          .findAllByType('strong')
          .some((node) =>
            String(node.children.join('')).includes('analysis.py'),
          ),
      ),
    false,
  )
  const browserToggle = browser!.root
    .findAllByType('input')
    .find((input) => input.props.type === 'checkbox')!
  await act(async () =>
    browserToggle.props.onChange({ target: { checked: true } }),
  )
  const codeRow = browser!.root
    .findAllByType('button')
    .find((button) =>
      button
        .findAllByType('strong')
        .some((node) => String(node.children.join('')).includes('analysis.py')),
    )!
  await act(async () => codeRow.props.onClick())
  assert.equal(codeRow.props.className.includes('selected'), true)
  await act(async () =>
    browserToggle.props.onChange({ target: { checked: false } }),
  )
  assert.equal(outputRow().props.className.includes('selected'), true)
} finally {
  globalThis.fetch = originalFetch
  await act(async () => browser?.unmount())
}
console.log(
  'Standalone artifact browser visibility and selection checks passed',
)

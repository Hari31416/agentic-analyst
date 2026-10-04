import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const output = await mkdtemp(join(tmpdir(), 'artifact-rendering-'))
try {
  await build({
    logLevel: 'error',
    ssr: { noExternal: true },
    build: {
      ssr: 'tests/artifact-rendering.tsx',
      outDir: output,
      rolldownOptions: { output: { entryFileNames: 'check.mjs' } },
    },
  })
  await import(pathToFileURL(join(output, 'check.mjs')).href)
} finally {
  await rm(output, { recursive: true, force: true })
}

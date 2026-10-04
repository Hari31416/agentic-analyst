import { documentApi } from '../documentApi'
import { structuredApi, SourceView } from '../structuredApi'

export function isDocumentFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return (
    name.endsWith('.pdf') ||
    name.endsWith('.docx') ||
    name.endsWith('.txt') ||
    name.endsWith('.md') ||
    name.endsWith('.markdown') ||
    name.endsWith('.html') ||
    name.endsWith('.htm') ||
    name.endsWith('.pptx') ||
    file.type.includes('pdf') ||
    file.type.includes('word') ||
    file.type.includes('text')
  )
}

export async function uploadWorkspaceFile(
  workspaceId: string,
  file: File,
  onProgress?: (progress: number) => void,
): Promise<SourceView> {
  if (isDocumentFile(file)) {
    const result = await documentApi.upload(workspaceId, file, onProgress)
    return result.source
  }
  onProgress?.(50)
  const result = await structuredApi.uploadFile(workspaceId, file)
  onProgress?.(100)
  return result
}

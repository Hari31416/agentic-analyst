import { documentApi } from '../documentApi'
import { structuredApi, SourceView } from '../structuredApi'

export function isStructuredDataFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return (
    name.endsWith('.csv') ||
    name.endsWith('.xlsx') ||
    name.endsWith('.xls') ||
    name.endsWith('.json') ||
    name.endsWith('.parquet') ||
    file.type === 'text/csv' ||
    file.type === 'application/vnd.ms-excel' ||
    file.type ===
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
    file.type === 'application/parquet' ||
    file.type === 'application/vnd.apache.parquet'
  )
}

export function isDocumentFile(file: File): boolean {
  if (isStructuredDataFile(file)) {
    return false
  }
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
    file.type === 'application/pdf' ||
    file.type ===
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ||
    file.type ===
      'application/vnd.openxmlformats-officedocument.presentationml.presentation' ||
    file.type === 'text/plain' ||
    file.type === 'text/markdown' ||
    file.type === 'text/html'
  )
}

export async function uploadWorkspaceFile(
  workspaceId: string,
  file: File,
  onProgress?: (progress: number) => void,
): Promise<SourceView> {
  if (isStructuredDataFile(file)) {
    onProgress?.(50)
    const result = await structuredApi.uploadFile(workspaceId, file)
    onProgress?.(100)
    return result
  }
  if (isDocumentFile(file)) {
    const result = await documentApi.upload(workspaceId, file, onProgress)
    return result.source
  }
  throw new Error(
    `Unsupported file type: "${file.name}". Supported formats: PDF, DOCX, TXT, MD, HTML, PPTX, CSV, XLSX, XLS, JSON, Parquet.`,
  )
}

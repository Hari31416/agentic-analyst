export type LanguageCapability = { tag: string; name: string }

export type ProviderCapability = {
  available: boolean
  provider: string | null
  model: string | null
  reason: string | null
}

export type LanguageCapabilities = {
  languages: LanguageCapability[]
  stt: ProviderCapability & {
    max_upload_bytes?: number
    max_duration_seconds?: number
  }
  audio_limits?: {
    max_upload_bytes?: number
    max_duration_seconds?: number
  }
  tts: ProviderCapability
  translation: ProviderCapability
}

export type Transcription = {
  text: string
  language: string | null
  provider: string | null
  model: string | null
  duration_seconds: number | null
}

async function readError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown }
    if (typeof data.detail === 'string') return data.detail
  } catch {
    // Keep the HTTP status as the useful error when the body is not JSON.
  }
  return `Request failed (${response.status})`
}

export const languageApi = {
  async capabilities(signal?: AbortSignal): Promise<LanguageCapabilities> {
    const response = await apiFetch('/api/languages/capabilities', { signal })
    if (!response.ok) throw new Error(await readError(response))
    return (await response.json()) as LanguageCapabilities
  },

  async transcribe(
    audio: Blob,
    filename: string,
    language: string,
    signal?: AbortSignal,
  ): Promise<Transcription> {
    const body = new FormData()
    body.append('audio', audio, filename)
    body.append('language', language)
    const response = await apiFetch('/api/languages/transcribe', {
      method: 'POST',
      body,
      signal,
    })
    if (!response.ok) throw new Error(await readError(response))
    return (await response.json()) as Transcription
  },
}
import { apiFetch } from './lib/apiFetch'

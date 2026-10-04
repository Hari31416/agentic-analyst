import {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Check,
  CircleStop,
  FileSpreadsheet,
  FileText,
  Globe2,
  LoaderCircle,
  Mic,
  ListChecks,
  Square,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import {
  AnalysisRun,
  AnswerLanguage,
  ApiError,
  ChatMessage,
  RunArtifact,
  RunEvent,
  chatApi,
} from './chatApi'
import { InlineArtifactPreview } from './components/InlineArtifactPreview'
import { DatasetSummary, sourceKindLabel } from './structuredApi'
import { EvidenceView, documentApi } from './documentApi'
import { LanguageCapabilities, languageApi } from './languageApi'
import { UiLanguage, UiTextKey, uiText } from './uiText'
import { useUiLanguage } from './hooks/useUiLanguage'
import { MarkdownRenderer } from './components/MarkdownRenderer'
import { AuditEntry, AuditPage, auditApi } from './auditApi'
import './chat.css'

export type ChatSource = {
  id: string
  display_name: string
  kind: string
  state: string
  version: number
}

type ChatPanelProps = {
  threadId: string
  sources: ChatSource[]
  datasets: DatasetSummary[]
  modelAvailable: boolean
  modelMessage?: string
}

type RetryInput = {
  text: string
  selectedSourceIds: string[]
  selectedDatasetIds: string[]
  language: AnswerLanguage
  retrievalProfile?: 'basic' | 'advanced'
}

type ProgressItem = { id: string; text: string; kind: string }

const liveStates = new Set(['queued', 'running'])
const DEFAULT_MAX_AUDIO_BYTES = 10 * 1024 * 1024
const DEFAULT_MAX_RECORDING_SECONDS = 60

function isLive(run: AnalysisRun): boolean {
  return liveStates.has(run.state) || run.outcome?.cleanup === 'pending'
}

function labelForState(runOrState: AnalysisRun | string): string {
  const state = typeof runOrState === 'string' ? runOrState : runOrState.state
  if (
    typeof runOrState !== 'string' &&
    runOrState.outcome?.cleanup === 'pending'
  ) {
    return 'Stopping sandbox'
  }
  const labels: Record<string, string> = {
    queued: 'Waiting to start',
    running: 'Working through the sources',
    awaiting_clarification: 'Needs a follow-up',
    completed: 'Complete',
    failed: 'Run failed',
    cancelled: 'Stopped',
    budget_exhausted: 'Run limit reached',
  }
  return labels[state] ?? state.replaceAll('_', ' ')
}

function safeText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim()
    ? value.trim().slice(0, 300)
    : undefined
}

function auditReferences(entry: AuditEntry): {
  artifactIds: string[]
  evidenceIds: string[]
} {
  const artifactIds = new Set<string>()
  const evidenceIds = new Set<string>()
  const visit = (value: unknown, depth = 0) => {
    if (!value || typeof value !== 'object' || depth > 3) return
    if (Array.isArray(value)) return
    const record = value as Record<string, unknown>
    for (const key of ['artifact_ids', 'evidence_ids', 'code_artifact_id']) {
      const raw = record[key]
      const values = key === 'code_artifact_id' ? [raw] : raw
      if (!Array.isArray(values)) continue
      for (const id of values) {
        if (typeof id !== 'string') continue
        if (key === 'artifact_ids') artifactIds.add(id)
        else evidenceIds.add(id)
      }
    }
    for (const nested of Object.values(record)) visit(nested, depth + 1)
  }
  visit(entry.details)
  return { artifactIds: [...artifactIds], evidenceIds: [...evidenceIds] }
}

function retrySelectionKey(threadId: string, runId: string): string {
  return `fieldnote:retry-selection:${threadId}:${runId}`
}

function eventSummary(event: RunEvent): string {
  const payload = event.payload ?? {}
  switch (event.type) {
    case 'status':
      return (
        safeText(payload.message) ??
        safeText(payload.status) ??
        'Updating run status'
      )
    case 'tool_started': {
      const name = safeText(payload.tool_name) ?? safeText(payload.name)
      return name ? `Started ${name}` : 'Started a source operation'
    }
    case 'tool_finished': {
      const name = safeText(payload.tool_name) ?? safeText(payload.name)
      return name ? `Finished ${name}` : 'Source operation finished'
    }
    case 'answer':
      return 'Answer ready'
    case 'error':
      return safeText(payload.message) ?? 'The run reported an error'
    case 'terminal':
      return labelForState(safeText(payload.state) ?? 'completed')
    default:
      return 'Run updated'
  }
}

function answerFromEvent(event: RunEvent): ChatMessage | null {
  const textValue =
    event.payload.text ?? event.payload.answer ?? event.payload.content
  const text =
    typeof textValue === 'string' && textValue.trim()
      ? textValue.trim().slice(0, 20_000)
      : undefined
  if (!text) return null
  const references = event.payload.references
  const referenceObject =
    references && typeof references === 'object'
      ? (references as Record<string, unknown>)
      : {}
  const evidenceValues =
    referenceObject.evidence_ids ?? event.payload.evidence_ids
  const artifactValues =
    referenceObject.artifact_ids ?? event.payload.artifact_ids
  const evidenceIds = Array.isArray(evidenceValues)
    ? evidenceValues.filter((item): item is string => typeof item === 'string')
    : []
  const artifactIds = Array.isArray(artifactValues)
    ? artifactValues.filter((item): item is string => typeof item === 'string')
    : []
  return {
    id: `event-${event.id}`,
    role: 'assistant',
    content: text,
    run_id: event.run_id,
    references: { evidence_ids: evidenceIds, artifact_ids: artifactIds },
  }
}

function SourceGlyph({ kind }: { kind: string }) {
  const structured = /csv|sheet|spreadsheet|table|database|json|parquet/i.test(
    kind,
  )
  return structured ? <FileSpreadsheet size={15} /> : <FileText size={15} />
}

function datasetLabel(dataset: DatasetSummary): string {
  if (typeof dataset.identity === 'string') return dataset.identity
  const values = Object.values(dataset.identity)
  return values.length ? values.map(String).join(' · ') : 'Dataset'
}

function effectiveDatasetIds(
  sourceIds: string[],
  selectedDatasetIds: string[],
  datasets: DatasetSummary[],
): string[] {
  const selected = new Set(selectedDatasetIds)
  const sourceHasSpecificSelection = sourceIds.some((sourceId) =>
    datasets.some(
      (dataset) => dataset.source_id === sourceId && selected.has(dataset.id),
    ),
  )
  if (!sourceHasSpecificSelection) return []
  return sourceIds.flatMap((sourceId) => {
    const available = datasets.filter(
      (dataset) => dataset.source_id === sourceId,
    )
    const specific = available.filter((dataset) => selected.has(dataset.id))
    return (specific.length ? specific : available).map((dataset) => dataset.id)
  })
}

function ChatPanel({
  threadId,
  sources,
  datasets,
  modelAvailable,
  modelMessage,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [runs, setRuns] = useState<AnalysisRun[]>([])
  const [artifactLists, setArtifactLists] = useState<
    Record<string, RunArtifact[]>
  >({})
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<string[]>([])
  const [language, setLanguage] = useState<AnswerLanguage>('en-IN')
  const [uiLanguage, setUiLanguage] = useUiLanguage()
  const [languageCapabilities, setLanguageCapabilities] =
    useState<LanguageCapabilities | null>(null)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [recording, setRecording] = useState(false)
  const [voiceMessage, setVoiceMessage] = useState('')
  const [draft, setDraft] = useState('')
  const [activeRun, setActiveRun] = useState<AnalysisRun | null>(null)
  const [progress, setProgress] = useState<ProgressItem[]>([])
  const [liveAnswer, setLiveAnswer] = useState<ChatMessage | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [retryingRunId, setRetryingRunId] = useState('')
  const [error, setError] = useState('')
  const [retrievalProfile, setRetrievalProfile] = useState<
    'basic' | 'advanced'
  >('basic')
  const [evidenceId, setEvidenceId] = useState('')
  const [evidence, setEvidence] = useState<EvidenceView | null>(null)
  const [loadingEvidence, setLoadingEvidence] = useState(false)
  const [evidenceError, setEvidenceError] = useState('')
  const [auditRunId, setAuditRunId] = useState('')
  const [auditPage, setAuditPage] = useState<AuditPage | null>(null)
  const [auditAction, setAuditAction] = useState('')
  const [auditState, setAuditState] = useState('')
  const [auditError, setAuditError] = useState('')
  const [optimisticMessage, setOptimisticMessage] =
    useState<ChatMessage | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const sequenceRef = useRef(0)
  const activeRunIdRef = useRef('')
  const retryInputsRef = useRef(new Map<string, RetryInput>())
  const selectedSourceIdsRef = useRef(selectedSourceIds)
  const draftsRef = useRef(new Map<string, string>())
  const threadIdRef = useRef(threadId)
  const mountedRef = useRef(true)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const audioTimeoutRef = useRef<number | null>(null)
  const transcriptionAbortRef = useRef<AbortController | null>(null)
  const voiceSequenceRef = useRef(0)
  const durationLimitHitRef = useRef(false)
  selectedSourceIdsRef.current = selectedSourceIds
  threadIdRef.current = threadId

  const copy = (key: UiTextKey) => uiText(uiLanguage, key)
  const maxAudioBytes =
    languageCapabilities?.audio_limits?.max_upload_bytes ??
    languageCapabilities?.stt.max_upload_bytes ??
    DEFAULT_MAX_AUDIO_BYTES
  const maxRecordingSeconds =
    languageCapabilities?.audio_limits?.max_duration_seconds ??
    languageCapabilities?.stt.max_duration_seconds ??
    DEFAULT_MAX_RECORDING_SECONDS

  function updateDraft(
    next: string | ((current: string) => string),
    ownerThread = threadIdRef.current,
  ) {
    setDraft((current) => {
      const currentForOwner =
        draftsRef.current.get(ownerThread) ??
        (threadIdRef.current === ownerThread ? current : '')
      const value = typeof next === 'function' ? next(currentForOwner) : next
      draftsRef.current.set(ownerThread, value)
      return threadIdRef.current === ownerThread ? value : current
    })
  }

  function releaseMicrophone() {
    if (audioTimeoutRef.current !== null) {
      window.clearTimeout(audioTimeoutRef.current)
      audioTimeoutRef.current = null
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    if (mountedRef.current) setRecording(false)
  }

  useEffect(() => {
    mountedRef.current = true
    const controller = new AbortController()
    languageApi
      .capabilities(controller.signal)
      .then((capabilities) => {
        if (!mountedRef.current) return
        setLanguageCapabilities(capabilities)
        if (!capabilities.languages.some((item) => item.tag === language)) {
          setLanguage(
            capabilities.languages.find((item) => item.tag === 'en-IN')?.tag ??
              capabilities.languages[0]?.tag ??
              'en-IN',
          )
        }
      })
      .catch(() => {
        // The chat remains usable when the optional language API is offline.
      })
    return () => {
      controller.abort()
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    const thisThread = threadId
    setDraft(draftsRef.current.get(thisThread) ?? '')
    voiceSequenceRef.current += 1
    transcriptionAbortRef.current?.abort()
    transcriptionAbortRef.current = null
    if (recorderRef.current?.state === 'recording') {
      try {
        recorderRef.current.stop()
      } catch {
        // The thread change still releases every microphone track below.
      }
    }
    releaseMicrophone()
    audioChunksRef.current = []
    setVoiceBusy(false)
    setVoiceMessage('')
    return () => {
      if (threadIdRef.current === thisThread) {
        voiceSequenceRef.current += 1
        transcriptionAbortRef.current?.abort()
        transcriptionAbortRef.current = null
        if (recorderRef.current?.state === 'recording') {
          try {
            recorderRef.current.stop()
          } catch {
            // Cleanup must continue even if MediaRecorder already stopped.
          }
        }
        releaseMicrophone()
      }
    }
  }, [threadId])

  async function openEvidence(id: string) {
    setEvidenceId(id)
    setEvidence(null)
    setEvidenceError('')
    setLoadingEvidence(true)
    try {
      setEvidence(await documentApi.evidence(id))
    } catch (reason) {
      setEvidenceError(
        reason instanceof Error
          ? reason.message
          : 'Could not resolve this evidence reference.',
      )
    } finally {
      setLoadingEvidence(false)
    }
  }

  const refreshHistory = useCallback(async () => {
    const [nextMessages, nextRuns] = await Promise.all([
      chatApi.messages(threadId),
      chatApi.runs(threadId),
    ])
    setMessages(nextMessages)
    setRuns(nextRuns)
    setOptimisticMessage(null)
    const current = nextRuns.find(isLive) ?? null
    setActiveRun(current)
    setLiveAnswer(null)
    setProgress([])
    activeRunIdRef.current = current?.id ?? ''
  }, [threadId])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setEvidenceId('')
    setEvidence(null)
    setEvidenceError('')
    setMessages([])
    setRuns([])
    setActiveRun(null)
    setLiveAnswer(null)
    setOptimisticMessage(null)
    setProgress([])
    activeRunIdRef.current = ''
    retryInputsRef.current.clear()
    Promise.all([chatApi.messages(threadId), chatApi.runs(threadId)])
      .then(([nextMessages, nextRuns]) => {
        if (!active) return
        setMessages(nextMessages)
        setRuns(nextRuns)
        const current = nextRuns.find(isLive) ?? null
        setActiveRun(current)
        activeRunIdRef.current = current?.id ?? ''
        for (const run of nextRuns) {
          if (run.state !== 'failed') continue
          const userMessage = [...nextMessages]
            .reverse()
            .find((item) => item.run_id === run.id && item.role === 'user')
          if (userMessage) {
            let saved: Partial<RetryInput> = {}
            try {
              saved = JSON.parse(
                sessionStorage.getItem(retrySelectionKey(threadId, run.id)) ??
                  '{}',
              )
            } catch {
              // Use the visible message and current source selection if browser storage is unavailable.
            }
            retryInputsRef.current.set(run.id, {
              text: userMessage.content,
              selectedSourceIds:
                run.selected_source_ids ?? saved.selectedSourceIds ?? [],
              selectedDatasetIds:
                run.selected_dataset_ids ?? saved.selectedDatasetIds ?? [],
              language: run.answer_language ?? saved.language ?? 'en-IN',
              retrievalProfile:
                run.retrieval_profile ?? saved.retrievalProfile ?? 'basic',
            })
          }
        }
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not load this conversation.',
          )
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [threadId])

  useEffect(() => {
    const allowed = new Set(sources.map((source) => source.id))
    setSelectedSourceIds((current) => current.filter((id) => allowed.has(id)))
  }, [sources])

  useEffect(() => {
    const checkedSources = new Set(selectedSourceIds)
    const allowed = new Set(
      datasets
        .filter((dataset) => checkedSources.has(dataset.source_id))
        .map((dataset) => dataset.id),
    )
    setSelectedDatasetIds((current) => current.filter((id) => allowed.has(id)))
  }, [datasets, selectedSourceIds])

  useEffect(() => {
    let active = true
    const runsWithArtifacts = new Set(
      messages
        .filter(
          (message) =>
            (message.references?.artifact_ids?.length ?? 0) > 0 &&
            message.run_id,
        )
        .map((message) => message.run_id as string),
    )
    const runIds = runs
      .filter(
        (run) =>
          (run.outcome?.artifact_ids?.length ?? 0) > 0 ||
          runsWithArtifacts.has(run.id),
      )
      .slice(0, 12)
      .map((run) => run.id)
    if (!runIds.length) {
      setArtifactLists({})
      return
    }
    Promise.all(
      runIds.map(async (runId) => {
        try {
          return [runId, await chatApi.artifacts(runId)] as const
        } catch {
          return [runId, []] as const
        }
      }),
    ).then((entries) => {
      if (active) setArtifactLists(Object.fromEntries(entries))
    })
    return () => {
      active = false
    }
  }, [messages, runs])

  useEffect(() => {
    if (!activeRun || !isLive(activeRun)) return
    const runId = activeRun.id
    sequenceRef.current = 0
    const close = chatApi.subscribe(
      runId,
      (event) => {
        if (
          activeRunIdRef.current !== runId ||
          event.run_id !== runId ||
          event.sequence <= sequenceRef.current
        )
          return
        sequenceRef.current = event.sequence
        setProgress((current) =>
          [
            ...current,
            { id: event.id, text: eventSummary(event), kind: event.type },
          ].slice(-5),
        )
        if (event.type === 'status') {
          const state = safeText(event.payload.state)
          const eventOutcome = event.payload.outcome
          const outcome =
            eventOutcome && typeof eventOutcome === 'object'
              ? (eventOutcome as Record<string, unknown>)
              : {}
          const cleanup =
            safeText(event.payload.cleanup) ?? safeText(outcome.cleanup)
          if (state || cleanup) {
            setActiveRun((current) => {
              if (!current || current.id !== runId) return current
              const next: AnalysisRun = {
                ...current,
                state: state ?? current.state,
                outcome: {
                  ...current.outcome,
                  cleanup: cleanup ?? current.outcome?.cleanup,
                },
              }
              return next
            })
          }
        }
        if (event.type === 'answer') {
          const answer = answerFromEvent(event)
          if (answer) setLiveAnswer(answer)
        }
        if (event.type === 'terminal') {
          void chatApi
            .run(runId)
            .then((finalRun) => {
              setRuns((current) => [
                finalRun,
                ...current.filter((item) => item.id !== runId),
              ])
              setActiveRun(null)
              activeRunIdRef.current = ''
              return refreshHistory()
            })
            .catch((reason: unknown) => {
              setError(
                reason instanceof Error
                  ? reason.message
                  : 'Could not load the completed run.',
              )
              setActiveRun(null)
            })
        }
      },
      () => {
        if (activeRunIdRef.current === runId) {
          setProgress((current) =>
            current.some((item) => item.id === 'reconnect')
              ? current
              : [
                  ...current,
                  {
                    id: 'reconnect',
                    text: 'Reconnecting to run updates',
                    kind: 'status',
                  },
                ].slice(-5),
          )
        }
      },
    )
    return close
  }, [activeRun?.id, refreshHistory])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, activeRun, liveAnswer, progress])

  const sortedMessages = useMemo(() => {
    const all = [...messages]
    if (
      optimisticMessage &&
      !all.some((item) => item.id === optimisticMessage.id)
    )
      all.push(optimisticMessage)
    return all
  }, [messages, optimisticMessage])

  const toggleSource = (sourceId: string) => {
    if (selectedSourceIds.includes(sourceId)) {
      setSelectedSourceIds((current) => current.filter((id) => id !== sourceId))
      setSelectedDatasetIds((current) =>
        current.filter(
          (datasetId) =>
            datasets.find((dataset) => dataset.id === datasetId)?.source_id !==
            sourceId,
        ),
      )
    } else {
      setSelectedSourceIds((current) => [...current, sourceId])
    }
  }

  const toggleDataset = (datasetId: string) => {
    setSelectedDatasetIds((current) =>
      current.includes(datasetId)
        ? current.filter((id) => id !== datasetId)
        : [...current, datasetId],
    )
  }

  async function sendMessage(
    text: string,
    retryFor?: string,
    explicitInput?: RetryInput,
  ) {
    const sendThreadId = threadIdRef.current
    const cleanText = text.trim()
    if (!cleanText || !modelAvailable || activeRun || sending) return
    setSending(true)
    setError('')
    const input: RetryInput = explicitInput ?? {
      text: cleanText,
      selectedSourceIds: [...selectedSourceIdsRef.current],
      selectedDatasetIds: effectiveDatasetIds(
        selectedSourceIdsRef.current,
        selectedDatasetIds,
        datasets,
      ),
      language,
      retrievalProfile,
    }
    try {
      const run = await chatApi.createRun(threadId, {
        text: input.text,
        selected_source_ids: input.selectedSourceIds,
        selected_dataset_ids: input.selectedDatasetIds,
        answer_language: input.language,
        retrieval_profile: input.retrievalProfile ?? 'basic',
        request_id: crypto.randomUUID(),
      })
      if (retryFor) retryInputsRef.current.delete(retryFor)
      retryInputsRef.current.set(run.id, input)
      try {
        sessionStorage.setItem(
          retrySelectionKey(threadId, run.id),
          JSON.stringify({
            selectedSourceIds: input.selectedSourceIds,
            selectedDatasetIds: input.selectedDatasetIds,
            language: input.language,
          }),
        )
      } catch {
        // Retry still works in the current view when browser storage is unavailable.
      }
      const userMessage: ChatMessage = {
        id: `pending-${run.id}`,
        role: 'user',
        content: cleanText,
        run_id: run.id,
        references: { evidence_ids: [], artifact_ids: [] },
      }
      setOptimisticMessage(userMessage)
      updateDraft('', sendThreadId)
      setRuns((current) => [
        run,
        ...current.filter((item) => item.id !== run.id),
      ])
      setActiveRun(isLive(run) ? run : null)
      activeRunIdRef.current = isLive(run) ? run.id : ''
      if (!isLive(run)) await refreshHistory()
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 503) {
        setError(
          reason.message || 'Chat is unavailable until a model is configured.',
        )
      } else {
        setError(
          reason instanceof Error
            ? reason.message
            : 'Could not start this run.',
        )
      }
    } finally {
      setSending(false)
    }
  }

  async function stopRun() {
    if (!activeRun || cancelling) return
    setCancelling(true)
    setError('')
    try {
      const stopped = await chatApi.cancelRun(activeRun.id)
      setRuns((current) => [
        stopped,
        ...current.filter((item) => item.id !== stopped.id),
      ])
      setActiveRun(stopped)
      activeRunIdRef.current = stopped.id
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not stop this run.',
      )
    } finally {
      setCancelling(false)
    }
  }

  async function retryRun(run: AnalysisRun) {
    if (activeRun || sending || retryingRunId) return
    let input = retryInputsRef.current.get(run.id)
    if (!input) {
      const userMessage = [...messages]
        .reverse()
        .find((item) => item.run_id === run.id && item.role === 'user')
      if (!userMessage) return
      let saved: Partial<RetryInput> = {}
      try {
        saved = JSON.parse(
          sessionStorage.getItem(retrySelectionKey(threadId, run.id)) ?? '{}',
        )
      } catch {
        // Retry from the stored user message with the current selection.
      }
      input = {
        text: userMessage.content,
        selectedSourceIds: run.selected_source_ids ??
          saved.selectedSourceIds ?? [...selectedSourceIdsRef.current],
        selectedDatasetIds: run.selected_dataset_ids ??
          saved.selectedDatasetIds ?? [...selectedDatasetIds],
        language: run.answer_language ?? saved.language ?? language,
        retrievalProfile:
          run.retrieval_profile ?? saved.retrievalProfile ?? 'basic',
      }
    }
    setRetryingRunId(run.id)
    updateDraft(input.text)
    setLanguage(input.language)
    setSelectedSourceIds(input.selectedSourceIds)
    setSelectedDatasetIds(input.selectedDatasetIds)
    await sendMessage(input.text, run.id, input)
    setRetryingRunId('')
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void sendMessage(draft)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void sendMessage(draft)
    }
  }

  async function transcribeAudio(blob: Blob, filename: string) {
    const originThread = threadIdRef.current
    if (blob.size > maxAudioBytes) {
      setVoiceMessage(copy('audioTooLarge'))
      return
    }
    const sequence = ++voiceSequenceRef.current
    const controller = new AbortController()
    transcriptionAbortRef.current?.abort()
    transcriptionAbortRef.current = controller
    setVoiceBusy(true)
    setVoiceMessage(copy('transcribing'))
    try {
      const result = await languageApi.transcribe(
        blob,
        filename,
        language,
        controller.signal,
      )
      if (
        !mountedRef.current ||
        threadIdRef.current !== originThread ||
        voiceSequenceRef.current !== sequence
      )
        return
      if (!result.text.trim()) {
        setVoiceMessage(copy('emptyTranscript'))
        return
      }
      updateDraft((current) =>
        current.trim()
          ? `${current.replace(/\s+$/, '')}\n\n${result.text.trim()}`
          : result.text.trim(),
      )
      setVoiceMessage(
        durationLimitHitRef.current
          ? `${copy('audioTooLong').replace('{seconds}', String(maxRecordingSeconds))} ${copy('transcriptionReady')}`
          : copy('transcriptionReady'),
      )
    } catch (reason) {
      if (
        controller.signal.aborted ||
        !mountedRef.current ||
        threadIdRef.current !== originThread ||
        voiceSequenceRef.current !== sequence
      )
        return
      setVoiceMessage(
        reason instanceof Error ? reason.message : copy('audioFailed'),
      )
    } finally {
      if (voiceSequenceRef.current === sequence && mountedRef.current) {
        setVoiceBusy(false)
        transcriptionAbortRef.current = null
      }
    }
  }

  async function startRecording() {
    if (voiceBusy || recording || !languageCapabilities?.stt.available) return
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setVoiceMessage(copy('unsupportedBrowser'))
      return
    }
    const originThread = threadIdRef.current
    const sequence = ++voiceSequenceRef.current
    setVoiceMessage('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (
        !mountedRef.current ||
        originThread !== threadIdRef.current ||
        sequence !== voiceSequenceRef.current
      ) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      audioChunksRef.current = []
      durationLimitHitRef.current = false
      const recorder = new MediaRecorder(stream)
      recorderRef.current = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size) audioChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const mimeType = recorder.mimeType || 'audio/webm'
        const blob = new Blob(audioChunksRef.current, { type: mimeType })
        audioChunksRef.current = []
        releaseMicrophone()
        if (
          blob.size &&
          originThread === threadIdRef.current &&
          sequence === voiceSequenceRef.current &&
          mountedRef.current
        ) {
          const extension = mimeType.includes('ogg') ? 'ogg' : 'webm'
          void transcribeAudio(blob, `recording.${extension}`)
        }
      }
      recorder.start()
      setRecording(true)
      audioTimeoutRef.current = window.setTimeout(() => {
        durationLimitHitRef.current = true
        if (recorder.state === 'recording') recorder.stop()
      }, maxRecordingSeconds * 1000)
    } catch (reason) {
      if (!mountedRef.current || originThread !== threadIdRef.current) return
      setVoiceMessage(
        reason instanceof DOMException && reason.name === 'NotAllowedError'
          ? copy('microphoneDenied')
          : reason instanceof Error
            ? reason.message
            : copy('audioFailed'),
      )
      releaseMicrophone()
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
  }

  function chooseAudio(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    if (file.size > maxAudioBytes) {
      setVoiceMessage(copy('audioTooLarge'))
      return
    }
    durationLimitHitRef.current = false
    void transcribeAudio(file, file.name || 'audio-upload')
  }

  const messageByRun = new Map<string, ChatMessage[]>()
  for (const message of sortedMessages) {
    if (!message.run_id) continue
    messageByRun.set(message.run_id, [
      ...(messageByRun.get(message.run_id) ?? []),
      message,
    ])
  }
  const latestFailedRun = runs.find((run) => run.state === 'failed')

  useEffect(() => {
    if (!auditRunId) return
    let active = true
    setAuditError('')
    void auditApi
      .page(auditRunId, auditAction, auditState)
      .then((page) => active && setAuditPage(page))
      .catch((reason: unknown) => {
        if (active)
          setAuditError(
            reason instanceof Error
              ? reason.message
              : 'Could not load run audit',
          )
      })
    return () => {
      active = false
    }
  }, [auditRunId, auditAction, auditState])

  return (
    <section className="chat-panel" aria-label={copy('conversation')}>
      <header className="chat-header">
        <div>
          <div className="chat-eyebrow">
            <span className="chat-eyebrow-rule" /> {copy('threadTranscript')}
          </div>
          <h2>{copy('conversation')}</h2>
        </div>
        <div className="chat-header-status">
          {activeRun ? labelForState(activeRun) : copy('savedThread')}
          <label className="ui-language-choice">
            <span className="sr-only">{copy('uiLanguage')}</span>
            <select
              value={uiLanguage}
              aria-label={copy('uiLanguage')}
              onChange={(event) => {
                const next = event.target.value as UiLanguage
                setUiLanguage(next)
              }}
            >
              <option value="en">EN</option>
              <option value="hi">हिन्दी</option>
            </select>
          </label>
        </div>
      </header>

      {!modelAvailable && (
        <div className="chat-model-block" role="status">
          <span className="chat-model-icon">
            <Sparkles size={16} />
          </span>
          <div>
            <strong>{copy('unavailable')}</strong>
            <p>
              {modelMessage ||
                'Configure a model endpoint and credentials to start a run.'}
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="chat-error" role="alert">
          <AlertCircle size={15} />
          <span>{error}</span>
          <button onClick={() => setError('')} aria-label="Dismiss error">
            ×
          </button>
        </div>
      )}

      <div className="chat-transcript" aria-live="polite">
        {loading ? (
          <div className="chat-loading">
            <LoaderCircle size={17} className="spin" /> Loading saved
            conversation
          </div>
        ) : sortedMessages.length === 0 && !activeRun ? (
          <div className="chat-empty">
            <div className="chat-empty-glyph">
              <Globe2 size={17} />
            </div>
            <span className="mini-label">A question to begin</span>
            <p>
              Ask about the sources attached to this workspace. Each answer will
              keep its evidence and files with the thread.
            </p>
          </div>
        ) : (
          <div className="message-list">
            {sortedMessages.map((message) => {
              const run = message.run_id
                ? runs.find((item) => item.id === message.run_id)
                : undefined
              const related = message.run_id
                ? (messageByRun.get(message.run_id) ?? [])
                : []
              const artifacts = message.references?.artifact_ids ?? []
              return (
                <div className={`message-row ${message.role}`} key={message.id}>
                  {message.role === 'assistant' && (
                    <span className="assistant-seal">
                      <Sparkles size={13} />
                    </span>
                  )}
                  <article className={`message-card ${message.role}`}>
                    <div className="message-topline">
                      <span>
                        {message.role === 'user'
                          ? 'YOU'
                          : message.role === 'assistant'
                            ? 'ASSISTANT'
                            : 'SYSTEM'}
                      </span>
                      {run && (
                        <span className={`message-run-state ${run.state}`}>
                          {labelForState(run)}
                        </span>
                      )}
                    </div>
                    <div className="message-content">
                      <MarkdownRenderer
                        content={message.content}
                        artifactIds={message.references?.artifact_ids ?? []}
                        evidenceIds={message.references?.evidence_ids ?? []}
                        onOpenEvidence={openEvidence}
                      />
                    </div>
                    {!!message.references?.evidence_ids?.length &&
                      !hasInlineEvidence(message.content) && (
                        <div className="evidence-references">
                          <ShieldCheck size={12} />
                          <span>Evidence</span>
                          {message.references.evidence_ids.map((id, index) => (
                            <button
                              className="evidence-id"
                              key={id}
                              type="button"
                              onClick={() => void openEvidence(id)}
                            >
                              [{index + 1}]
                            </button>
                          ))}
                        </div>
                      )}
                    {artifacts.length > 0 && (
                      <ArtifactLinks
                        artifacts={artifactLists[message.run_id ?? ''] ?? []}
                        ids={artifacts}
                      />
                    )}
                    {run && message.role === 'assistant' && (
                      <>
                        {run.outcome?.warnings?.map((warning) => (
                          <p
                            className="answer-warning"
                            key={warning.code}
                            role="note"
                          >
                            {warning.message}
                          </p>
                        ))}
                      </>
                    )}
                    {run && message.role === 'user' && (
                      <>
                        <button
                          className="run-audit-trigger"
                          type="button"
                          onClick={() => {
                            setAuditRunId((current) =>
                              current === run.id ? '' : run.id,
                            )
                            setAuditPage(null)
                          }}
                          aria-expanded={auditRunId === run.id}
                        >
                          <ListChecks size={14} />{' '}
                          {auditRunId === run.id
                            ? 'Hide run audit'
                            : 'Run audit'}
                        </button>
                        {auditRunId === run.id && (
                          <section className="run-audit" aria-label="Run audit">
                            <div className="run-audit-heading">
                              <strong>Run history</strong>
                              <a
                                href={auditApi.exportUrl(run.id)}
                                target="_blank"
                                rel="noreferrer"
                              >
                                Export JSON
                              </a>
                            </div>
                            <div className="run-audit-filters">
                              <label>
                                Action{' '}
                                <input
                                  value={auditAction}
                                  onChange={(event) =>
                                    setAuditAction(event.target.value)
                                  }
                                  placeholder="All actions"
                                />
                              </label>
                              <label>
                                State{' '}
                                <input
                                  value={auditState}
                                  onChange={(event) =>
                                    setAuditState(event.target.value)
                                  }
                                  placeholder="All states"
                                />
                              </label>
                            </div>
                            {auditError ? (
                              <p role="alert">{auditError}</p>
                            ) : auditPage ? (
                              <ol>
                                {auditPage.entries.map((entry: AuditEntry) => {
                                  const refs = auditReferences(entry)
                                  return (
                                    <li key={`${entry.kind}-${entry.id}`}>
                                      <span>{entry.action}</span>
                                      <small>
                                        {entry.state} · {entry.kind}
                                      </small>
                                      {!!refs.evidenceIds.length && (
                                        <button
                                          type="button"
                                          onClick={() =>
                                            void openEvidence(
                                              refs.evidenceIds[0],
                                            )
                                          }
                                        >
                                          Open citation
                                        </button>
                                      )}
                                      {!!refs.artifactIds.length && (
                                        <ArtifactLinks
                                          artifacts={
                                            artifactLists[run.id] ?? []
                                          }
                                          ids={refs.artifactIds}
                                        />
                                      )}
                                    </li>
                                  )
                                })}
                                {!auditPage.entries.length && (
                                  <li>No matching audit entries.</li>
                                )}
                              </ol>
                            ) : (
                              <p>Loading run history…</p>
                            )}
                          </section>
                        )}
                      </>
                    )}
                  </article>
                  {message.role === 'user' &&
                    run?.state === 'failed' &&
                    !related.some((item) => item.role === 'assistant') && (
                      <RunFailure
                        run={run}
                        retrying={retryingRunId === run.id}
                        onRetry={() => void retryRun(run)}
                      />
                    )}
                </div>
              )
            })}
            {liveAnswer &&
              !messages.some(
                (item) =>
                  item.id === liveAnswer.id ||
                  (item.run_id === liveAnswer.run_id &&
                    item.role === 'assistant'),
              ) && (
                <MessageBubble
                  message={liveAnswer}
                  artifacts={artifactLists[liveAnswer.run_id ?? ''] ?? []}
                  onOpenEvidence={openEvidence}
                />
              )}
          </div>
        )}

        {activeRun && (
          <div className="run-progress" aria-label="Run progress">
            <div className="run-progress-head">
              <LoaderCircle size={14} className="spin" />
              <strong>{labelForState(activeRun)}</strong>
              {activeRun.state !== 'cancelled' && (
                <button
                  className="stop-run"
                  onClick={() => void stopRun()}
                  disabled={cancelling}
                >
                  <CircleStop size={14} />
                  {cancelling ? 'Stopping' : 'Stop'}
                </button>
              )}
            </div>
            {!!progress.length && (
              <ol>
                {progress.map((item) => (
                  <li key={item.id} className={item.kind}>
                    <span />
                    {item.text}
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
        {latestFailedRun &&
          !sortedMessages.some(
            (message) =>
              message.run_id === latestFailedRun.id && message.role === 'user',
          ) && (
            <RunFailure
              run={latestFailedRun}
              retrying={retryingRunId === latestFailedRun.id}
              onRetry={() => void retryRun(latestFailedRun)}
            />
          )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-compose-area">
        {sources.length > 0 && (
          <details className="source-selector">
            <summary>
              <span className="source-selector-mark">
                <Check size={12} />
              </span>
              <span>Use sources</span>
              <span className="selected-source-count">
                {selectedSourceIds.length} sources ·{' '}
                {selectedDatasetIds.length
                  ? `${selectedDatasetIds.length} sheets`
                  : 'all sheets'}
              </span>
              <ArrowDown size={13} className="selector-chevron" />
            </summary>
            <div className="source-check-list">
              {sources.map((source) => {
                const checked = selectedSourceIds.includes(source.id)
                const sourceDatasets = datasets.filter(
                  (dataset) => dataset.source_id === source.id,
                )
                return (
                  <div className="source-check-group" key={source.id}>
                    <label className="source-check-row">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSource(source.id)}
                      />
                      <span className="source-check-icon">
                        <SourceGlyph kind={source.kind} />
                      </span>
                      <span className="source-check-copy">
                        <strong>{source.display_name}</strong>
                        <small>
                          {sourceKindLabel(source.kind)} · v{source.version}
                        </small>
                      </span>
                      <span
                        className={`source-check-state ${source.state.toLowerCase()}`}
                      >
                        {source.state}
                      </span>
                    </label>
                    {checked && sourceDatasets.length > 0 && (
                      <div className="dataset-check-list">
                        <div className="dataset-check-heading">
                          Choose sheets or tables <span>optional</span>
                        </div>
                        {sourceDatasets.map((dataset) => (
                          <label className="dataset-check-row" key={dataset.id}>
                            <input
                              type="checkbox"
                              checked={selectedDatasetIds.includes(dataset.id)}
                              onChange={() => toggleDataset(dataset.id)}
                            />
                            <span>{datasetLabel(dataset)}</span>
                            <small>{dataset.designation}</small>
                          </label>
                        ))}
                        {!selectedDatasetIds.some((id) =>
                          sourceDatasets.some((dataset) => dataset.id === id),
                        ) && (
                          <p className="dataset-check-hint">
                            All sheets stay available to the analyst.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </details>
        )}
        <form
          className={`chat-composer ${!modelAvailable ? 'disabled' : ''}`}
          onSubmit={submit}
        >
          <textarea
            aria-label={copy('askSources')}
            placeholder={
              modelAvailable ? copy('askPlaceholder') : copy('configureModel')
            }
            value={draft}
            onChange={(event) => updateDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            maxLength={12000}
            disabled={!modelAvailable || !!activeRun || loading}
          />
          <div className="composer-bottom">
            <div className="composer-tools">
              <label className="language-choice">
                <Globe2 size={13} />
                <span className="sr-only">{copy('answerLanguage')}</span>
                <select
                  value={language}
                  onChange={(event) =>
                    setLanguage(event.target.value as AnswerLanguage)
                  }
                  disabled={
                    !modelAvailable || !languageCapabilities?.languages.length
                  }
                >
                  {(
                    languageCapabilities?.languages ?? [
                      { tag: 'en-IN', name: 'English' },
                      { tag: 'hi-IN', name: 'हिन्दी' },
                    ]
                  ).map((item) => (
                    <option value={item.tag} key={item.tag}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label
                className="language-choice"
                title="Advanced searches keyword variants and expands surrounding passages"
              >
                <span className="sr-only">{copy('retrievalProfile')}</span>
                <select
                  value={retrievalProfile}
                  onChange={(event) =>
                    setRetrievalProfile(
                      event.target.value as 'basic' | 'advanced',
                    )
                  }
                  disabled={!!activeRun}
                >
                  <option value="basic">{copy('basicRetrieval')}</option>
                  <option value="advanced">{copy('advancedRetrieval')}</option>
                </select>
              </label>
              <span className="composer-hint">
                {activeRun ? copy('activeRun') : copy('enterToSend')}
              </span>
            </div>
            <button
              className="send-button"
              type="submit"
              disabled={
                !modelAvailable ||
                !draft.trim() ||
                sending ||
                !!activeRun ||
                loading
              }
              aria-label={copy('sendMessage')}
            >
              {sending ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <ArrowUp size={16} />
              )}
            </button>
          </div>
          <div className="voice-controls" aria-label={copy('voice')}>
            {languageCapabilities?.stt.available ? (
              <>
                <button
                  className={`voice-button ${recording ? 'recording' : ''}`}
                  type="button"
                  disabled={
                    voiceBusy || !modelAvailable || !!activeRun || loading
                  }
                  onClick={
                    recording ? stopRecording : () => void startRecording()
                  }
                  aria-label={
                    recording ? copy('stopRecording') : copy('startRecording')
                  }
                >
                  {recording ? <Square size={13} /> : <Mic size={14} />}
                  {recording ? copy('stopRecording') : copy('startRecording')}
                </button>
                <label className="voice-button">
                  <input
                    type="file"
                    accept="audio/*"
                    onChange={chooseAudio}
                    disabled={
                      voiceBusy || !modelAvailable || !!activeRun || loading
                    }
                  />
                  {voiceBusy ? (
                    <LoaderCircle size={14} className="spin" />
                  ) : (
                    <FileText size={14} />
                  )}
                  {voiceBusy ? copy('transcribing') : copy('chooseAudio')}
                </label>
              </>
            ) : (
              <span
                className="voice-capability-unavailable"
                title={languageCapabilities?.stt.reason ?? ''}
              >
                <Mic size={13} /> {copy('sttUnavailable')}
              </span>
            )}
            {recording && (
              <span className="voice-live-status">
                {copy('recording').replace(
                  '{seconds}',
                  String(maxRecordingSeconds),
                )}
              </span>
            )}
            {voiceMessage && (
              <span className="voice-message" role="status">
                {voiceMessage}
              </span>
            )}
          </div>
          {languageCapabilities &&
            (!languageCapabilities.translation.available ||
              !languageCapabilities.tts.available) && (
              <div className="voice-optional-status">
                {!languageCapabilities.translation.available &&
                  copy('translationUnavailable')}
                {!languageCapabilities.translation.available &&
                  !languageCapabilities.tts.available &&
                  ' · '}
                {!languageCapabilities.tts.available && copy('ttsUnavailable')}
              </div>
            )}
        </form>
      </div>
      {evidenceId && (
        <EvidenceViewer
          evidenceId={evidenceId}
          evidence={evidence}
          loading={loadingEvidence}
          error={evidenceError}
          onClose={() => setEvidenceId('')}
        />
      )}
    </section>
  )
}

function hasInlineEvidence(content: string): boolean {
  return /\[evidence:[0-9a-f-]{36}\]/i.test(content)
}

function locationText(
  location: Record<string, unknown> | null | undefined,
): string {
  if (!location) return 'Location not provided'
  const parts = Object.entries(location)
    .filter(
      ([, value]) => value !== null && value !== undefined && value !== '',
    )
    .map(([key, value]) => `${key.replaceAll('_', ' ')} ${String(value)}`)
  return parts.join(' · ') || 'Location not provided'
}

function EvidenceViewer({
  evidenceId,
  evidence,
  loading,
  error,
  onClose,
}: {
  evidenceId: string
  evidence: EvidenceView | null
  loading: boolean
  error: string
  onClose: () => void
}) {
  const details = evidence?.details ?? {}
  const trace = evidence?.trace ?? details.trace ?? details.retrieval_trace
  const structuredDetails = [
    ['Query', details.query],
    ['Source versions', details.source_versions],
    ['Result hash', details.result_sha256],
    ['Result artifact', details.result_artifact_id ?? details.artifact_id],
  ].filter(([, value]) => value !== null && value !== undefined)
  return (
    <aside
      className="evidence-viewer"
      role="dialog"
      aria-label="Evidence reference"
    >
      <header className="evidence-viewer-header">
        <div>
          <span className="mini-label">SOURCE EVIDENCE</span>
          <strong>
            {evidence?.display_name ?? `Evidence ${evidenceId.slice(0, 8)}`}
          </strong>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close evidence viewer"
        >
          <X size={15} />
        </button>
      </header>
      <div className="evidence-viewer-body">
        {loading ? (
          <div className="evidence-viewer-state">
            <LoaderCircle size={15} className="spin" /> Resolving saved evidence
          </div>
        ) : error ? (
          <div className="evidence-viewer-state error" role="alert">
            <AlertCircle size={14} /> {error}
          </div>
        ) : evidence ? (
          <>
            <div className="evidence-source-meta">
              <span>{evidence.kind?.replaceAll('_', ' ') ?? 'Evidence'}</span>
              {evidence.document_version !== null &&
                evidence.document_version !== undefined && (
                  <span>Document v{evidence.document_version}</span>
                )}
              {evidence.retrieval_mode && (
                <span>{evidence.retrieval_mode}</span>
              )}
            </div>
            {!!evidence.source_ids.length && (
              <div className="evidence-source-ids">
                Sources{' '}
                {evidence.source_ids.map((id) => id.slice(0, 8)).join(' · ')}
              </div>
            )}
            {evidence.source_state === 'archived' && (
              <div className="evidence-archived-notice" role="status">
                This source has been archived. This saved citation still points
                to its original evidence.
              </div>
            )}
            <div className="evidence-location">
              {locationText(evidence.location)}
            </div>
            {evidence.excerpt ? (
              <blockquote className="evidence-excerpt">
                {evidence.excerpt}
              </blockquote>
            ) : (
              <p className="evidence-empty-excerpt">
                This calculation evidence does not contain a document passage.
              </p>
            )}
            {evidence.context && evidence.context !== evidence.excerpt && (
              <details className="evidence-context">
                <summary>Wider passage context</summary>
                <p>{evidence.context}</p>
              </details>
            )}
            {(typeof evidence.score === 'number' ||
              typeof evidence.rank === 'number') && (
              <div className="evidence-retrieval-meta">
                {typeof evidence.rank === 'number' && (
                  <span>Rank {evidence.rank}</span>
                )}
                {typeof evidence.score === 'number' && (
                  <span>Score {evidence.score.toFixed(3)}</span>
                )}
              </div>
            )}
            {!!structuredDetails.length && (
              <section className="evidence-trace-section">
                <span className="mini-label">CALCULATION RECORD</span>
                <dl>
                  {structuredDetails.map(([label, value]) => (
                    <div key={String(label)}>
                      <dt>{String(label)}</dt>
                      <dd>
                        {typeof value === 'string'
                          ? value
                          : JSON.stringify(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}
            {trace !== undefined && trace !== null && (
              <details className="evidence-trace">
                <summary>Retrieval trace</summary>
                <pre>
                  {typeof trace === 'string'
                    ? trace.slice(0, 6000)
                    : JSON.stringify(trace, null, 2).slice(0, 6000)}
                </pre>
              </details>
            )}
            <small className="evidence-id-full">Reference {evidence.id}</small>
          </>
        ) : null}
      </div>
    </aside>
  )
}

function MessageBubble({
  message,
  artifacts,
  onOpenEvidence,
}: {
  message: ChatMessage
  artifacts: RunArtifact[]
  onOpenEvidence: (id: string) => void
}) {
  return (
    <div className="message-row assistant">
      <span className="assistant-seal">
        <Sparkles size={13} />
      </span>
      <article className="message-card assistant">
        <div className="message-topline">
          <span className="message-run-state running">Answer</span>
        </div>
        <div className="message-content">
          <MarkdownRenderer
            content={message.content}
            artifactIds={message.references?.artifact_ids ?? []}
            evidenceIds={message.references?.evidence_ids ?? []}
            onOpenEvidence={onOpenEvidence}
          />
        </div>
        {!!message.references?.evidence_ids?.length &&
          !hasInlineEvidence(message.content) && (
            <div className="evidence-references">
              <ShieldCheck size={12} />
              <span>Evidence</span>
              {message.references.evidence_ids.map((id, index) => (
                <button
                  className="evidence-id"
                  key={id}
                  type="button"
                  onClick={() => onOpenEvidence(id)}
                >
                  [{index + 1}]
                </button>
              ))}
            </div>
          )}
        {!!message.references?.artifact_ids?.length && (
          <ArtifactLinks
            artifacts={artifacts}
            ids={message.references.artifact_ids}
          />
        )}
      </article>
    </div>
  )
}

function ArtifactLinks({
  artifacts,
  ids,
}: {
  artifacts: RunArtifact[]
  ids: string[]
}) {
  return (
    <div className="artifact-list" aria-label="Run artifacts">
      {ids.map((id) => {
        const artifact = artifacts.find((item) => item.id === id)
        return <ArtifactItem key={id} id={id} artifact={artifact} />
      })}
    </div>
  )
}

function ArtifactItem({
  id,
  artifact,
}: {
  id: string
  artifact?: RunArtifact
}) {
  return (
    <InlineArtifactPreview
      id={id}
      caption={artifact?.display_name}
      initiallyExpanded={false}
    />
  )
}

function RunFailure({
  run,
  retrying,
  onRetry,
}: {
  run: AnalysisRun
  retrying: boolean
  onRetry: () => void
}) {
  return (
    <div className="run-failure">
      <span className="failure-icon">
        <AlertCircle size={14} />
      </span>
      <div>
        <strong>{labelForState(run)}</strong>
        <p>
          {safeText(run.outcome?.text) ?? 'This run did not produce an answer.'}
        </p>
      </div>
      {run.state === 'failed' && (
        <button
          onClick={onRetry}
          disabled={retrying}
          aria-label="Retry this run"
        >
          <RotateCcw size={13} />
          {retrying ? 'Retrying' : 'Retry'}
        </button>
      )}
    </div>
  )
}

export default ChatPanel

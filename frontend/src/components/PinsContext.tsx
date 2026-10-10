import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  pinsApi,
  type SavedPin,
  type PinFields,
  type PinKind,
} from '../pinsApi'

type PinsState = {
  pins: SavedPin[]
  loading: boolean
  error: string
  refresh: () => Promise<void>
  save: (
    kind: PinKind,
    targetId: string,
    fields: PinFields,
    pinId?: string,
  ) => Promise<void>
  remove: (id: string) => Promise<void>
}
const PinsContext = createContext<PinsState | null>(null)
export const usePins = () => useContext(PinsContext)

export function PinsProvider({
  workspaceId,
  resourceKey = '',
  children,
}: {
  workspaceId: string
  resourceKey?: string
  children: ReactNode
}) {
  const [pins, setPins] = useState<SavedPin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  const refresh = useCallback(async () => {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setLoading(true)
    setError('')
    try {
      const all: SavedPin[] = []
      if (workspaceId) {
        let page: SavedPin[]
        do {
          page = await pinsApi.list(workspaceId, all.length, controller.signal)
          all.push(...page)
        } while (page.length === 200)
      }
      if (!controller.signal.aborted) setPins(all)
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(
          reason instanceof Error ? reason.message : 'Could not load pins.',
        )
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [workspaceId])
  useEffect(() => {
    void refresh()
    return () => pending.current?.abort()
  }, [refresh, resourceKey])
  const save = async (
    kind: PinKind,
    targetId: string,
    fields: PinFields,
    pinId?: string,
  ) => {
    const pin = pinId
      ? await pinsApi.update(pinId, fields)
      : await pinsApi.create(workspaceId, kind, targetId, fields)
    // Cancel an older list request so it cannot overwrite a successful mutation.
    pending.current?.abort()
    setLoading(false)
    setError('')
    setPins((items) => [pin, ...items.filter((item) => item.id !== pin.id)])
  }
  const remove = async (id: string) => {
    await pinsApi.remove(id)
    pending.current?.abort()
    setLoading(false)
    setError('')
    setPins((items) => items.filter((item) => item.id !== id))
  }
  return (
    <PinsContext.Provider
      value={{ pins, loading, error, refresh, save, remove }}
    >
      {children}
    </PinsContext.Provider>
  )
}

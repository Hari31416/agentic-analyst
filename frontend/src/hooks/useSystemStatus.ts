import { useCallback, useEffect, useState } from 'react'
import { SystemReadiness } from '../components/RightSidebar'

export type HealthStatus = 'checking' | 'online' | 'offline'

export function useSystemStatus(): {
  health: HealthStatus
  readiness: SystemReadiness | null
  refreshStatus: () => Promise<void>
} {
  const [health, setHealth] = useState<HealthStatus>('checking')
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null)

  const refreshStatus = useCallback(async () => {
    try {
      const [healthRes, readinessRes] = await Promise.allSettled([
        apiFetch('/api/health'),
        apiFetch('/api/readiness'),
      ])

      if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
        setHealth('online')
      } else {
        setHealth('offline')
      }

      if (readinessRes.status === 'fulfilled' && readinessRes.value.ok) {
        const data = (await readinessRes.value.json()) as SystemReadiness
        setReadiness(data)
      } else {
        setReadiness(null)
      }
    } catch {
      setHealth('offline')
      setReadiness(null)
    }
  }, [])

  useEffect(() => {
    void refreshStatus()
  }, [refreshStatus])

  return { health, readiness, refreshStatus }
}
import { apiFetch } from '../lib/apiFetch'

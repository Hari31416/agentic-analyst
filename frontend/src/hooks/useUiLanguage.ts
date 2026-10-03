import { useCallback, useEffect, useState } from 'react'
import { UiLanguage } from '../uiText'

const STORAGE_KEY = 'fieldnote:ui-language'
const CHANGE_EVENT = 'fieldnote:ui-language-change'

function readUiLanguage(): UiLanguage {
  return localStorage.getItem(STORAGE_KEY) === 'hi' ? 'hi' : 'en'
}

export function useUiLanguage(): [UiLanguage, (language: UiLanguage) => void] {
  const [language, setLanguage] = useState<UiLanguage>(readUiLanguage)

  useEffect(() => {
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<UiLanguage>).detail
      if (next === 'en' || next === 'hi') setLanguage(next)
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) setLanguage(readUiLanguage())
    }
    window.addEventListener(CHANGE_EVENT, onChange)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  const updateLanguage = useCallback((next: UiLanguage) => {
    localStorage.setItem(STORAGE_KEY, next)
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: next }))
  }, [])

  return [language, updateLanguage]
}

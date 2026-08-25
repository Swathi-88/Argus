import { useCallback, useEffect, useState } from 'react'

type Theme = 'light' | 'dark'

const STORAGE_KEY = 'rtc.theme'

/**
 * Theme toggle backed by the `data-theme` stamp on <html>.
 *
 * The initial value is applied by an inline script in index.html before first
 * paint, so this hook only reads what is already there — no flash on reload.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (document.documentElement.dataset.theme as Theme) || 'light',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* private mode — the stamp still applies for this session */
    }
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((current) => (current === 'light' ? 'dark' : 'light'))
  }, [])

  return { theme, toggle }
}

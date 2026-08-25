import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from './api'

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

/**
 * Minimal data-fetching hook: run on mount and whenever `deps` change, with a
 * `reload` for imperative refresh.
 *
 * Results from a superseded call are discarded by generation counter rather than
 * AbortController, because several of these calls are non-idempotent reads whose
 * server-side audit record we do want written even if the UI moved on.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  options: { enabled?: boolean } = {},
): AsyncState<T> & { reload: () => void; setData: (value: T) => void } {
  const enabled = options.enabled ?? true

  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: enabled,
    error: null,
  })

  const generation = useRef(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const run = useCallback(() => {
    if (!enabled) {
      setState({ data: null, loading: false, error: null })
      return
    }

    const current = ++generation.current
    setState((previous) => ({ ...previous, loading: true, error: null }))

    loaderRef
      .current()
      .then((data) => {
        if (generation.current === current) setState({ data, loading: false, error: null })
      })
      .catch((error: unknown) => {
        if (generation.current !== current) return
        const message =
          error instanceof ApiError
            ? error.message
            : error instanceof Error
              ? error.message
              : 'Unexpected error'
        setState({ data: null, loading: false, error: message })
      })
  }, [enabled])

  useEffect(run, [run, ...deps])

  const setData = useCallback((value: T) => {
    setState({ data: value, loading: false, error: null })
  }, [])

  return { ...state, reload: run, setData }
}

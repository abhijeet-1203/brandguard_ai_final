import { useCallback, useEffect, useRef, useState } from 'react'
import { extractError, listRows, sql, Row } from './api'

/** Guarded value for SQL interpolation (workspace ids come from our own records). */
export function safeId(value?: string | null): string {
  const v = String(value ?? '')
  if (!/^[0-9a-fA-F-]{36}$/.test(v)) throw new Error('Invalid identifier')
  return v
}

export function useSqlQuery<T = Row>(query: string | null, deps: unknown[] = []) {
  const [items, setItems] = useState<T[]>([])
  const [loading, setLoading] = useState<boolean>(Boolean(query))
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const depsKey = JSON.stringify(deps)

  useEffect(() => {
    let cancelled = false
    if (!query) { setItems([]); setLoading(false); return }
    setLoading(true)
    sql<T>(query)
      .then((rows) => { if (!cancelled) { setItems(rows); setError(null) } })
      .catch((e) => { if (!cancelled) setError(extractError(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, tick, depsKey])

  return { items, loading, error, refresh: useCallback(() => setTick((t) => t + 1), []) }
}

export function useRows<T = Row>(table: string, opts: {
  filters?: { field: string; op?: string; value: unknown }[]
  sort?: { field: string; direction?: 'asc' | 'desc' }[]
  limit?: number
} = {}, deps: unknown[] = []) {
  const [items, setItems] = useState<T[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const depsKey = JSON.stringify(deps)
  const optsKey = JSON.stringify(opts)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listRows<T>(table, opts)
      .then((rows) => { if (!cancelled) { setItems(rows); setError(null) } })
      .catch((e) => { if (!cancelled) setError(extractError(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, optsKey, tick, depsKey])

  return { items, loading, error, refresh: useCallback(() => setTick((t) => t + 1), []) }
}

/** Bounded status poll for one review while its analysis is in flight. */
export function useAnalysisWatch(reviewId: string | undefined, active: boolean, onDone: () => void, maxMs = 240000) {
  const started = useRef<number>(0)
  useEffect(() => {
    if (!reviewId || !active) return
    started.current = Date.now()
    const handle = window.setInterval(async () => {
      if (Date.now() - started.current > maxMs) { window.clearInterval(handle); onDone(); return }
      try {
        const rows = await sql<Row>(`select status, analysis_status from bg_reviews where id = '${safeId(reviewId)}'`)
        const row = rows[0]
        if (row && row.status !== 'analysing' && row.analysis_status !== 'running') {
          window.clearInterval(handle)
          onDone()
        }
      } catch {
        window.clearInterval(handle)
      }
    }, 3000)
    return () => window.clearInterval(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewId, active])
}

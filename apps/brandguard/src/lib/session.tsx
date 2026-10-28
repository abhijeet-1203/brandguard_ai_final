import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { lemmaClient } from '../lemma-client'
import { extractError, listRows, runFn, sql, Row } from './api'
import { CAPABILITIES, Capability, Role } from './types'

export type SessionData = {
  user: Row | null
  email: string
  orgs: Row[]
  memberships: Row[]
  unread: number
}

type SessionValue = SessionData & {
  ready: boolean
  stage: string
  bootError: string | null
  activeOrg: Row | null
  role: Role | null
  membership: Row | null
  can: (cap: Capability) => boolean
  setActiveOrgId: (id: string) => void
  refresh: () => Promise<void>
  refreshNotifications: () => Promise<void>
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${label} did not respond within ${Math.round(ms / 1000)} seconds.`)), ms)),
  ])
}

/** Session bootstrap runs once per page load, outside React, so an AuthGuard
 *  re-render can never restart it (or lose the result). */
let bootstrapPromise: Promise<SessionData> | null = null
let stageReporter: ((stage: string) => void) | null = null
const report = (stage: string) => { stageReporter?.(stage) }

async function fetchSession(): Promise<SessionData> {
  report('checking your session')
  const state: any = await withTimeout(lemmaClient.initialize(), 20000, 'The sign-in check')
  const user = state?.user ?? null
  const email: string = user?.email ?? ''
  if (!email) throw new Error('The platform did not report a signed-in user for this app.')

  report('preparing the workspace')
  const boot = await withTimeout(runFn('bg_bootstrap', {}), 40000, 'Workspace preparation')
  if (!boot.ok) throw new Error(boot.error ?? 'The workspace could not be prepared.')

  report('loading workspaces and roles')
  const [orgs, memberships] = await Promise.all([
    withTimeout(listRows('bg_organizations', { limit: 50 }), 25000, 'Loading workspaces'),
    withTimeout(listRows('bg_memberships', { filters: [{ field: 'user_email', op: 'eq', value: email }], limit: 50 }), 25000, 'Loading your roles'),
  ])
  // Most active workspace first, so the switcher opens on the workspace in use.
  let activity: Record<string, number> = {}
  try {
    const counts = await withTimeout(sql<Row>('select organization_id, count(*) as n from bg_reviews group by 1'), 20000, 'Ranking workspaces')
    activity = Object.fromEntries(counts.map((c) => [String(c.organization_id), Number(c.n)]))
  } catch { activity = {} }
  orgs.sort((a, b) => (activity[String(b.id)] ?? 0) - (activity[String(a.id)] ?? 0) || String(a.name).localeCompare(String(b.name)))

  report('loading notifications')
  let unread = 0
  try {
    const notes = await withTimeout(listRows('bg_notifications', {
      filters: [{ field: 'user_email', op: 'eq', value: email }, { field: 'is_read', op: 'eq', value: false }],
      limit: 100,
    }), 20000, 'Loading notifications')
    unread = notes.length
  } catch { unread = 0 }

  report('ready')
  return { user, email, orgs, memberships, unread }
}

export function ensureSession(force = false): Promise<SessionData> {
  if (force || !bootstrapPromise) bootstrapPromise = fetchSession()
  return bootstrapPromise
}

const SessionContext = createContext<SessionValue | null>(null)
const STORAGE_KEY = 'brandguard.activeOrg'

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<SessionData | null>(null)
  const [ready, setReady] = useState(false)
  const [stage, setStage] = useState('starting')
  const [bootError, setBootError] = useState<string | null>(null)
  const [activeOrgId, setActiveOrgIdState] = useState<string>(() => localStorage.getItem(STORAGE_KEY) ?? '')

  useEffect(() => {
    let cancelled = false
    stageReporter = (s) => { if (!cancelled) setStage(s) }
    ;(async () => {
      try {
        const result = await ensureSession()
        if (!cancelled) { setData(result); setBootError(null); setReady(true) }
      } catch (e: any) {
        if (!cancelled) { setBootError(extractError(e)); setReady(true) }
      }
    })()
    return () => { cancelled = true; stageReporter = null }
  }, [])

  const refresh = useCallback(async () => {
    const result = await ensureSession(true)
    setData(result)
  }, [])

  const refreshNotifications = useCallback(async () => {
    if (!data?.email) return
    const notes = await listRows('bg_notifications', {
      filters: [{ field: 'user_email', op: 'eq', value: data.email }, { field: 'is_read', op: 'eq', value: false }],
      limit: 100,
    })
    setData((prev) => (prev ? { ...prev, unread: notes.length } : prev))
  }, [data?.email])

  const setActiveOrgId = useCallback((id: string) => {
    localStorage.setItem(STORAGE_KEY, id)
    setActiveOrgIdState(id)
  }, [])

  const orgs = data?.orgs ?? []
  const memberships = data?.memberships ?? []
  // Archived workspaces stay out of the switcher even if a membership row exists.
  const mine = useMemo(() => orgs.filter((o) => o.is_active !== false
    && memberships.some((m) => m.organization_id === o.id)), [orgs, memberships])

  useEffect(() => {
    if (!ready || !mine.length) return
    if (!mine.some((o) => o.id === activeOrgId)) setActiveOrgIdState(mine[0].id)
  }, [ready, mine, activeOrgId])

  const activeOrg = mine.find((o) => o.id === activeOrgId) ?? mine[0] ?? null
  const membership = memberships.find((m) => m.organization_id === activeOrg?.id) ?? null
  const role = (membership?.role ?? null) as Role | null

  const can = useCallback((cap: Capability) => {
    if (!role) return false
    return (CAPABILITIES[cap] as readonly string[]).includes(role)
  }, [role])

  const value: SessionValue = {
    user: data?.user ?? null,
    email: data?.email ?? '',
    orgs: mine,
    memberships,
    unread: data?.unread ?? 0,
    ready, stage, bootError, activeOrg, role, membership, can, setActiveOrgId, refresh, refreshNotifications,
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used inside SessionProvider')
  return ctx
}

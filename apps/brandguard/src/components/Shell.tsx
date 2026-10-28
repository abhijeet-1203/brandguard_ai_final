import React, { useEffect, useState } from 'react'
import {
  Activity, BadgeCheck, Bell, BookOpen, Building2, ClipboardCheck, FileImage, FileSearch, Gauge,
  GitBranch, Image as ImageIcon, LayoutDashboard, ListChecks, Menu, ScrollText, Send, Settings,
  ShieldAlert, Share2, Sparkles, TrendingUp, Users, X,
} from 'lucide-react'
import { useSession } from '../lib/session'
import { listRows, sql, updateRow, watchTable, Row } from '../lib/api'
import { ROLE_LABEL, relTime } from '../lib/types'
import { Badge, Button } from './ui'

export type Route = { page: string; id?: string }

const NAV: { group: string; items: { page: string; label: string; icon: React.ReactNode; cap?: string }[] }[] = [
  {
    group: 'Overview',
    items: [
      { page: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={15} /> },
      { page: 'analytics', label: 'Governance analytics', icon: <Gauge size={15} /> },
    ],
  },
  {
    group: 'Content review',
    items: [
      { page: 'reviews', label: 'Content reviews', icon: <FileSearch size={15} /> },
      { page: 'new', label: 'New review', icon: <ClipboardCheck size={15} /> },
      { page: 'approvals', label: 'Approval centre', icon: <ListChecks size={15} /> },
      { page: 'campaigns', label: 'Cross-channel check', icon: <Share2 size={15} /> },
      { page: 'creative', label: 'Creative compliance', icon: <FileImage size={15} /> },
    ],
  },
  {
    group: 'Brand knowledge',
    items: [
      { page: 'guidelines', label: 'Brand guidelines', icon: <BookOpen size={15} /> },
      { page: 'facts', label: 'Approved facts', icon: <BadgeCheck size={15} /> },
      { page: 'assets', label: 'Asset library', icon: <Building2 size={15} /> },
    ],
  },
  {
    group: 'Governance',
    items: [
      { page: 'risk', label: 'Risk triage', icon: <TrendingUp size={15} /> },
      { page: 'visual', label: 'Visual compliance', icon: <ImageIcon size={15} /> },
      { page: 'provenance', label: 'Claim provenance', icon: <GitBranch size={15} /> },
      { page: 'insights', label: 'Governance memory', icon: <Sparkles size={15} /> },
      { page: 'policy', label: 'Root cause & policy', icon: <GitBranch size={15} /> },
      { page: 'publish', label: 'Governed publishing', icon: <Send size={15} /> },
      { page: 'chains', label: 'Approval chains', icon: <GitBranch size={15} /> },
      { page: 'audit', label: 'Audit log', icon: <ScrollText size={15} /> },
      { page: 'quality', label: 'Quality & evaluation', icon: <Activity size={15} /> },
    ],
  },
  {
    group: 'Administration',
    items: [
      { page: 'team', label: 'Team & permissions', icon: <Users size={15} /> },
      { page: 'settings', label: 'Settings & privacy', icon: <Settings size={15} /> },
    ],
  },
]

export function Shell({ route, navigate, children }: { route: Route; navigate: (r: Route) => void; children: React.ReactNode }) {
  const session = useSession()
  const [open, setOpen] = useState(false)
  const [counts, setCounts] = useState<{ reviews: number; approvals: number; failed: number }>({ reviews: 0, approvals: 0, failed: 0 })
  const [notifications, setNotifications] = useState<Row[]>([])
  const [showNotes, setShowNotes] = useState(false)
  const orgId = session.activeOrg?.id

  const loadCounts = async () => {
    if (!orgId) return
    const rows = await sql<Row>(
      `select
        (select count(*) from bg_reviews where organization_id = '${orgId}' and status in ('awaiting_review','resubmitted')) as reviews,
        (select count(*) from bg_approvals where organization_id = '${orgId}' and kind = 'assignment' and status = 'pending') as approvals,
        (select count(*) from bg_reviews where organization_id = '${orgId}' and status = 'analysis_failed') as failed`,
    )
    const r = rows[0] ?? {}
    setCounts({ reviews: Number(r.reviews ?? 0), approvals: Number(r.approvals ?? 0), failed: Number(r.failed ?? 0) })
  }

  const loadNotifications = async () => {
    if (!session.email) return
    const notes = await listRows('bg_notifications', {
      filters: [{ field: 'user_email', op: 'eq', value: session.email }],
      sort: [{ field: 'created_at', direction: 'desc' }], limit: 12,
    })
    setNotifications(notes)
  }

  useEffect(() => {
    void loadCounts()
    void loadNotifications()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, route.page, route.id, session.email])

  useEffect(() => {
    if (!orgId) return
    return watchTable('bg_reviews', () => { void loadCounts() })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId])

  const badgeFor = (page: string) => {
    if (page === 'reviews' && counts.reviews) return counts.reviews
    if (page === 'approvals' && counts.approvals) return counts.approvals
    return null
  }

  const go = (page: string) => { navigate({ page }); setOpen(false) }

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Primary navigation">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">BG</div>
          <div>
            <div className="brand-name">BrandGuard AI</div>
            <div className="brand-sub">Brand governance</div>
          </div>
          <button className="btn btn-ghost btn-sm menu-btn" style={{ marginLeft: 'auto' }} onClick={() => setOpen(false)} aria-label="Close navigation">
            <X size={15} />
          </button>
        </div>
        {NAV.map((group) => (
          <nav className="nav-group" key={group.group} aria-label={group.group}>
            <div className="nav-group-label">{group.group}</div>
            {group.items.map((item) => (
              <button
                key={item.page}
                className={`nav-item ${route.page === item.page ? 'active' : ''}`}
                onClick={() => go(item.page)}
                aria-current={route.page === item.page ? 'page' : undefined}
              >
                {item.icon}
                <span>{item.label}</span>
                {badgeFor(item.page) ? <span className="nav-count">{badgeFor(item.page)}</span> : null}
              </button>
            ))}
          </nav>
        ))}
        {counts.failed ? (
          <div className="warn-msg" style={{ marginTop: 8 }}>
            {counts.failed} review(s) failed analysis and need a retry.
          </div>
        ) : null}
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="btn btn-ghost btn-sm menu-btn" onClick={() => setOpen(true)} aria-label="Open navigation">
            <Menu size={16} />
          </button>
          <div className="crumbs">
            <span>BrandGuard AI</span>
            <span aria-hidden="true">/</span>
            <b>{NAV.flatMap((g) => g.items).find((i) => i.page === route.page)?.label ?? 'Dashboard'}</b>
          </div>
          <div className="topbar-actions">
            <label className="small muted" htmlFor="org-switch" style={{ marginRight: -4 }}>Workspace</label>
            <select
              id="org-switch"
              value={session.activeOrg?.id ?? ''}
              onChange={(e) => session.setActiveOrgId(e.target.value)}
              style={{ width: 'auto', minWidth: 180 }}
            >
              {session.orgs
                .filter((o) => session.memberships.some((m) => m.organization_id === o.id))
                .map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            <Badge tone="b-accent" title={`Your role in this workspace: ${ROLE_LABEL[session.role ?? ''] ?? 'none'}`}>
              {ROLE_LABEL[session.role ?? ''] ?? 'no role'}
            </Badge>
            <div style={{ position: 'relative' }}>
              <Button size="sm" variant="ghost" onClick={() => setShowNotes((v) => !v)} title="Notifications">
                <Bell size={16} />
                {session.unread ? <span className="nav-count">{session.unread}</span> : null}
              </Button>
              {showNotes ? (
                <div className="card" style={{ position: 'absolute', right: 0, top: 36, width: 340, zIndex: 40, maxHeight: 420, overflowY: 'auto' }}>
                  <CardHeadNotes onClose={() => setShowNotes(false)} />
                  {notifications.length === 0 ? (
                    <div className="empty small">No notifications yet.</div>
                  ) : (
                    notifications.map((n) => (
                      <div key={n.id} style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 600, fontSize: 13 }}>{n.title}</div>
                            {n.body ? <div className="muted small">{n.body}</div> : null}
                            <div className="muted small">{relTime(n.created_at)}</div>
                          </div>
                          {!n.is_read ? (
                            <button
                              className="btn-link"
                              onClick={async () => {
                                await updateRow('bg_notifications', n.id, { is_read: true })
                                await loadNotifications()
                                await session.refreshNotifications()
                              }}
                            >
                              mark read
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
            <div className="small" style={{ textAlign: 'right' }}>
              <div style={{ fontWeight: 600 }}>{session.membership?.display_name ?? session.email}</div>
              <div className="muted" style={{ fontSize: 11 }}>{session.email}</div>
            </div>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  )
}

function CardHeadNotes({ onClose }: { onClose: () => void }) {
  return (
    <div className="card-head">
      <h3 style={{ flex: 1 }}>Notifications</h3>
      <Button size="sm" variant="ghost" onClick={onClose} title="Close notifications"><X size={14} /></Button>
    </div>
  )
}

export function ShieldIcon() {
  return <ShieldAlert size={16} />
}

import React, { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { Badge, Button, Card, CardHead, Empty, Loading } from '../components/ui'
import { fmtDateTime, Row } from '../lib/types'

const ACTIONS = ['', 'review.created', 'review.analysed', 'review.analysis_failed', 'review.approve',
  'review.reject', 'review.request_changes', 'review.assigned', 'guideline.created', 'guideline.approved',
  'guideline.archived', 'fact.created', 'fact.verified', 'asset.approved', 'asset.replaced',
  'correction.applied', 'correction.regenerated', 'policy.updated', 'comment.added', 'workspace.seeded']

export function AuditLog() {
  const session = useSession()
  const orgId = session.activeOrg?.id ? safeId(session.activeOrg.id) : null
  const [action, setAction] = useState('')
  const [actor, setActor] = useState('')
  const [q, setQ] = useState('')

  const where = [
    orgId ? `organization_id = '${orgId}'` : '',
    action ? `action = '${action.replace(/'/g, '')}'` : '',
    actor ? `actor_email = '${actor.replace(/'/g, '')}'` : '',
  ].filter(Boolean).join(' and ')

  const rows = useSqlQuery<Row>(`select * from bg_audit_logs where ${where} order by created_at desc limit 300`, [where])
  const actors = useSqlQuery<Row>(orgId ? `
    select distinct actor_email from bg_audit_logs where organization_id = '${orgId}' and actor_email is not null order by 1` : null, [orgId])

  const filtered = rows.items.filter((r) => !q || `${r.summary} ${r.action} ${r.subject_type}`.toLowerCase().includes(q.toLowerCase()))

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Audit log</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Immutable record of governance actions: who did what, to which record, and when.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={rows.refresh}><RefreshCw size={14} /> Refresh</Button>
      </div>

      <Card pad>
        <div className="row">
          <div>
            <label className="small muted" htmlFor="a-action">Action</label>
            <select id="a-action" value={action} onChange={(e) => setAction(e.target.value)}>
              {ACTIONS.map((a) => <option key={a} value={a}>{a === '' ? 'All actions' : a}</option>)}
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="a-actor">Actor</label>
            <select id="a-actor" value={actor} onChange={(e) => setActor(e.target.value)}>
              <option value="">All people</option>
              {actors.items.map((a) => <option key={a.actor_email} value={a.actor_email}>{a.actor_email}</option>)}
            </select>
          </div>
          <div style={{ minWidth: 220 }}>
            <label className="small muted" htmlFor="a-q">Search summary</label>
            <input id="a-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. warranty" />
          </div>
          <div className="spacer" />
          <div className="muted small">{filtered.length} record(s)</div>
        </div>
      </Card>

      <Card>
        {rows.loading ? <Loading label="Loading audit records…" /> : rows.error ? <div className="err">{rows.error}</div> : filtered.length === 0 ? (
          <Empty title="No audit records match these filters" />
        ) : (
          <div className="table-wrap">
            <table aria-label="Audit records">
              <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Summary</th><th>Subject</th><th>Details</th></tr></thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id}>
                    <td className="muted small nowrap">{fmtDateTime(r.created_at)}</td>
                    <td className="small">{r.actor_email ?? '—'}<div className="muted small">{r.actor_name}</div></td>
                    <td><Badge tone={r.action.includes('failed') || r.action.includes('reject') ? 'b-critical' : r.action.includes('approve') ? 'b-ok' : 'b-info'}>{r.action}</Badge></td>
                    <td className="small">{r.summary}</td>
                    <td className="muted small">{r.subject_type ?? '—'}{r.subject_id ? <div className="mono">{String(r.subject_id).slice(0, 8)}…</div> : null}</td>
                    <td className="small">
                      {r.metadata && Object.keys(r.metadata).length ? (
                        <details><summary className="btn-link">metadata</summary>
                          <pre className="mono" style={{ whiteSpace: 'pre-wrap', maxWidth: 420 }}>{JSON.stringify(r.metadata, null, 1)}</pre>
                        </details>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

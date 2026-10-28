import React, { useMemo, useState } from 'react'
import { FileSearch, RefreshCw } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, StatusBadge } from '../components/ui'
import { CONTENT_TYPES, STATUS_LABEL, relTime, Row } from '../lib/types'
import type { Route } from '../components/Shell'

const STATUSES = ['draft', 'submitted', 'analysing', 'analysis_failed', 'awaiting_review', 'changes_requested',
  'resubmitted', 'approved', 'rejected', 'archived']

export function Reviews({ navigate }: { navigate: (r: Route) => void }) {
  const { activeOrg, role, can } = useSession()
  const orgId = activeOrg?.id ? safeId(activeOrg.id) : null
  const [status, setStatus] = useState('')
  const [ctype, setCtype] = useState('')
  const [risk, setRisk] = useState('')
  const [q, setQ] = useState('')

  const where = useMemo(() => {
    if (!orgId) return ''
    const clauses = [`r.organization_id = '${orgId}'`]
    if (status) clauses.push(`r.status = '${status.replace(/'/g, '')}'`)
    if (ctype) clauses.push(`r.content_type = '${ctype.replace(/'/g, '')}'`)
    if (risk) clauses.push(`r.risk_level = '${risk.replace(/'/g, '')}'`)
    return clauses.join(' and ')
  }, [orgId, status, ctype, risk])

  const rows = useSqlQuery<Row>(orgId ? `
    select r.*,
      (select count(*) from bg_findings f where f.review_id = r.id and f.review_status = 'open') as open_findings,
      (select count(*) from bg_findings f where f.review_id = r.id and f.review_status = 'open'
        and f.severity in ('critical','high')) as serious_findings
    from bg_reviews r where ${where} order by r.created_at desc limit 200` : null, [where])

  const filtered = rows.items.filter((r) =>
    !q || `${r.title} ${r.campaign ?? ''}`.toLowerCase().includes(q.toLowerCase()))

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Content reviews</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Every submission with its analysis state, risk level and outstanding findings.
          </p>
        </div>
        <div className="spacer" />
        <Button onClick={rows.refresh} size="sm"><RefreshCw size={14} /> Refresh</Button>
        {can('createContent') ? <Button variant="primary" onClick={() => navigate({ page: 'new' })}><FileSearch size={15} /> New review</Button> : null}
      </div>

      <Card pad>
        <div className="row" style={{ gap: 12 }}>
          <div>
            <label className="small muted" htmlFor="f-status">Status</label>
            <select id="f-status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s] ?? s}</option>)}
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="f-type">Content type</label>
            <select id="f-type" value={ctype} onChange={(e) => setCtype(e.target.value)}>
              <option value="">All types</option>
              {CONTENT_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="f-risk">Risk level</label>
            <select id="f-risk" value={risk} onChange={(e) => setRisk(e.target.value)}>
              <option value="">Any risk</option>
              {['critical', 'high', 'medium', 'low'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div style={{ minWidth: 220 }}>
            <label className="small muted" htmlFor="f-q">Search title or campaign</label>
            <input id="f-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. AirPure launch" />
          </div>
          <div className="spacer" />
          <div className="muted small">{rows.items.length} review(s) · your role: {role ?? 'none'}</div>
        </div>
      </Card>

      <Card>
        {rows.loading ? <Loading label="Loading reviews…" /> : rows.error ? <div className="err">{rows.error}</div> : rows.items.length === 0 ? (
          <Empty title="No reviews match these filters"
            hint="Adjust the filters, or submit new content to start a compliance review."
            action={can('createContent') ? <Button variant="primary" onClick={() => navigate({ page: 'new' })}>New review</Button> : undefined} />
        ) : (
          <div className="table-wrap">
            <table aria-label="Content reviews">
              <thead>
                <tr>
                  <th>Submission</th><th>Status</th><th>Risk</th><th>Findings</th>
                  <th>Version</th><th>Reviewer</th><th>Submitted</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => navigate({ page: 'review', id: r.id })}>
                    <td>
                      <div style={{ fontWeight: 580 }}>{r.title}</div>
                      <div className="muted small">
                        {(r.content_type ?? '').replace(/_/g, ' ')}
                        {r.campaign ? ` · ${r.campaign}` : ''}
                      </div>
                    </td>
                    <td><StatusBadge status={r.status} label={STATUS_LABEL[r.status]} /></td>
                    <td>{r.risk_level ? <SeverityBadge severity={r.risk_level} /> : <span className="muted small">—</span>}</td>
                    <td>
                      <span className="mono">{r.open_findings}</span>
                      {Number(r.serious_findings) > 0 ? <span className="muted small"> · {r.serious_findings} serious</span> : null}
                    </td>
                    <td className="mono">v{r.current_version}</td>
                    <td className="small">{r.assigned_reviewer_email ?? <span className="muted">unassigned</span>}</td>
                    <td className="muted small nowrap">{relTime(r.submitted_at ?? r.created_at)}</td>
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

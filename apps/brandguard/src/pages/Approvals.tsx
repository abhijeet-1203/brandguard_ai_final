import React, { useState } from 'react'
import { CheckCircle2, Inbox, RefreshCw, ShieldAlert } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { runFn } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, StatusBadge, useToast } from '../components/ui'
import { STATUS_LABEL, fmtDateTime, relTime, titleCase, Row } from '../lib/types'
import type { Route } from '../components/Shell'

export function Approvals({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id ? safeId(session.activeOrg.id) : null
  const [busy, setBusy] = useState<string | null>(null)
  const [filter, setFilter] = useState<'queue' | 'assigned' | 'decided'>('queue')

  const queue = useSqlQuery<Row>(orgId ? `
    select r.*,
      (select count(*) from bg_findings f where f.review_id = r.id and f.review_status = 'open') as open_findings,
      (select count(*) from bg_findings f where f.review_id = r.id and f.review_status = 'open'
        and f.severity in ('critical','high')) as blocking
    from bg_reviews r
    where r.organization_id = '${orgId}' and r.status in ('awaiting_review','resubmitted')
    order by case r.risk_level when 'critical' then 0 when 'high' then 1 when 'medium' then 2 else 3 end,
             r.submitted_at asc` : null, [orgId])

  const assignments = useSqlQuery<Row>(orgId ? `
    select a.*, r.title, r.status as review_status, r.risk_level
    from bg_approvals a join bg_reviews r on r.id = a.review_id
    where a.organization_id = '${orgId}' and a.kind = 'assignment' and a.status = 'pending'
    order by a.created_at desc` : null, [orgId])

  const decided = useSqlQuery<Row>(orgId ? `
    select a.*, r.title, r.current_version
    from bg_approvals a join bg_reviews r on r.id = a.review_id
    where a.organization_id = '${orgId}' and a.kind = 'decision'
    order by a.decided_at desc limit 40` : null, [orgId])

  const decide = async (review: Row, action: 'request_changes' | 'approve' | 'reject') => {
    const reason = window.prompt(
      action === 'approve'
        ? `Approval reason for “${review.title}” (recorded in the audit log):`
        : action === 'reject'
          ? `Why is “${review.title}” being rejected?`
          : `What needs to change in “${review.title}”?`,
      '',
    )
    if (!reason) return
    setBusy(review.id + action)
    const res = await runFn('bg_review_workflow', {
      organization_id: review.organization_id, review_id: review.id, action, reason,
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The decision could not be recorded.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Decision recorded.', out.blocked ? 'error' : 'success')
    queue.refresh(); assignments.refresh(); decided.refresh()
  }

  const claim = async (row: Row) => {
    setBusy(row.id)
    const res = await runFn('bg_review_workflow', {
      organization_id: row.organization_id, review_id: row.review_id, action: 'assign',
      assigned_reviewer_email: session.email, reason: 'self-assigned from the approval centre',
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not assign.', 'error'); return }
    toast.push('Assigned to you.', 'success')
    queue.refresh(); assignments.refresh()
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Approval centre</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Risk-based routing: high-risk findings are sent to the reviewer role required by workspace policy.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { queue.refresh(); assignments.refresh(); decided.refresh() }}><RefreshCw size={14} /> Refresh</Button>
      </div>

      <div className="row" role="tablist" aria-label="Approval views">
        {([['queue', `Pending reviews (${queue.items.length})`], ['assigned', `Open assignments (${assignments.items.length})`],
           ['decided', 'Recent decisions']] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={filter === key} className={`chip ${filter === key ? 'on' : ''}`}
            onClick={() => setFilter(key as any)}>{label}</button>
        ))}
      </div>

      {filter === 'queue' ? (
        <Card>
          <CardHead title="Reviews awaiting a human decision" subtitle="Nothing here is approved by the AI — a person decides." />
          {queue.loading ? <Loading label="Loading the approval queue…" /> : queue.items.length === 0 ? (
            <Empty title="Nothing waiting for approval" hint="Reviews appear here once their analysis completes." />
          ) : (
            <div className="table-wrap">
              <table aria-label="Pending reviews">
                <thead><tr><th>Submission</th><th>Status</th><th>Risk</th><th>Findings</th><th>Required role</th><th>Assigned</th><th>Submitted</th><th>Decision</th></tr></thead>
                <tbody>
                  {queue.items.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <button className="btn-link" onClick={() => navigate({ page: 'review', id: r.id })}>{r.title}</button>
                        <div className="muted small">{titleCase(r.content_type)}{r.campaign ? ` · ${r.campaign}` : ''}</div>
                      </td>
                      <td><StatusBadge status={r.status} label={STATUS_LABEL[r.status]} /></td>
                      <td>{r.risk_level ? <SeverityBadge severity={r.risk_level} /> : <span className="muted small">—</span>}</td>
                      <td><span className="mono">{r.open_findings}</span>{Number(r.blocking) > 0 ? <span className="muted small"> · {r.blocking} blocking</span> : null}</td>
                      <td className="small">{r.required_reviewer_role ? titleCase(r.required_reviewer_role) : '—'}</td>
                      <td className="small">{r.assigned_reviewer_email ?? <span className="muted">unassigned</span>}</td>
                      <td className="muted small nowrap">{relTime(r.submitted_at)}</td>
                      <td>
                        <div className="row" style={{ gap: 4 }}>
                          {session.can('decideReview') ? (
                            <>
                              <Button size="sm" variant="primary" disabled={busy === r.id + 'approve'} onClick={() => decide(r, 'approve')}>Approve</Button>
                              <Button size="sm" disabled={busy === r.id + 'request_changes'} onClick={() => decide(r, 'request_changes')}>Changes</Button>
                              <Button size="sm" variant="danger" disabled={busy === r.id + 'reject'} onClick={() => decide(r, 'reject')}>Reject</Button>
                            </>
                          ) : <span className="muted small">read-only</span>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}

      {filter === 'assigned' ? (
        <Card>
          <CardHead title="Open reviewer assignments" />
          {assignments.loading ? <Loading /> : assignments.items.length === 0 ? (
            <Empty title="No open assignments" hint="Assignments are created automatically when an analysis routes a review to a role, or manually on a review." />
          ) : (
            <div className="table-wrap">
              <table aria-label="Assignments">
                <thead><tr><th>Review</th><th>Required role</th><th>Assigned to</th><th>Reason</th><th>Created</th><th /></tr></thead>
                <tbody>
                  {assignments.items.map((a) => (
                    <tr key={a.id}>
                      <td><button className="btn-link" onClick={() => navigate({ page: 'review', id: a.review_id })}>{a.title}</button>
                        <div className="muted small"><StatusBadge status={a.review_status} label={STATUS_LABEL[a.review_status]} /></div></td>
                      <td className="small">{titleCase(a.required_role)}</td>
                      <td className="small">{a.reviewer_email ?? <span className="muted">unassigned</span>}</td>
                      <td className="small">{a.reason ?? '—'}</td>
                      <td className="muted small nowrap">{relTime(a.created_at)}</td>
                      <td>{!a.reviewer_email && session.can('decideReview') ? (
                        <Button size="sm" disabled={busy === a.id} onClick={() => claim(a)}><Inbox size={13} /> Take this</Button>
                      ) : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}

      {filter === 'decided' ? (
        <Card>
          <CardHead title="Recorded decisions" subtitle="Who decided what, when, on which version and why" />
          {decided.loading ? <Loading /> : decided.items.length === 0 ? <Empty title="No decisions recorded yet" /> : (
            <div className="table-wrap">
              <table aria-label="Recent decisions">
                <thead><tr><th>When</th><th>Review</th><th>Version</th><th>Decision</th><th>Reviewer</th><th>Reason</th></tr></thead>
                <tbody>
                  {decided.items.map((d) => (
                    <tr key={d.id}>
                      <td className="muted small nowrap">{fmtDateTime(d.decided_at)}</td>
                      <td><button className="btn-link" onClick={() => navigate({ page: 'review', id: d.review_id })}>{d.title}</button></td>
                      <td className="mono">v{d.version ?? '—'}</td>
                      <td>
                        <Badge tone={d.status === 'approved' ? 'b-ok' : d.status === 'rejected' ? 'b-critical' : 'b-high'}>{d.status}</Badge>
                        {d.is_override ? <Badge tone="b-critical"><ShieldAlert size={11} /> override</Badge> : null}
                      </td>
                      <td className="small">{d.reviewer_email}</td>
                      <td className="small">{d.reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}

      <Card pad>
        <div className="row small">
          <CheckCircle2 size={14} />
          <span>
            Approvals are enforced server-side: separation of duties, mandatory-violation blocking and the
            compliance-reviewer requirement for critical findings are all checked by the backend, not by this screen.
          </span>
        </div>
      </Card>
    </div>
  )
}

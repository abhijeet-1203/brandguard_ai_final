import React, { useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, StatusBadge, useToast } from '../components/ui'
import { STATUS_LABEL, fmtDateTime, titleCase } from '../lib/types'
import type { Route } from '../components/Shell'

export function Risk({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id ? safeId(session.activeOrg.id) : null
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const queue = useSqlQuery<Row>(orgId ? `
    select r.id, r.title, r.content_type, r.status, r.current_version, r.risk_score, r.risk_band, r.risk_factors,
           r.assigned_reviewer_email, r.submitted_at,
           (select count(*) from bg_findings f where f.review_id = r.id and f.review_status = 'open') as open_findings
    from bg_reviews r
    where r.organization_id = '${orgId}' and r.status in ('awaiting_review','resubmitted','analysing','submitted')
    order by coalesce(r.risk_score, -1) desc, r.submitted_at asc` : null, [orgId])

  const all = useSqlQuery<Row>(orgId ? `
    select count(*) as total,
           count(*) filter (where risk_band = 'critical') as critical,
           count(*) filter (where risk_band = 'high') as high,
           count(*) filter (where risk_band = 'medium') as medium,
           count(*) filter (where risk_band = 'low') as low,
           count(*) filter (where risk_score is null) as unscored
    from bg_reviews where organization_id = '${orgId}'` : null, [orgId])

  const outcomes = useSqlQuery<Row>(orgId ? `
    select band, outcome, count(*) as n from bg_risk_scores
    where organization_id = '${orgId}' group by 1, 2 order by 1, 2` : null, [orgId])

  const sync = async () => {
    setBusy(true)
    const res = await runFn('bg_risk_score', { organization_id: orgId, action: 'sync_outcomes' })
    if (!res.ok) { toast.push(res.error ?? 'Could not sync outcomes.', 'error'); setBusy(false); return }
    const res2 = await runFn('bg_risk_score', { organization_id: orgId })
    setBusy(false)
    if (!res2.ok) { toast.push(res2.error ?? 'Scoring failed.', 'error'); return }
    toast.push('Risk scores recalculated and outcomes recorded.', 'success')
    queue.refresh(); all.refresh(); outcomes.refresh()
  }

  const stats = all.items[0] ?? {}
  const bands = ['critical', 'high', 'medium', 'low']
  const byBand: Record<string, Record<string, number>> = {}
  outcomes.items.forEach((o) => {
    byBand[o.band] = byBand[o.band] ?? {}
    byBand[o.band][o.outcome] = Number(o.n)
  })

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Predictive risk triage</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Which submissions most need a person, and why. Every score is a weighted sum of named factors — it is a
            triage aid, not a calibrated probability of error.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" disabled={busy} onClick={sync}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <RefreshCw size={14} />} Recalculate and record outcomes
        </Button>
      </div>

      <div className="grid cols-4">
        <Card><div className="metric"><div className="metric-label">Critical band</div><div className="metric-value">{stats.critical ?? 0}</div><div className="metric-note">of {stats.total ?? 0} reviews</div></div></Card>
        <Card><div className="metric"><div className="metric-label">High band</div><div className="metric-value">{stats.high ?? 0}</div><div className="metric-note">review before the rest</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Medium / low</div><div className="metric-value">{Number(stats.medium ?? 0) + Number(stats.low ?? 0)}</div><div className="metric-note">normal queue</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Not scored yet</div><div className="metric-value">{stats.unscored ?? 0}</div><div className="metric-note">press recalculate</div></div></Card>
      </div>

      <Card>
        <CardHead title="Review queue, highest risk first" subtitle="Click a submission to see the factors behind its score" />
        {queue.loading ? <Loading label="Loading the queue…" /> : queue.items.length === 0 ? (
          <Empty title="Nothing waiting for review" hint="Submissions appear here while they await a human decision." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Risk-ranked queue">
              <thead><tr><th>Score</th><th>Submission</th><th>Status</th><th>Open findings</th><th>Reviewer</th><th>Submitted</th><th /></tr></thead>
              <tbody>
                {queue.items.map((r) => (
                  <React.Fragment key={r.id}>
                    <tr className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)}>
                      <td>
                        {r.risk_score === null || r.risk_score === undefined
                          ? <span className="muted small">not scored</span>
                          : <><b className="mono">{r.risk_score}</b> <SeverityBadge severity={r.risk_band} /></>}
                      </td>
                      <td>
                        <div style={{ fontWeight: 560 }}>{r.title}</div>
                        <div className="muted small">{titleCase(r.content_type)} · v{r.current_version}</div>
                      </td>
                      <td><StatusBadge status={r.status} label={STATUS_LABEL[r.status]} /></td>
                      <td className="mono">{r.open_findings}</td>
                      <td className="small">{r.assigned_reviewer_email ?? <span className="muted">unassigned</span>}</td>
                      <td className="muted small nowrap">{fmtDateTime(r.submitted_at)}</td>
                      <td><button className="btn-link" onClick={(e) => { e.stopPropagation(); navigate({ page: 'review', id: r.id }) }}>open</button></td>
                    </tr>
                    {open === r.id ? (
                      <tr>
                        <td colSpan={7} style={{ background: 'var(--surface-2)' }}>
                          <div className="stack small">
                            <div className="muted">Why this score</div>
                            {(r.risk_factors ?? []).map((f: Row, i: number) => (
                              <div key={i} className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                                <Badge tone={f.points >= 15 ? 'b-critical' : f.points >= 6 ? 'b-medium' : 'b-info'}>+{f.points}</Badge>
                                <div>
                                  <b>{f.factor}</b>
                                  <div className="muted">{f.reason}</div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHead title="Is the scoring useful?"
          subtitle="Recorded outcomes per predicted band — measured from decided reviews, not estimated" />
        {outcomes.items.length === 0 ? (
          <Empty title="No scored-and-decided reviews yet"
            hint="Outcomes are recorded when a review with a score reaches a decision. Nothing is claimed until then." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Scoring evaluation">
              <thead><tr><th>Predicted band</th><th>Decided</th><th>Approved</th><th>Changes requested</th><th>Rejected</th><th>Needed rework</th></tr></thead>
              <tbody>
                {bands.filter((b) => byBand[b]).map((b) => {
                  const row = byBand[b]
                  const approved = row.approved ?? 0
                  const changes = row.changes_requested ?? 0
                  const rejected = row.rejected ?? 0
                  const decided = approved + changes + rejected
                  return (
                    <tr key={b}>
                      <td><SeverityBadge severity={b} /></td>
                      <td className="mono">{decided}</td>
                      <td className="mono">{approved}</td>
                      <td className="mono">{changes}</td>
                      <td className="mono">{rejected}</td>
                      <td>{decided ? `${Math.round(((changes + rejected) / decided) * 100)}%` : '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ padding: 16 }} className="muted small">
          <Activity size={13} /> A band is useful when a higher band really does end in more rework. With a small
          number of decided reviews this table is a signal, not proof.
        </div>
      </Card>
    </div>
  )
}

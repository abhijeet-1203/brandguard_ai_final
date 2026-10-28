import React from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, FileSearch, ShieldAlert, Sparkles } from 'lucide-react'
import { BarChart, Bar as RBar, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useSession } from '../lib/session'
import { useSqlQuery, safeId } from '../lib/hooks'
import { Card, CardHead, Empty, Loading, Metric, Badge, SeverityBadge, StatusBadge, Button, Bar } from '../components/ui'
import { CATEGORY_LABEL, STATUS_LABEL, fmtDate, pct, relTime, Row } from '../lib/types'
import type { Route } from '../components/Shell'

export function Dashboard({ navigate }: { navigate: (r: Route) => void }) {
  const { activeOrg } = useSession()
  const orgId = activeOrg?.id ? safeId(activeOrg.id) : null

  const overview = useSqlQuery<Row>(orgId ? `
    select
      (select count(*) from bg_reviews where organization_id = '${orgId}') as total_reviews,
      (select count(*) from bg_reviews where organization_id = '${orgId}' and status = 'approved') as approved,
      (select count(*) from bg_reviews where organization_id = '${orgId}' and status = 'rejected') as rejected,
      (select count(*) from bg_reviews where organization_id = '${orgId}' and status in ('awaiting_review','resubmitted')) as awaiting,
      (select count(*) from bg_reviews where organization_id = '${orgId}' and status = 'changes_requested') as changes,
      (select count(*) from bg_reviews where organization_id = '${orgId}' and status = 'analysis_failed') as failed,
      (select count(*) from bg_reviews where organization_id = '${orgId}' and current_version > 1) as revised,
      (select count(*) from bg_findings f join bg_reviews r on r.id = f.review_id
         where r.organization_id = '${orgId}' and f.review_status = 'open' and f.severity in ('critical','high')) as high_open,
      (select count(*) from bg_findings f join bg_reviews r on r.id = f.review_id
         where r.organization_id = '${orgId}') as findings_total,
      (select count(*) from bg_guidelines where organization_id = '${orgId}' and status = 'active') as active_rules,
      (select count(*) from bg_guidelines where organization_id = '${orgId}' and status = 'pending_approval') as rules_pending,
      (select count(*) from bg_facts where organization_id = '${orgId}' and approval_status = 'approved') as facts,
      (select count(*) from bg_facts where organization_id = '${orgId}' and expiration_date is not null
         and expiration_date < current_date) as facts_expired,
      (select count(*) from bg_assets where organization_id = '${orgId}' and approval_status = 'approved') as assets
  ` : null, [orgId])

  const byCategory = useSqlQuery<Row>(orgId ? `
    select f.category as category, count(*) as n
    from bg_findings f join bg_reviews r on r.id = f.review_id
    where r.organization_id = '${orgId}'
    group by 1 order by n desc` : null, [orgId])

  const bySeverity = useSqlQuery<Row>(orgId ? `
    select f.severity as severity, count(*) as n
    from bg_findings f join bg_reviews r on r.id = f.review_id
    where r.organization_id = '${orgId}'
    group by 1` : null, [orgId])

  const recent = useSqlQuery<Row>(orgId ? `
    select id, title, content_type, status, risk_level, current_version, created_at, created_by_email,
           (select count(*) from bg_findings f where f.review_id = bg_reviews.id and f.review_status = 'open') as open_findings
    from bg_reviews where organization_id = '${orgId}'
    order by created_at desc limit 8` : null, [orgId])

  const recurring = useSqlQuery<Row>(orgId ? `
    select f.rule_code as rule_code, f.title as title, count(*) as n,
           count(distinct f.review_id) as reviews
    from bg_findings f join bg_reviews r on r.id = f.review_id
    where r.organization_id = '${orgId}' and f.rule_code is not null
    group by 1, 2 having count(*) > 1 order by n desc limit 6` : null, [orgId])

  const turnaround = useSqlQuery<Row>(orgId ? `
    select avg(extract(epoch from (decided_at - submitted_at))/3600) as hours, count(*) as decided
    from bg_reviews where organization_id = '${orgId}' and decided_at is not null and submitted_at is not null` : null, [orgId])

  const activity = useSqlQuery<Row>(orgId ? `
    select action, summary, actor_email, created_at from bg_audit_logs
    where organization_id = '${orgId}' order by created_at desc limit 7` : null, [orgId])

  const insights = useSqlQuery<Row>(orgId ? `
    select id, title, insight_type, cases_count, total_count, status from bg_insights
    where organization_id = '${orgId}' and status = 'new' order by generated_at desc limit 3` : null, [orgId])

  const o = overview.items[0] ?? {}
  const total = Number(o.total_reviews ?? 0)
  const approved = Number(o.approved ?? 0)
  const decided = approved + Number(o.rejected ?? 0)
  const hours = turnaround.items[0]?.hours
  const sevMap: Record<string, number> = Object.fromEntries(bySeverity.items.map((r) => [r.severity, Number(r.n)]))
  const maxCat = Math.max(1, ...byCategory.items.map((r) => Number(r.n)))

  if (overview.loading) return <Loading label="Loading governance metrics…" />
  if (overview.error) return <Card pad><div className="err">{overview.error}</div></Card>

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>{activeOrg?.name ?? 'Workspace'}</h1>
          <p className="muted small" style={{ margin: 0 }}>
            {activeOrg?.tone_summary ? `Approved tone: ${activeOrg.tone_summary}` : 'Brand governance overview'}
          </p>
        </div>
        <div className="spacer" />
        <Button variant="primary" onClick={() => navigate({ page: 'new' })}><FileSearch size={15} /> New review</Button>
      </div>

      {total === 0 ? (
        <Card pad>
          <Empty
            title="No review activity yet"
            hint="Submit your first piece of content to begin. Every metric on this page is calculated from stored records, so it stays empty until real reviews exist."
            action={<Button variant="primary" onClick={() => navigate({ page: 'new' })}>Submit content for review</Button>}
          />
        </Card>
      ) : (
        <>
          <div className="grid cols-4">
            <Card><Metric label="Content reviews" value={total} note={`${o.revised ?? 0} needed at least one revision`} /></Card>
            <Card><Metric label="Awaiting human review" value={o.awaiting ?? 0} note={`${o.changes ?? 0} with changes requested`} /></Card>
            <Card>
              <Metric label="Approval rate" value={decided ? pct(approved, decided) : '—'}
                note={decided ? `${approved} approved, ${o.rejected ?? 0} rejected` : 'No decisions recorded yet'} />
            </Card>
            <Card>
              <Metric label="Open high-risk findings" value={o.high_open ?? 0}
                note={`${o.findings_total ?? 0} findings recorded in total`} />
            </Card>
          </div>

          {(o.failed ?? 0) > 0 ? (
            <div className="err">
              <b>{o.failed} review(s) failed analysis.</b> Those reviews are not marked as successfully reviewed — open them and retry.
            </div>
          ) : null}

          <div className="split">
            <Card>
              <CardHead title="Most frequently detected issue categories" subtitle="Counted from stored findings" />
              {byCategory.loading ? <Loading rows={3} /> : byCategory.items.length === 0 ? (
                <Empty title="No findings recorded yet" hint="Findings appear here after the first analysis run completes." />
              ) : (
                <div className="table-wrap">
                  <table aria-label="Findings by category">
                    <thead><tr><th>Category</th><th>Findings</th><th style={{ width: 140 }}>Share</th><th /></tr></thead>
                    <tbody>
                      {byCategory.items.map((r) => (
                        <tr key={r.category}>
                          <td>{CATEGORY_LABEL[r.category] ?? r.category}</td>
                          <td className="mono">{r.n}</td>
                          <td><Bar value={Number(r.n)} max={maxCat} /></td>
                          <td><button className="btn-link" onClick={() => navigate({ page: 'analytics' })}>inspect</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <div className="stack">
              <Card>
                <CardHead title="Findings by severity" />
                <div style={{ padding: '10px 14px' }}>
                  {(['critical', 'high', 'medium', 'low', 'informational'] as const).map((s) => (
                    <div key={s} className="row" style={{ marginBottom: 6 }}>
                      <div style={{ width: 104 }}><SeverityBadge severity={s} /></div>
                      <div className="mono" style={{ width: 40, textAlign: 'right' }}>{sevMap[s] ?? 0}</div>
                      <div style={{ flex: 1 }}><Bar value={sevMap[s] ?? 0} max={Math.max(1, ...Object.values(sevMap))}
                        tone={s === 'critical' ? 'var(--critical)' : s === 'high' ? 'var(--high)' : s === 'medium' ? 'var(--medium)' : 'var(--low)'} /></div>
                    </div>
                  ))}
                </div>
              </Card>
              <Card>
                <CardHead title="Review turnaround" />
                <div style={{ padding: '12px 14px' }}>
                  {turnaround.items[0]?.decided ? (
                    <>
                      <div className="row"><Clock size={15} /><b>{Number(hours).toFixed(1)} hours</b>
                        <span className="muted small">average from submission to decision</span></div>
                      <div className="muted small" style={{ marginTop: 6 }}>
                        Measured across {turnaround.items[0].decided} decided review(s) with a recorded submission time.
                      </div>
                    </>
                  ) : <div className="muted small">No decided reviews yet, so turnaround cannot be calculated.</div>}
                </div>
              </Card>
              <Card>
                <CardHead title="Brand knowledge" />
                <div style={{ padding: '12px 14px' }} className="stack">
                  <div className="row"><CheckCircle2 size={15} /><span>{o.active_rules ?? 0} active guideline rules</span>
                    {(o.rules_pending ?? 0) > 0 ? <Badge tone="b-medium">{o.rules_pending} awaiting approval</Badge> : null}</div>
                  <div className="row"><ShieldAlert size={15} /><span>{o.facts ?? 0} approved facts</span>
                    {(o.facts_expired ?? 0) > 0 ? <Badge tone="b-high">{o.facts_expired} expired</Badge> : null}</div>
                  <div className="row"><CheckCircle2 size={15} /><span>{o.assets ?? 0} approved assets</span></div>
                </div>
              </Card>
            </div>
          </div>

          <div className="split">
            <Card>
              <CardHead title="Recent review activity" actions={<Button size="sm" onClick={() => navigate({ page: 'reviews' })}>All reviews <ArrowRight size={13} /></Button>} />
              {recent.items.length === 0 ? <Empty title="Nothing submitted yet" /> : (
                <div className="table-wrap">
                  <table aria-label="Recent reviews">
                    <thead><tr><th>Submission</th><th>Status</th><th>Risk</th><th>Open findings</th><th>Created</th></tr></thead>
                    <tbody>
                      {recent.items.map((r) => (
                        <tr key={r.id} className="clickable" onClick={() => navigate({ page: 'review', id: r.id })}>
                          <td>
                            <div style={{ fontWeight: 560 }}>{r.title}</div>
                            <div className="muted small">{(r.content_type ?? '').replace(/_/g, ' ')} · v{r.current_version}</div>
                          </td>
                          <td><StatusBadge status={r.status} label={STATUS_LABEL[r.status]} /></td>
                          <td>{r.risk_level ? <SeverityBadge severity={r.risk_level} /> : <span className="muted small">not analysed</span>}</td>
                          <td className="mono">{r.open_findings}</td>
                          <td className="muted small nowrap">{relTime(r.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <div className="stack">
              <Card>
                <CardHead title="Recurring rule breaches" subtitle="Same rule breached more than once" />
                {recurring.items.length === 0 ? (
                  <Empty title="No repeat pattern yet" hint="Repeat breaches are only reported when a rule is breached more than once." />
                ) : (
                  <div style={{ padding: '4px 0' }}>
                    {recurring.items.map((r) => (
                      <div key={`${r.rule_code}-${r.title}`} style={{ padding: '9px 14px', borderBottom: '1px solid var(--line)' }}>
                        <div className="row" style={{ gap: 6 }}>
                          <Badge tone="b-accent">{r.rule_code}</Badge>
                          <span className="mono small">{r.n}× in {r.reviews} review(s)</span>
                        </div>
                        <div className="small" style={{ marginTop: 3 }}>{r.title}</div>
                      </div>
                    ))}
                    <div style={{ padding: '10px 14px' }}>
                      <Button size="sm" onClick={() => navigate({ page: 'insights' })}><Sparkles size={13} /> Governance memory</Button>
                    </div>
                  </div>
                )}
              </Card>
              <Card>
                <CardHead title="New governance insights" />
                {insights.items.length === 0 ? (
                  <Empty title="No new insights" hint="Insights are computed from review history on the Governance memory page." />
                ) : insights.items.map((i) => (
                  <div key={i.id} style={{ padding: '9px 14px', borderBottom: '1px solid var(--line)' }}>
                    <div className="small" style={{ fontWeight: 560 }}>{i.title}</div>
                    <div className="muted small">{i.cases_count}/{i.total_count} cases</div>
                  </div>
                ))}
              </Card>
            </div>
          </div>

          <Card>
            <CardHead title="Audit activity" actions={<Button size="sm" onClick={() => navigate({ page: 'audit' })}>Open audit log</Button>} />
            {activity.items.length === 0 ? <Empty title="No audit records yet" /> : (
              <ul className="timeline" style={{ padding: '4px 14px' }}>
                {activity.items.map((a, i) => (
                  <li key={i}>
                    <span className="when">{relTime(a.created_at)}</span>
                    <span><b>{a.action}</b> — {a.summary}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  )
}

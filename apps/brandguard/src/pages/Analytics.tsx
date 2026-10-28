import React, { useState } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import {
  Bar as RBar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge } from '../components/ui'
import { CATEGORY_LABEL, CONTENT_TYPES, STATUS_LABEL, pct, titleCase, Row } from '../lib/types'
import type { Route } from '../components/Shell'

const SEV_COLORS: Record<string, string> = {
  critical: '#b42318', high: '#b54708', medium: '#8a6100', low: '#3f6212', informational: '#475467',
}
const CAT_COLORS = ['#1d4ed8', '#0f7a55', '#b54708', '#8a6100', '#b42318', '#7c3aed', '#0e7490', '#475467', '#3f6212']

export function Analytics({ navigate }: { navigate: (r: Route) => void }) {
  const { activeOrg, can } = useSession()
  const orgId = activeOrg?.id ? safeId(activeOrg.id) : null
  const [days, setDays] = useState(365)
  const [ctype, setCtype] = useState('')
  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')

  const filters = [
    ctype ? `r.content_type = '${ctype.replace(/'/g, '')}'` : '',
    status ? `r.status = '${status.replace(/'/g, '')}'` : '',
    severity ? `f.severity = '${severity.replace(/'/g, '')}'` : '',
    category ? `f.category = '${category.replace(/'/g, '')}'` : '',
  ].filter(Boolean).join(' and ')
  const reviewFilter = [ctype ? `content_type = '${ctype.replace(/'/g, '')}'` : '',
    status ? `status = '${status.replace(/'/g, '')}'` : ''].filter(Boolean).join(' and ')

  const joined = `from bg_findings f join bg_reviews r on r.id = f.review_id
    where r.organization_id = '${orgId}' and r.created_at > now() - interval '${days} days'
    ${filters ? `and ${filters}` : ''}`

  const byCategory = useSqlQuery<Row>(orgId ? `select f.category as category, count(*) as n ${joined} group by 1 order by n desc` : null, [joined])
  const bySeverity = useSqlQuery<Row>(orgId ? `select f.severity as severity, count(*) as n ${joined} group by 1` : null, [joined])
  const byEvidence = useSqlQuery<Row>(orgId ? `select f.evidence_status as status, count(*) as n ${joined} group by 1 order by n desc` : null, [joined])
  const topRules = useSqlQuery<Row>(orgId ? `
    select f.rule_code as rule_code, f.title as title, count(*) as n, count(distinct f.review_id) as reviews
    ${joined} and f.rule_code is not null group by 1, 2 order by n desc limit 8` : null, [joined])
  const topTerms = useSqlQuery<Row>(orgId ? `
    select f.original_text as term, f.rule_code as rule_code, count(*) as n
    ${joined} and f.category = 'terminology' and f.original_text is not null
    group by 1, 2 order by n desc limit 8` : null, [joined])
  const trend = useSqlQuery<Row>(orgId ? `
    select to_char(date_trunc('week', r.created_at), 'YYYY-MM-DD') as week,
           count(distinct r.id) as reviews, count(f.id) as findings
    from bg_reviews r left join bg_findings f on f.review_id = r.id
    where r.organization_id = '${orgId}' and r.created_at > now() - interval '${days} days'
    group by 1 order by 1` : null, [joined])
  const rates = useSqlQuery<Row>(orgId ? `
    select
      count(*) as total,
      count(*) filter (where current_version > 1) as revised,
      count(*) filter (where status = 'approved') as approved,
      count(*) filter (where status = 'rejected') as rejected,
      avg(extract(epoch from (decided_at - submitted_at))/3600) as hours
    from bg_reviews where organization_id = '${orgId}' ${reviewFilter ? `and ${reviewFilter}` : ''}` : null, [reviewFilter])
  const byType = useSqlQuery<Row>(orgId ? `
    select content_type as content_type, count(*) as n,
           count(*) filter (where current_version > 1) as revised
    from bg_reviews where organization_id = '${orgId}' ${reviewFilter ? `and ${reviewFilter}` : ''}
    group by 1 order by n desc` : null, [reviewFilter])
  const workload = useSqlQuery<Row>(orgId ? `
    select reviewer_email, count(*) as decisions,
           count(*) filter (where status = 'changes_requested') as changes,
           count(*) filter (where status = 'approved') as approvals
    from bg_approvals where organization_id = '${orgId}' and kind = 'decision' and reviewer_email is not null
    group by 1 order by decisions desc limit 10` : null, [orgId])
  const severityByRule = useSqlQuery<Row>(orgId ? `
    select f.rule_code as rule_code,
           count(*) filter (where f.evidence_status in ('ambiguous','unverified')) as ambiguous,
           count(*) as total
    ${joined} and f.rule_code is not null group by 1 having count(*) filter (where f.evidence_status in ('ambiguous','unverified')) > 0
    order by ambiguous desc limit 8` : null, [joined])

  const r0 = rates.items[0] ?? {}
  const all = [byCategory, bySeverity, byEvidence, topRules, topTerms, trend, rates, byType, workload, severityByRule]
  if (all.some((q) => q.loading)) return <Loading label="Calculating analytics from stored review records…" />
  if (byCategory.error) return <Card pad><div className="err">{byCategory.error}</div></Card>

  const exportReport = async () => {
    const lines = [
      `# BrandGuard AI — governance analytics report`,
      ``,
      `Workspace: ${activeOrg?.name}`,
      `Generated: ${new Date().toISOString()}`,
      `Period: last ${days} days`,
      ``,
      `## Review outcomes`,
      `- Reviews: ${r0.total ?? 0}`,
      `- Revised at least once: ${r0.revised ?? 0} (${pct(Number(r0.revised ?? 0), Number(r0.total ?? 0))})`,
      `- Approved: ${r0.approved ?? 0}, rejected: ${r0.rejected ?? 0}`,
      `- Average turnaround: ${r0.hours ? Number(r0.hours).toFixed(1) + ' hours' : 'not calculable'}`,
      ``,
      `## Findings by category`,
      ...byCategory.items.map((r) => `- ${CATEGORY_LABEL[r.category] ?? r.category}: ${r.n}`),
      ``,
      `## Findings by severity`,
      ...bySeverity.items.map((r) => `- ${r.severity}: ${r.n}`),
      ``,
      `## Most frequently flagged rules`,
      ...topRules.items.map((r) => `- ${r.rule_code} (${r.title}): ${r.n} finding(s) across ${r.reviews} review(s)`),
      ``,
      `## Most frequent terminology violations`,
      ...topTerms.items.map((r) => `- “${r.term}” (${r.rule_code}): ${r.n}`),
      ``,
      `## Rules most often producing ambiguous findings`,
      ...severityByRule.items.map((r) => `- ${r.rule_code}: ${r.ambiguous} ambiguous of ${r.total} finding(s)`),
      ``,
      `Every figure above is counted from stored BrandGuard records. Association is not causation: a rule appearing`,
      `in many findings is a signal to investigate, not proof that the rule caused the problem.`,
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' })
    const file = new File([blob], `brandguard-analytics-${new Date().toISOString().slice(0, 10)}.md`, { type: 'text/markdown' })
    try {
      const { lemmaClient } = await import('../lemma-client')
      const res: any = await lemmaClient.files.upload(file, { directoryPath: `/brandguard/${activeOrg?.slug ?? 'workspace'}/reports`, searchEnabled: true })
      window.alert(`Report saved to pod files at ${res.path ?? res.file_path}`)
    } catch (e: any) {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.name
      a.click()
      URL.revokeObjectURL(url)
    }
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Governance analytics</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Every number is counted from stored reviews, findings and decisions. Nothing here is estimated.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={exportReport}><Download size={14} /> Export report</Button>
        <Button size="sm" onClick={() => all.forEach((q) => q.refresh())}><RefreshCw size={14} /> Recalculate</Button>
      </div>

      <Card pad>
        <div className="row">
          <div>
            <label className="small muted" htmlFor="an-days">Date range</label>
            <select id="an-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={180}>Last 180 days</option>
              <option value={365}>Last 12 months</option>
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="an-type">Content type</label>
            <select id="an-type" value={ctype} onChange={(e) => setCtype(e.target.value)}>
              <option value="">All types</option>
              {CONTENT_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="an-status">Review status</label>
            <select id="an-status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any status</option>
              {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="an-sev">Severity</label>
            <select id="an-sev" value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">Any severity</option>
              {['critical', 'high', 'medium', 'low', 'informational'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="an-cat">Category</label>
            <select id="an-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Any category</option>
              {Object.entries(CATEGORY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
        </div>
      </Card>

      <div className="grid cols-4">
        <Card><div className="metric"><div className="metric-label">Reviews in period</div><div className="metric-value">{r0.total ?? 0}</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Revision rate</div><div className="metric-value">{pct(Number(r0.revised ?? 0), Number(r0.total ?? 0))}</div><div className="metric-note">{r0.revised ?? 0} of {r0.total ?? 0} revised at least once</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Approval rate</div><div className="metric-value">{pct(Number(r0.approved ?? 0), Number(r0.approved ?? 0) + Number(r0.rejected ?? 0))}</div><div className="metric-note">{r0.approved ?? 0} approved, {r0.rejected ?? 0} rejected</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Average turnaround</div><div className="metric-value">{r0.hours ? `${Number(r0.hours).toFixed(1)}h` : '—'}</div><div className="metric-note">submission to decision</div></div></Card>
      </div>

      {byCategory.items.length === 0 ? (
        <Card pad><Empty title="No findings in this period" hint="Change the filters or run more reviews to build the analytics base." /></Card>
      ) : (
        <>
          <div className="split">
            <Card>
              <CardHead title="Findings by category" subtitle="Click a bar to open the matching reviews" />
              <div className="chart-box" style={{ height: 280 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byCategory.items} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e9edf1" vertical={false} />
                    <XAxis dataKey="category" tickFormatter={(v) => CATEGORY_LABEL[v] ?? v} tick={{ fontSize: 11 }} interval={0} angle={-18} textAnchor="end" height={64} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(v: any) => [v, 'findings']} labelFormatter={(v) => CATEGORY_LABEL[v as string] ?? v} />
                    <RBar dataKey="n" radius={[3, 3, 0, 0]} onClick={(d: any) => navigate({ page: 'reviews' })}>
                      {byCategory.items.map((_, i) => <Cell key={i} fill={CAT_COLORS[i % CAT_COLORS.length]} />)}
                    </RBar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card>
              <CardHead title="Findings by severity" />
              <div className="chart-box" style={{ height: 280 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={bySeverity.items} dataKey="n" nameKey="severity" outerRadius={90} label={(e: any) => `${e.severity} (${e.n})`}>
                      {bySeverity.items.map((r) => <Cell key={r.severity} fill={SEV_COLORS[r.severity] ?? '#475467'} />)}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="legend">
                {bySeverity.items.map((r) => (
                  <span key={r.severity}><i style={{ background: SEV_COLORS[r.severity] }} />{r.severity}: {r.n}</span>
                ))}
              </div>
            </Card>
          </div>

          <div className="split">
            <Card>
              <CardHead title="Findings over time" subtitle="Weekly buckets from stored records" />
              <div className="chart-box" style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trend.items} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e9edf1" />
                    <XAxis dataKey="week" tick={{ fontSize: 11 }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="reviews" stroke="#1d4ed8" strokeWidth={2} name="Reviews" />
                    <Line type="monotone" dataKey="findings" stroke="#b54708" strokeWidth={2} name="Findings" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card>
              <CardHead title="Evidence quality" subtitle="How findings were grounded" />
              <div className="chart-box" style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byEvidence.items} layout="vertical" margin={{ top: 8, right: 24, bottom: 8, left: 40 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e9edf1" horizontal={false} />
                    <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="status" tick={{ fontSize: 11 }} width={110} />
                    <Tooltip />
                    <RBar dataKey="n" fill="#0f7a55" radius={[0, 3, 3, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          <div className="split-even">
            <Card>
              <CardHead title="Most frequently flagged guidelines" />
              {topRules.items.length === 0 ? <Empty title="No rule-linked findings in this period" /> : (
                <div className="table-wrap">
                  <table aria-label="Most flagged rules">
                    <thead><tr><th>Rule</th><th>Findings</th><th>Reviews</th><th /></tr></thead>
                    <tbody>
                      {topRules.items.map((r) => (
                        <tr key={`${r.rule_code}-${r.title}`}>
                          <td><Badge tone="b-accent">{r.rule_code}</Badge> <span className="small">{r.title}</span></td>
                          <td className="mono">{r.n}</td>
                          <td className="mono">{r.reviews}</td>
                          <td><button className="btn-link" onClick={() => navigate({ page: 'reviews' })}>records</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card>
              <CardHead title="Most frequent terminology violations" />
              {topTerms.items.length === 0 ? <Empty title="No terminology findings in this period" /> : (
                <div className="table-wrap">
                  <table aria-label="Terminology violations">
                    <thead><tr><th>Term</th><th>Rule</th><th>Occurrences</th></tr></thead>
                    <tbody>
                      {topTerms.items.map((r, i) => (
                        <tr key={i}><td>“{r.term}”</td><td className="mono">{r.rule_code}</td><td className="mono">{r.n}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          <div className="split-even">
            <Card>
              <CardHead title="Content types needing correction" />
              {byType.items.length === 0 ? <Empty title="No reviews in this period" /> : (
                <div className="table-wrap">
                  <table aria-label="Revision rate by content type">
                    <thead><tr><th>Content type</th><th>Reviews</th><th>Revised</th><th>Revision rate</th></tr></thead>
                    <tbody>
                      {byType.items.map((r) => (
                        <tr key={r.content_type}>
                          <td>{titleCase(r.content_type)}</td>
                          <td className="mono">{r.n}</td>
                          <td className="mono">{r.revised}</td>
                          <td>{pct(Number(r.revised), Number(r.n))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card>
              <CardHead title="Reviewer workload" subtitle="Counts of recorded decisions — not a performance score" />
              {workload.items.length === 0 ? <Empty title="No decisions recorded yet" /> : (
                <div className="table-wrap">
                  <table aria-label="Reviewer workload">
                    <thead><tr><th>Reviewer</th><th>Decisions</th><th>Approvals</th><th>Change requests</th></tr></thead>
                    <tbody>
                      {workload.items.map((r) => (
                        <tr key={r.reviewer_email}>
                          <td className="small">{r.reviewer_email}</td>
                          <td className="mono">{r.decisions}</td>
                          <td className="mono">{r.approvals}</td>
                          <td className="mono">{r.changes}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          <Card>
            <CardHead title="Guidelines associated with ambiguous findings"
              subtitle="A signal to investigate the rule's wording — not proof that the rule is wrong" />
            {severityByRule.items.length === 0 ? <Empty title="No ambiguous findings in this period" /> : (
              <div className="table-wrap">
                <table aria-label="Ambiguous findings by rule">
                  <thead><tr><th>Rule</th><th>Ambiguous findings</th><th>Total findings</th><th>Share</th></tr></thead>
                  <tbody>
                    {severityByRule.items.map((r) => (
                      <tr key={r.rule_code}>
                        <td className="mono">{r.rule_code}</td>
                        <td className="mono">{r.ambiguous}</td>
                        <td className="mono">{r.total}</td>
                        <td>{pct(Number(r.ambiguous), Number(r.total))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  )
}

import React, { useMemo, useState } from 'react'
import { AlertTriangle, BellRing, RefreshCw, Share2 } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, useToast } from '../components/ui'
import { fmtDate, fmtDateTime, titleCase } from '../lib/types'
import type { Route } from '../components/Shell'

const STATUS_TONE: Record<string, string> = {
  valid: 'b-ok', expiring: 'b-medium', expired: 'b-critical', unsupported: 'b-critical',
  orphaned: 'b-high', superseded: 'b-info',
}
const NODE_FILL: Record<string, string> = {
  claim: '#eaf0fe', fact: '#e7f6ef', document: '#fdf6e3', owner: '#eef1f4', review: '#f7f8fa',
}
const NODE_STROKE: Record<string, string> = {
  claim: '#1d4ed8', fact: '#0f7a55', document: '#8a6100', owner: '#475467', review: '#c9d0d8',
}

export function Provenance({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('')
  const [focus, setFocus] = useState<string | null>(null)

  const links = useRows<Row>('bg_claim_provenance', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'status', direction: 'asc' }], limit: 400,
  }, [orgId])
  const graph = useRows<Row>('bg_claim_provenance', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 400 }, [orgId])

  const counts = useMemo(() => {
    const out: Record<string, number> = {}
    links.items.forEach((l) => { out[l.status] = (out[l.status] ?? 0) + 1 })
    return out
  }, [links.items])

  const alerts = links.items.filter((l) => ['expired', 'expiring', 'unsupported', 'orphaned'].includes(l.status))
  const visible = links.items.filter((l) => !filter || l.status === filter)

  const refresh = async () => {
    setBusy(true)
    const res = await runFn('bg_provenance_scan', { organization_id: orgId })
    setBusy(false)
    if (!res.ok) { toast.push(res.error ?? 'The provenance scan failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Provenance refreshed.', 'success')
    links.refresh(); graph.refresh()
  }

  // simple layered graph layout: claim -> source/owner/review columns
  const layout = useMemo(() => {
    const claims = links.items.filter((l) => l.source_type === 'fact' || focus === l.id)
    const sources = new Map<string, Row>()
    claims.forEach((c) => {
      if (c.document_id) sources.set(`doc:${c.document_id}`, { id: `doc:${c.document_id}`, label: c.document_title ?? 'Source document', kind: 'document' })
      if (c.fact_id) sources.set(`fact:${c.fact_id}`, { id: `fact:${c.fact_id}`, label: 'Approved fact', kind: 'fact' })
      if (c.owner) sources.set(`owner:${c.owner}`, { id: `owner:${c.owner}`, label: c.owner, kind: 'owner' })
    })
    return { claims: claims.slice(0, 14), sources: [...sources.values()].slice(0, 12) }
  }, [links.items, focus])

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Claim provenance</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Where every claim came from — the document, the passage, the owner and the expiry — and whether that
            support still holds today.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => links.refresh()}><RefreshCw size={14} /> Refresh</Button>
        <Button size="sm" variant="primary" disabled={busy} onClick={refresh}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <Share2 size={14} />} Re-evaluate provenance
        </Button>
      </div>

      <div className="grid cols-4">
        <Card><div className="metric"><div className="metric-label">Traced claims</div><div className="metric-value">{links.items.length}</div><div className="metric-note">claims with a recorded source</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Valid support</div><div className="metric-value">{counts.valid ?? 0}</div><div className="metric-note">source present and current</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Expired or expiring</div><div className="metric-value">{Number(counts.expired ?? 0) + Number(counts.expiring ?? 0)}</div><div className="metric-note">needs re-verification</div></div></Card>
        <Card><div className="metric"><div className="metric-label">Unsupported or orphaned</div><div className="metric-value">{Number(counts.unsupported ?? 0) + Number(counts.orphaned ?? 0)}</div><div className="metric-note">no usable evidence</div></div></Card>
      </div>

      {alerts.length > 0 ? (
        <Card>
          <CardHead title="Alerts" subtitle="Claims whose evidence has lapsed or never existed" actions={<Badge tone="b-critical">{alerts.length}</Badge>} />
          <div className="stack" style={{ padding: 16 }}>
            {alerts.slice(0, 8).map((a) => (
              <div key={a.id} className="warn-msg">
                <div className="row" style={{ gap: 6 }}>
                  <BellRing size={13} />
                  <b className="small">{String(a.claim_text).slice(0, 110)}</b>
                  <Badge tone={STATUS_TONE[a.status] ?? 'b-info'}>{a.status}</Badge>
                </div>
                <div className="small">{a.status_reason}</div>
                <div className="muted small">
                  Owner: {a.owner ?? 'unassigned'} · {a.expires_at ? `expires ${fmtDate(a.expires_at)}` : 'no expiry recorded'}
                </div>
              </div>
            ))}
            <div className="muted small">
              Alerts are also sent to the workspace's administrators and brand managers, at most once a week per claim.
            </div>
          </div>
        </Card>
      ) : null}

      <Card>
        <CardHead title="Evidence graph" subtitle="Claims on the left, the sources that support them on the right" />
        {links.loading ? <Loading /> : links.items.length === 0 ? (
          <Empty title="No claims traced yet"
            hint="Re-evaluate provenance to build the graph from the approved facts library and the findings that relied on it." />
        ) : (
          <div style={{ overflowX: 'auto', padding: 12 }}>
            <svg width={860} height={Math.max(240, layout.claims.length * 54 + 40)} role="img"
              aria-label="Claim provenance graph">
              {layout.claims.map((claim, i) => {
                const y = 30 + i * 54
                const targets = layout.sources.filter((s) =>
                  (claim.fact_id && s.id === `fact:${claim.fact_id}`) ||
                  (claim.document_id && s.id === `doc:${claim.document_id}`) ||
                  (claim.owner && s.id === `owner:${claim.owner}`))
                return (
                  <g key={claim.id}>
                    <rect x={8} y={y - 16} width={330} height={34} rx={6}
                      fill={NODE_FILL.claim} stroke={NODE_STROKE.claim} strokeWidth={1} />
                    <text x={18} y={y + 5} fontSize={11.5} fill="#11161c">{String(claim.claim_text).slice(0, 52)}</text>
                    {targets.map((t, k) => {
                      const ty = 30 + layout.sources.indexOf(t) * 54
                      const colour = STATUS_TONE[claim.status] === 'b-ok' ? '#0f7a55'
                        : claim.status === 'expired' || claim.status === 'unsupported' ? '#b42318' : '#b54708'
                      return (
                        <g key={t.id}>
                          <path d={`M 338 ${y} C 430 ${y}, 430 ${ty}, 520 ${ty}`} fill="none" stroke={colour} strokeWidth={1.4} />
                          <rect x={524} y={ty - 16} width={320} height={34} rx={6}
                            fill={NODE_FILL[t.kind]} stroke={NODE_STROKE[t.kind]} strokeWidth={1} />
                          <text x={534} y={ty + 5} fontSize={11.5} fill="#11161c">
                            {t.kind === 'fact' ? 'approved fact' : String(t.label).slice(0, 46)}
                          </text>
                        </g>
                      )
                    })}
                  </g>
                )
              })}
            </svg>
            <div className="legend">
              <span><i style={{ background: NODE_STROKE.claim }} /> claim</span>
              <span><i style={{ background: NODE_STROKE.fact }} /> approved fact</span>
              <span><i style={{ background: NODE_STROKE.document }} /> source document</span>
              <span><i style={{ background: NODE_STROKE.owner }} /> owner</span>
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardHead title="Claim register" subtitle="Every claim and the source behind it"
          actions={
            <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 'auto' }}>
              <option value="">All statuses</option>
              {['valid', 'expiring', 'expired', 'unsupported', 'orphaned', 'superseded'].map((s) => (
                <option key={s} value={s}>{s}</option>))}
            </select>
          } />
        {visible.length === 0 ? <Empty title="Nothing in this view" /> : (
          <div className="table-wrap">
            <table aria-label="Claim provenance">
              <thead><tr><th>Status</th><th>Claim</th><th>Source</th><th>Owner</th><th>Expires</th><th>Relied on by</th><th>Checked</th></tr></thead>
              <tbody>
                {visible.map((l) => (
                  <tr key={l.id}>
                    <td><Badge tone={STATUS_TONE[l.status] ?? 'b-info'}>{l.status}</Badge>
                      <div className="muted small" style={{ maxWidth: 240 }}>{l.status_reason}</div></td>
                    <td className="small" style={{ maxWidth: 320 }}>{String(l.claim_text).slice(0, 160)}</td>
                    <td className="small">
                      {l.document_title ?? (l.source_location ? 'document not linked' : '—')}
                      {l.source_location ? <div className="muted small">{l.source_location}</div> : null}
                      {l.evidence_strength ? <Badge tone="b-info">{l.evidence_strength} evidence</Badge> : null}
                    </td>
                    <td className="small">{l.owner ?? '—'}</td>
                    <td className="muted small nowrap">{fmtDate(l.expires_at)}</td>
                    <td className="small">
                      {l.review_id ? (
                        <button className="btn-link" onClick={() => navigate({ page: 'review', id: l.review_id })}>
                          {String(l.review_title ?? 'review').slice(0, 34)}
                        </button>
                      ) : <span className="muted">—</span>}
                    </td>
                    <td className="muted small nowrap">{fmtDateTime(l.last_checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ padding: 16 }} className="muted small">
          <AlertTriangle size={13} /> A claim is only as good as its source. When a document is retired or a fact
          expires, every claim that depended on it is flagged here rather than silently trusted.
        </div>
      </Card>
    </div>
  )
}

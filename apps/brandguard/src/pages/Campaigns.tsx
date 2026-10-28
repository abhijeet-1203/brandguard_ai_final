import React, { useState } from 'react'
import { AlertTriangle, RefreshCw, Share2 } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows, useSqlQuery } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, useToast } from '../components/ui'
import { fmtDateTime, relTime, titleCase } from '../lib/types'
import type { Route } from '../components/Shell'

export function Campaigns({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)

  const campaigns = useSqlQuery<Row>(orgId ? `
    select coalesce(campaign, '(no campaign)') as campaign,
           count(*) as reviews, count(distinct channel) as channels,
           min(created_at) as first_seen, max(created_at) as last_seen
    from bg_reviews where organization_id = '${orgId}' and status not in ('draft','archived')
    group by 1 order by reviews desc` : null, [orgId])

  const scans = useRows<Row>('bg_campaign_scans', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'scanned_at', direction: 'desc' }], limit: 40,
  }, [orgId])

  const scan = async (campaign?: string) => {
    setBusy(true)
    const res = await runFn('bg_campaign_scan', { organization_id: orgId, campaign: campaign ?? null })
    setBusy(false)
    if (!res.ok) { toast.push(res.error ?? 'The scan failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Scan complete.', 'success')
    scans.refresh()
    const first = (out.scans ?? [])[0]
    if (first) setSelected(first.id ?? first.campaign)
  }

  const latest = scans.items.find((s) => s.id === selected) ?? scans.items[0]

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Cross-channel consistency</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Compares every channel of a campaign at once — prices, warranties, product names, offers and slogans —
            and scores how far the channels agree.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => scans.refresh()}><RefreshCw size={14} /> Refresh</Button>
        <Button size="sm" variant="primary" disabled={busy} onClick={() => scan()}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <Share2 size={14} />} Scan all campaigns
        </Button>
      </div>

      <Card>
        <CardHead title="Campaigns in this workspace" subtitle="Scan one campaign to compare its channels" />
        {campaigns.loading ? <Loading /> : campaigns.items.length === 0 ? (
          <Empty title="No submitted content yet" hint="Submit content with a campaign name and it will be grouped here." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Campaigns">
              <thead><tr><th>Campaign</th><th>Submissions</th><th>Channels</th><th>Last activity</th><th /></tr></thead>
              <tbody>
                {campaigns.items.map((c) => (
                  <tr key={c.campaign}>
                    <td style={{ fontWeight: 560 }}>{c.campaign}</td>
                    <td className="mono">{c.reviews}</td>
                    <td className="mono">{c.channels}</td>
                    <td className="muted small nowrap">{relTime(c.last_seen)}</td>
                    <td>
                      <Button size="sm" disabled={busy} onClick={() => scan(c.campaign)}>Scan channels</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {scans.items.length > 0 ? (
        <div className="row">
          <label className="small muted" htmlFor="scan-pick">Scan</label>
          <select id="scan-pick" value={latest?.id ?? ''} onChange={(e) => setSelected(e.target.value)} style={{ width: 'auto', minWidth: 280 }}>
            {scans.items.map((s) => (
              <option key={s.id} value={s.id}>
                {s.campaign} — score {s.consistency_score} ({fmtDateTime(s.scanned_at)})
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {latest ? (
        <>
          <div className="split">
            <Card>
              <CardHead title={`${latest.campaign} — consistency score`}
                subtitle={`${latest.review_count} submission(s) across ${latest.channel_count} channel(s) · scanned ${fmtDateTime(latest.scanned_at)}`}
                actions={<Badge tone={Number(latest.consistency_score) >= 85 ? 'b-ok' : Number(latest.consistency_score) >= 60 ? 'b-medium' : 'b-critical'}>
                  {latest.consistency_score}/100
                </Badge>} />
              <div style={{ padding: 16 }} className="stack">
                <div className="small">{latest.summary}</div>
                <div>
                  <div className="muted small">How the score was reached</div>
                  {(latest.signals?.breakdown ?? []).length === 0 ? (
                    <div className="ok-msg small">No penalties: every comparable signal agrees across channels.</div>
                  ) : (
                    <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                      {(latest.signals?.breakdown ?? []).map((b: Row, i: number) => (
                        <li key={i}>{b.reason} <span className="mono">−{b.points}</span> <SeverityBadge severity={b.severity} /></li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="muted small">
                  100 minus the weighted penalty of each conflict. A conflict is two channels carrying different
                  values for the same thing — not a rule breach in a single document.
                </div>
              </div>
            </Card>
            <Card>
              <CardHead title="Channel coverage" />
              <div style={{ padding: 16 }} className="stack">
                {(latest.channels ?? []).map((c: Row) => (
                  <div key={c.channel}>
                    <div className="row" style={{ gap: 6 }}>
                      <b className="small">{c.channel}</b>
                      <Badge tone="b-info">{c.items.length}</Badge>
                    </div>
                    {c.items.map((i: Row) => (
                      <div key={i.review_id} className="small" style={{ marginLeft: 8 }}>
                        <button className="btn-link" onClick={() => navigate({ page: 'review', id: i.review_id })}>
                          {i.title}
                        </button>
                        <span className="muted"> · {titleCase(i.content_type)} · {i.status}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <Card>
            <CardHead title="Cross-channel conflicts" subtitle="The same subject described differently in two or more channels" />
            {(latest.conflicts ?? []).length === 0 ? (
              <Empty title="No conflicts found" hint="Either the channels agree, or only one channel carries this campaign." />
            ) : (
              <div className="stack" style={{ padding: 16 }}>
                {(latest.conflicts ?? []).map((c: Row, i: number) => (
                  <div key={i} className="finding">
                    <div className="finding-head">
                      <span className="sev-bar" style={{ background: `var(--${c.severity})` }} aria-hidden="true" />
                      <div style={{ flex: 1 }}>
                        <h3>{titleCase(c.type)}: {c.subject}</h3>
                        <div className="row" style={{ gap: 6, marginTop: 4 }}>
                          <SeverityBadge severity={c.severity} />
                          <Badge tone="b-accent">{c.values.length} different value(s)</Badge>
                        </div>
                      </div>
                    </div>
                    <div className="finding-body" style={{ paddingTop: 10 }}>
                      <div className="small">{c.explanation}</div>
                      <div className="diff" style={{ marginTop: 8 }}>
                        {c.values.map((v: Row, k: number) => (
                          <div key={k} className="diff-box">
                            <div className="diff-label">{v.value}</div>
                            {v.items.map((item: Row) => (
                              <div key={item.review_id} className="small">
                                <button className="btn-link" onClick={() => navigate({ page: 'review', id: item.review_id })}>
                                  {item.title}
                                </button>
                                <span className="muted"> · {item.channel || 'unspecified channel'}</span>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                      {c.approved_reference ? (
                        <div className="notice small" style={{ marginTop: 8 }}>
                          <b>Approved reference:</b>{' '}
                          {c.approved_reference.statement
                            ? <>{c.approved_reference.statement}
                              {c.approved_reference.expiration_date ? ` (expires ${String(c.approved_reference.expiration_date).slice(0, 10)})` : ''}</>
                            : <>approved names: {(c.approved_reference.approved_names ?? []).join(', ')}
                              {c.approved_reference.closest ? ` · closest match: ${c.approved_reference.closest} (similarity ${c.approved_reference.similarity})` : ''}</>}
                        </div>
                      ) : (
                        <div className="warn-msg small" style={{ marginTop: 8 }}>
                          No approved fact covers this signal, so there is nothing to align it with yet.
                        </div>
                      )}
                      <div className="small" style={{ marginTop: 8 }}>
                        <AlertTriangle size={13} /> <b>Recommended action:</b> {c.recommended_action}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      ) : null}
    </div>
  )
}

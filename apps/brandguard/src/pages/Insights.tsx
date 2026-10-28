import React, { useState } from 'react'
import { Lightbulb, RefreshCw, Sparkles } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, useToast } from '../components/ui'
import { INSIGHT_LABEL, fmtDate, fmtDateTime } from '../lib/types'
import type { Route } from '../components/Shell'

export function Insights({ navigate }: { navigate?: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const rows = useRows<Row>('bg_insights', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'generated_at', direction: 'desc' }], limit: 100,
  }, [orgId])

  const generate = async () => {
    setBusy('generate')
    const res = await runFn('bg_insights_generate', { organization_id: orgId, action: 'generate', days: 365 })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not compute insights.', 'error'); return }
    const out: any = res.output
    setNote(out.data_note ?? null)
    toast.push(out.message ?? 'Insights computed.', 'success')
    rows.refresh()
  }

  const act = async (insight: Row, action: 'update_status' | 'create_draft_guideline', status?: string) => {
    setBusy(insight.id + action)
    const res = await runFn('bg_insights_generate', {
      organization_id: orgId, action, insight_id: insight.id, status,
      reason: action === 'update_status' ? `marked ${status} from governance memory` : 'drafted from insight',
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    toast.push((res.output as any)?.message ?? 'Done.', 'success')
    rows.refresh()
  }

  const tone: Record<string, string> = {
    new: 'b-accent', investigating: 'b-medium', draft_created: 'b-ok', dismissed: 'b-info', resolved: 'b-ok',
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Governance memory</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Recurring problems and possible guideline gaps, computed from this workspace's actual review history.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={rows.refresh}><RefreshCw size={14} /> Refresh</Button>
        {session.can('authorGuidelines') ? (
          <Button size="sm" variant="primary" disabled={busy === 'generate'} onClick={generate}>
            {busy === 'generate' ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={14} />} Recompute insights
          </Button>
        ) : null}
      </div>

      <Card pad>
        <div className="row small">
          <Lightbulb size={15} />
          <span>
            Patterns are only reported when the evidence supports them: at least 3 cases of the same issue across two
            or more reviews. Association is described, never causation, and the platform never rewrites or approves a
            guideline by itself.
          </span>
        </div>
        {note ? <div className="muted small" style={{ marginTop: 6 }}>Analysed: {note}</div> : null}
      </Card>

      {rows.loading ? <Loading label="Loading insights…" /> : rows.items.length === 0 ? (
        <Card pad>
          <Empty title="No insights computed yet"
            hint="Recompute insights to analyse the review history for recurring problems and guideline gaps." />
        </Card>
      ) : (
        <div className="stack">
          {rows.items.map((i) => (
            <Card key={i.id}>
              <CardHead
                title={i.title}
                subtitle={`${INSIGHT_LABEL[i.insight_type] ?? i.insight_type} · ${i.cases_count ?? 0} of ${i.total_count ?? 0} cases · ${fmtDate(i.period_start)} → ${fmtDate(i.period_end)}`}
                actions={<Badge tone={tone[i.status] ?? 'b-info'}>{(i.status ?? '').replace(/_/g, ' ')}</Badge>}
              />
              <div style={{ padding: '12px 14px' }} className="stack">
                <div className="small">{i.description}</div>
                {i.recommended_action ? (
                  <div className="notice"><b>Recommended action:</b> {i.recommended_action}</div>
                ) : null}
                <div className="row small">
                  {i.guideline_code ? <Badge tone="b-accent">rule {i.guideline_code}</Badge> : null}
                  {i.confidence_note ? <span className="muted">{i.confidence_note}</span> : null}
                  <span className="muted">generated {fmtDateTime(i.generated_at)}</span>
                </div>
                {i.supporting_review_ids?.length ? (
                  <details>
                    <summary className="small">Supporting review records ({i.supporting_review_ids.length})</summary>
                    <ul className="small">
                      {i.supporting_review_ids.map((rid: string) => (
                        <li key={rid}>
                          <button className="btn-link" onClick={() => navigate?.({ page: 'review', id: rid })}>
                            open review {String(rid).slice(0, 8)}…
                          </button>
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                {i.status !== 'insufficient_data' && session.can('actOnInsight') ? (
                  <div className="row" style={{ gap: 6 }}>
                    {i.status !== 'draft_created' ? (
                      <Button size="sm" variant="primary" disabled={busy === i.id + 'create_draft_guideline'}
                        onClick={() => act(i, 'create_draft_guideline')}>
                        <Sparkles size={13} /> Draft a guideline update
                      </Button>
                    ) : <span className="muted small">A draft rule was created from this insight — approve it in Brand guidelines.</span>}
                    <Button size="sm" disabled={busy === i.id + 'update_status'} onClick={() => act(i, 'update_status', 'investigating')}>Investigate</Button>
                    <Button size="sm" disabled={busy === i.id + 'update_status'} onClick={() => act(i, 'update_status', 'dismissed')}>Dismiss</Button>
                    <Button size="sm" disabled={busy === i.id + 'update_status'} onClick={() => act(i, 'update_status', 'resolved')}>Mark resolved</Button>
                  </div>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

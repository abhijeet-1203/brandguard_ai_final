import React, { useState } from 'react'
import { GitBranch, RefreshCw, Sparkles } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, useToast } from '../components/ui'
import { fmtDateTime, titleCase } from '../lib/types'
import type { Route } from '../components/Shell'

const CAUSE_LABEL: Record<string, string> = {
  rule_clarity: 'Likely rule ambiguity',
  outdated_source: 'Outdated source document',
  training_gap: 'Training or coaching gap',
  campaign_specific: 'One-campaign spike',
  process_gap: 'Process gap (checked too late)',
  insufficient_data: 'Not enough data',
}
const CAUSE_TONE: Record<string, string> = {
  rule_clarity: 'b-medium', outdated_source: 'b-high', training_gap: 'b-info',
  campaign_specific: 'b-accent', process_gap: 'b-medium', insufficient_data: 'b-info',
}

export function Policy({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [busy, setBusy] = useState<string | null>(null)

  const rows = useRows<Row>('bg_policy_suggestions', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'generated_at', direction: 'desc' }], limit: 60,
  }, [orgId])

  const generate = async () => {
    setBusy('generate')
    const res = await runFn('bg_root_cause', { organization_id: orgId, action: 'generate' })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Root-cause analysis failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Analysis complete.', 'success')
    rows.refresh()
  }

  const act = async (s: Row, action: 'create_draft' | 'dismiss') => {
    setBusy(s.id + action)
    const res = await runFn('bg_root_cause', {
      organization_id: orgId, action, suggestion_id: s.id,
      reason: action === 'dismiss' ? 'dismissed from root-cause review' : 'drafted from root-cause analysis',
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    toast.push((res.output as any)?.message ?? 'Done.', 'success')
    rows.refresh()
  }

  const canAct = session.can('actOnInsight')

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Root cause &amp; policy suggestions</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Turns repeated breaches into an explanation and a proposed rule. Nothing changes until a brand manager
            approves it through the normal guideline workflow.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => rows.refresh()}><RefreshCw size={14} /> Refresh</Button>
        <Button size="sm" variant="primary" disabled={busy === 'generate'} onClick={generate}>
          {busy === 'generate' ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={14} />} Analyse review history
        </Button>
      </div>

      <Card pad>
        <div className="row small">
          <GitBranch size={15} />
          <span>
            Grouped by rule, campaign, content type and author. A cause is an <b>interpretation of stored findings</b>,
            not proof — the supporting cases are listed on every suggestion so the reasoning can be checked.
          </span>
        </div>
      </Card>

      {rows.loading ? <Loading label="Loading suggestions…" /> : rows.items.length === 0 ? (
        <Card pad><Empty title="No analysis yet"
          hint="Run the analysis to group recurring breaches and see what the likely cause is." /></Card>
      ) : (
        <div className="stack">
          {rows.items.map((s) => (
            <Card key={s.id}>
              <CardHead title={s.title}
                subtitle={`${CAUSE_LABEL[s.root_cause_type] ?? s.root_cause_type} · ${(s.evidence ?? []).length} supporting case(s) · generated ${fmtDateTime(s.generated_at)}`}
                actions={<Badge tone={CAUSE_TONE[s.root_cause_type] ?? 'b-info'}>{(s.status ?? '').replace(/_/g, ' ')}</Badge>} />
              <div style={{ padding: 14 }} className="stack">
                <div className="small">{s.rationale}</div>
                <div className="row small" style={{ gap: 6 }}>
                  {s.rule_code ? <Badge tone="b-accent">rule {s.rule_code}</Badge> : null}
                  {(s.affected_campaigns ?? []).map((c: string) => <Badge key={c} tone="b-info">{c}</Badge>)}
                  {(s.affected_content_types ?? []).map((c: string) => <Badge key={c} tone="b-info">{titleCase(c)}</Badge>)}
                </div>

                {s.proposed_rule ? (
                  <div>
                    <div className="muted small">Proposed rule</div>
                    <div className="diff">
                      <div className="diff-box">
                        <div className="diff-label">Current rule</div>
                        <div className="small">{s.root_cause_detail}</div>
                      </div>
                      <div className="diff-box after">
                        <div className="diff-label">{s.proposed_rule.title}</div>
                        <div className="small">{s.proposed_rule.rule_text}</div>
                        <div className="muted small">
                          {s.proposed_rule.enforcement} · severity {s.proposed_rule.severity}
                          {s.proposed_rule.based_on_rule ? ` · revises ${s.proposed_rule.based_on_rule}` : ''}
                        </div>
                        {s.proposed_rule.source_quote ? (
                          <div className="muted small">Evidence: “{String(s.proposed_rule.source_quote).slice(0, 160)}”</div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ) : null}

                {(s.evidence ?? []).length > 0 ? (
                  <details>
                    <summary className="small">Supporting cases ({(s.evidence ?? []).length})</summary>
                    <div className="table-wrap">
                      <table aria-label="Supporting cases">
                        <thead><tr><th>Review</th><th>Campaign</th><th>Type</th><th>Quoted</th></tr></thead>
                        <tbody>
                          {(s.evidence ?? []).map((e: Row, i: number) => (
                            <tr key={i}>
                              <td><button className="btn-link" onClick={() => navigate({ page: 'review', id: e.review_id })}>
                                {String(e.title).slice(0, 40)}</button></td>
                              <td className="small">{e.campaign ?? '—'}</td>
                              <td className="small">{titleCase(e.content_type)}</td>
                              <td className="small">{String(e.quoted ?? '').slice(0, 90)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                ) : null}

                {s.confidence_note ? <div className="muted small">{s.confidence_note}</div> : null}

                {canAct && s.status === 'proposed' ? (
                  <div className="row" style={{ gap: 6 }}>
                    <Button size="sm" variant="primary" disabled={busy === s.id + 'create_draft'}
                      onClick={() => act(s, 'create_draft')}>
                      <Sparkles size={13} /> Create draft rule
                    </Button>
                    <Button size="sm" disabled={busy === s.id + 'dismiss'} onClick={() => act(s, 'dismiss')}>
                      Dismiss
                    </Button>
                    <span className="muted small">
                      A draft rule still needs approval in Brand guidelines before it is enforced.
                    </span>
                  </div>
                ) : null}
                {s.status === 'draft_created' ? (
                  <div className="ok-msg small">
                    A draft rule was created from this suggestion — review and approve it in{' '}
                    <button className="btn-link" onClick={() => navigate({ page: 'guidelines' })}>Brand guidelines</button>.
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

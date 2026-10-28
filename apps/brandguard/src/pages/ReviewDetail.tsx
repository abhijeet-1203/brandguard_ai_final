import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, Check, CheckCircle2, GitCompare, MessageSquare, PlayCircle, RotateCcw,
  Send, ShieldAlert, Sparkles, X, XCircle,
} from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useAnalysisWatch, useRows, useSqlQuery } from '../lib/hooks'
import { runFn } from '../lib/api'
import {
  Badge, Button, Card, CardHead, Empty, ErrorNote, Field, Loading, Modal, SeverityBadge, StatusBadge, useToast,
} from '../components/ui'
import {
  CATEGORY_LABEL, EVIDENCE_LABEL, FINDING_STATUS_LABEL, STATUS_LABEL, confidenceLabel, fmtDateTime, relTime, Row, titleCase,
} from '../lib/types'
import type { Route } from '../components/Shell'

type Tab = 'findings' | 'content' | 'minimal' | 'discussion' | 'decisions' | 'analysis'

export function ReviewDetail({ id, navigate }: { id: string; navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('findings')
  const [busy, setBusy] = useState<string | null>(null)
  const [actionModal, setActionModal] = useState<null | 'approve' | 'reject' | 'request_changes' | 'escalate'>(null)
  const [reason, setReason] = useState('')
  const [override, setOverride] = useState(false)

  const reviewQ = useSqlQuery<Row>(`select * from bg_reviews where id = '${safeId(id)}'`)
  const review = reviewQ.items[0]
  const findingsQ = useRows<Row>('bg_findings', { filters: [{ field: 'review_id', op: 'eq', value: id }], sort: [{ field: 'created_at', direction: 'desc' }], limit: 300 }, [id])
  const versionsQ = useRows<Row>('bg_review_versions', { filters: [{ field: 'review_id', op: 'eq', value: id }], sort: [{ field: 'version', direction: 'desc' }], limit: 100 }, [id])
  const commentsQ = useRows<Row>('bg_comments', { filters: [{ field: 'review_id', op: 'eq', value: id }], sort: [{ field: 'created_at', direction: 'asc' }], limit: 200 }, [id])
  const decisionsQ = useRows<Row>('bg_approvals', { filters: [{ field: 'review_id', op: 'eq', value: id }], sort: [{ field: 'created_at', direction: 'desc' }], limit: 100 }, [id])
  const runsQ = useRows<Row>('bg_analysis_runs', { filters: [{ field: 'review_id', op: 'eq', value: id }], sort: [{ field: 'started_at', direction: 'desc' }], limit: 20 }, [id])
  const editsQ = useRows<Row>('bg_edit_reports', { filters: [{ field: 'review_id', op: 'eq', value: id }], sort: [{ field: 'created_at', direction: 'desc' }], limit: 20 }, [id])

  const analysing = review?.status === 'analysing' || review?.analysis_status === 'running'
  useAnalysisWatch(id, Boolean(analysing), () => {
    void reviewQ.refresh(); void findingsQ.refresh(); void runsQ.refresh(); void versionsQ.refresh()
  })

  const refreshAll = () => { void reviewQ.refresh(); void findingsQ.refresh(); void versionsQ.refresh(); void commentsQ.refresh(); void decisionsQ.refresh(); void runsQ.refresh(); void editsQ.refresh() }

  const [selectedFinding, setSelectedFinding] = useState<string | null>(null)
  useEffect(() => {
    if (!selectedFinding && findingsQ.items.length) setSelectedFinding(findingsQ.items[0].id)
  }, [findingsQ.items, selectedFinding])

  const openFindings = findingsQ.items.filter((f) => ['open', 'escalated', 'accepted'].includes(f.review_status))
  const blocking = openFindings.filter((f) => ['critical', 'high'].includes(f.severity))
  const accepted = findingsQ.items.filter((f) => f.review_status === 'accepted' && (f.suggested_correction ?? '').trim())

  const call = async (fn: string, input: Row, label: string, after?: () => void) => {
    setBusy(label)
    const res = await runFn(fn, input)
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return null }
    const out: any = res.output ?? {}
    toast.push(out.message ?? 'Done.', out.blocked ? 'error' : 'success')
    if (out.rescan_required) {
      await runFn('bg_review_analyse', { review_id: id })
    }
    refreshAll()
    after?.()
    return out
  }

  if (reviewQ.loading) return <Loading label="Loading review…" />
  if (!review) return <Card pad><Empty title="Review not found" hint="It may have been archived or you may not have access to it." action={<Button onClick={() => navigate({ page: 'reviews' })}>Back to reviews</Button>} /></Card>

  const content = review.current_text ?? review.source_text ?? ''

  return (
    <div className="stack">
      <div className="row">
        <Button size="sm" onClick={() => navigate({ page: 'reviews' })}><ArrowLeft size={14} /> Reviews</Button>
        <div className="spacer" />
        <Button size="sm" onClick={refreshAll}>Refresh</Button>
        {review.status !== 'analysing' && session.can('createContent') ? (
          <Button size="sm" variant="primary" disabled={busy === 'analyse'}
            onClick={() => call('bg_review_analyse', { review_id: id }, 'analyse')}>
            <PlayCircle size={14} /> {review.analysis_status === 'completed' ? 'Re-run analysis' : 'Run analysis'}
          </Button>
        ) : null}
      </div>

      <Card pad>
        <div className="row">
          <div style={{ minWidth: 0 }}>
            <h1>{review.title}</h1>
            <div className="muted small">
              {titleCase(review.content_type)}{review.campaign ? ` · ${review.campaign}` : ''}
              {review.channel ? ` · ${review.channel}` : ''}{review.audience ? ` · ${review.audience}` : ''}
              {review.language ? ` · ${review.language}` : ''}
            </div>
          </div>
          <div className="spacer" />
          <StatusBadge status={review.status} label={STATUS_LABEL[review.status]} />
          {review.risk_level ? <SeverityBadge severity={review.risk_level} /> : null}
          <Badge tone="b-info">v{review.current_version}</Badge>
        </div>
        <div className="grid cols-4" style={{ marginTop: 14 }}>
          <div><div className="muted small">Creator</div><div>{review.created_by_email}</div></div>
          <div><div className="muted small">Assigned reviewer</div>
            <div>{review.assigned_reviewer_email ?? <span className="muted">unassigned</span>}</div></div>
          <div><div className="muted small">Required reviewer role</div>
            <div>{review.required_reviewer_role ? titleCase(review.required_reviewer_role) : <span className="muted">—</span>}</div></div>
          <div><div className="muted small">Submitted</div><div>{fmtDateTime(review.submitted_at)}</div></div>
        </div>
        {review.decision_reason ? (
          <div className="notice" style={{ marginTop: 12 }}><b>Last decision note:</b> {review.decision_reason}</div>
        ) : null}
        {review.error_message ? (
          <div className="err" style={{ marginTop: 12 }}>
            <b>Analysis failed.</b> {review.error_message}
          </div>
        ) : null}
        {review.blocked_reason ? <div className="warn-msg" style={{ marginTop: 12 }}>{review.blocked_reason}</div> : null}
      </Card>

      <Card pad>
        <div className="row">
          <span className="muted small">{openFindings.length} open finding(s) · {blocking.length} blocking · {accepted.length} accepted correction(s) ready to apply</span>
          <div className="spacer" />
          {['draft', 'changes_requested', 'analysis_failed'].includes(review.status) ? (
            <Button size="sm" onClick={() => call('bg_review_workflow', { organization_id: review.organization_id, review_id: id, action: review.status === 'draft' ? 'submit' : 'resubmit' }, 'submit')}>
              <Send size={14} /> {review.status === 'draft' ? 'Submit for review' : 'Resubmit'}
            </Button>
          ) : null}
          {accepted.length > 0 ? (
            <Button size="sm" variant="primary" disabled={busy === 'apply'}
              onClick={() => call('bg_correction_action', { organization_id: review.organization_id, review_id: id, action: 'apply', apply_all_accepted: true }, 'apply')}>
              <Check size={14} /> Apply {accepted.length} accepted correction(s)
            </Button>
          ) : null}
          {session.can('decideReview') && ['awaiting_review', 'resubmitted'].includes(review.status) ? (
            <>
              <Button size="sm" onClick={() => { setActionModal('request_changes'); setReason('') }}>Request changes</Button>
              <Button size="sm" variant="danger" onClick={() => { setActionModal('reject'); setReason('') }}>Reject</Button>
              <Button size="sm" variant="primary" onClick={() => { setActionModal('approve'); setReason(''); setOverride(false) }}>Approve</Button>
            </>
          ) : null}
          {session.can('decideReview') && ['awaiting_review', 'resubmitted'].includes(review.status) ? (
            <Button size="sm" onClick={() => { setActionModal('escalate'); setReason('') }}><ShieldAlert size={14} /> Escalate</Button>
          ) : null}
        </div>
      </Card>

      <div className="row" role="tablist" aria-label="Review sections">
        {([['findings', `Findings (${findingsQ.items.length})`], ['content', 'Content & versions'],
           ['minimal', `Minimal edit (${editsQ.items.length})`],
           ['discussion', `Discussion (${commentsQ.items.length})`], ['decisions', `Decisions (${decisionsQ.items.length})`],
           ['analysis', `Analysis runs (${runsQ.items.length})`]] as [Tab, string][]).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={`chip ${tab === key ? 'on' : ''}`} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {tab === 'findings' ? (
        <div className="split">
          <div className="stack">
            {findingsQ.loading ? <Loading label="Loading findings…" /> : findingsQ.items.length === 0 ? (
              <Card pad>
                <Empty title={review.analysis_status === 'completed' ? 'No findings for this version' : 'No analysis yet'}
                  hint={review.analysis_status === 'completed'
                    ? 'The analysis completed and recorded no issues against the current approved guidelines.'
                    : 'Run the analysis to compare this content against the approved guidelines and facts.'} />
              </Card>
            ) : findingsQ.items.map((f) => (
              <FindingCard key={f.id} finding={f} review={review} selected={selectedFinding === f.id}
                onSelect={() => setSelectedFinding(f.id)} busy={busy} call={call} />
            ))}
          </div>
          <div className="stack">
            <Card>
              <CardHead title="What to do next" />
              <div style={{ padding: '12px 14px' }} className="stack">
                <div className="small">Every finding answers five questions: what, where, why, which approved source, and what to do.</div>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  <li>Accept or reject each suggestion — nothing is applied without your consent.</li>
                  <li>Applied corrections create a new version and trigger a re-scan.</li>
                  <li>A finding is only marked resolved when the re-scan confirms it is gone.</li>
                  <li>Blocking findings (critical/high on mandatory rules) need a documented override to approve.</li>
                </ul>
                {blocking.length > 0 ? (
                  <div className="warn-msg">
                    <b>{blocking.length} blocking finding(s).</b> Approval is refused until these are resolved or a brand
                    manager records a documented override.
                  </div>
                ) : <div className="ok-msg">No blocking findings for this version.</div>}
              </div>
            </Card>
            <Card>
              <CardHead title="Evidence status key" />
              <div style={{ padding: '10px 14px' }} className="stack small">
                {Object.entries(EVIDENCE_LABEL).map(([k, v]) => (
                  <div key={k}><Badge tone="b-info">{k}</Badge> <span className="muted">{v}</span></div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      ) : null}

      {tab === 'content' ? <ContentTab review={review} versions={versionsQ.items} findings={findingsQ.items} call={call} busy={busy} /> : null}
      {tab === 'minimal' ? <MinimalEditTab review={review} findings={findingsQ.items} reports={editsQ.items} refresh={editsQ.refresh} /> : null}
      {tab === 'discussion' ? <DiscussionTab review={review} comments={commentsQ.items} findings={findingsQ.items} refresh={commentsQ.refresh} /> : null}
      {tab === 'decisions' ? (
        <Card>
          <CardHead title="Approval and decision history" subtitle="Recorded server-side with actor, timestamp, version and reason" />
          {decisionsQ.items.length === 0 ? <Empty title="No decisions recorded yet" /> : (
            <div className="table-wrap">
              <table aria-label="Decision history">
                <thead><tr><th>When</th><th>Kind</th><th>Status</th><th>Reviewer</th><th>Version</th><th>Reason</th></tr></thead>
                <tbody>
                  {decisionsQ.items.map((d) => (
                    <tr key={d.id}>
                      <td className="nowrap">{fmtDateTime(d.decided_at ?? d.created_at)}</td>
                      <td>{d.kind}</td>
                      <td><Badge tone={d.status === 'approved' ? 'b-ok' : d.status === 'pending' ? 'b-info' : 'b-high'}>{d.status}</Badge>
                        {d.is_override ? <Badge tone="b-critical">override</Badge> : null}</td>
                      <td className="small">{d.reviewer_email ?? '—'}</td>
                      <td className="mono">v{d.version ?? '—'}</td>
                      <td className="small">{d.reason ?? d.override_reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}
      {tab === 'analysis' ? (
        <div className="stack">
          {runsQ.items.length === 0 ? <Card pad><Empty title="No analysis runs yet" /></Card> : runsQ.items.map((r) => (
            <Card key={r.id}>
              <CardHead
                title={`Run on v${r.version} — ${r.status}`}
                subtitle={`${r.model ?? 'model not recorded'} · analysis version ${r.analysis_version ?? '—'} · triggered by ${r.triggered_by ?? '—'}`}
                actions={<Badge tone={r.status === 'completed' ? 'b-ok' : r.status === 'failed' ? 'b-critical' : 'b-medium'}>{r.status}</Badge>}
              />
              <div style={{ padding: '12px 14px' }} className="stack">
                <div className="row small">
                  <span className="mono">{r.findings_count ?? 0} findings</span>
                  <span className="mono">{r.deterministic_count ?? 0} deterministic</span>
                  <span className="mono">{r.ai_count ?? 0} AI</span>
                  <span className="muted">started {fmtDateTime(r.started_at)} · finished {fmtDateTime(r.completed_at)}</span>
                </div>
                {r.error_message ? <ErrorNote>{r.error_message}</ErrorNote> : null}
                {r.stats?.ai_summary ? <div className="excerpt">{r.stats.ai_summary}</div> : null}
                {r.stats?.tone_assessment ? (
                  <div className="small">
                    <b>Tone assessment:</b> expected “{r.stats.tone_assessment.expected}”, observed “{r.stats.tone_assessment.observed}”
                    {' — '}{r.stats.tone_assessment.matches ? 'matches' : 'does not match'}.
                  </div>
                ) : null}
                {(r.stats?.supported_statements ?? []).length ? (
                  <details>
                    <summary className="small">Statements that were checked and supported ({r.stats.supported_statements.length})</summary>
                    <ul className="small">{r.stats.supported_statements.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
                  </details>
                ) : null}
                {(r.sources_used?.limitations ?? []).length || (r.stats?.ai_limitations ?? []).length ? (
                  <div className="warn-msg">
                    <b>Limitations recorded for this run:</b>
                    <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                      {[...(r.sources_used?.limitations ?? []), ...(r.stats?.ai_limitations ?? [])].map((l: string, i: number) => <li key={i}>{l}</li>)}
                    </ul>
                  </div>
                ) : null}
                <details>
                  <summary className="small">Sources supplied to the model for this run</summary>
                  <div className="small mono" style={{ marginTop: 6 }}>
                    rules: {(r.sources_used?.guidelines ?? []).join(', ') || '—'}<br />
                    facts: {(r.sources_used?.facts ?? []).join(', ') || '—'}
                  </div>
                </details>
              </div>
            </Card>
          ))}
        </div>
      ) : null}

      {actionModal ? (
        <Modal title={
          actionModal === 'approve' ? 'Record approval'
            : actionModal === 'reject' ? 'Reject this content'
              : actionModal === 'request_changes' ? 'Request changes' : 'Escalate for compliance review'
        } onClose={() => setActionModal(null)}
          footer={
            <>
              <Button onClick={() => setActionModal(null)}>Cancel</Button>
              <Button variant={actionModal === 'reject' ? 'danger' : 'primary'} disabled={busy === 'decision'}
                onClick={async () => {
                  const out = await call('bg_review_workflow', {
                    organization_id: review.organization_id, review_id: id, action: actionModal,
                    reason, override: actionModal === 'approve' ? override : false,
                    override_reason: override ? reason : null,
                  }, 'decision')
                  if (out && !out.blocked) setActionModal(null)
                }}>
                {actionModal === 'approve' ? 'Record approval' : actionModal === 'reject' ? 'Reject' : actionModal === 'request_changes' ? 'Request changes' : 'Escalate'}
              </Button>
            </>
          }>
          <Field label="Reason (recorded in the audit log)" required
            help="This reason is stored with your decision and shown to the creator.">
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3}
              placeholder="Why you are taking this decision…" />
          </Field>
          {actionModal === 'approve' ? (
            <>
              {blocking.length > 0 ? (
                <div className="warn-msg">
                  {blocking.length} unresolved mandatory finding(s) block approval. Approving anyway requires a
                  documented override, which is only permitted for a brand manager or administrator.
                </div>
              ) : null}
              <label className="row small" style={{ gap: 8, marginTop: 8 }}>
                <input type="checkbox" style={{ width: 'auto' }} checked={override} onChange={(e) => setOverride(e.target.checked)} />
                Override the blocking findings (recorded with your reason)
              </label>
              <div className="muted small" style={{ marginTop: 8 }}>
                Approval never publishes content: no publishing integration is configured, so publication stays a
                manual step.
              </div>
            </>
          ) : null}
        </Modal>
      ) : null}
    </div>
  )
}

function FindingCard({ finding, review, selected, onSelect, busy, call }: {
  finding: Row; review: Row; selected: boolean; onSelect: () => void; busy: string | null
  call: (fn: string, input: Row, label: string) => Promise<any>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(finding.suggested_correction ?? '')
  const [instructions, setInstructions] = useState('')
  const [showRegen, setShowRegen] = useState(false)
  const ref = finding.source_reference ?? {}

  return (
    <article className={`finding ${selected ? 'selected' : ''}`}>
      <div className="finding-head" onClick={onSelect} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') onSelect() }} aria-expanded={selected}>
        <span className="sev-bar" style={{ background: `var(--${finding.severity === 'informational' ? 'info' : finding.severity})` }} aria-hidden="true" />
        <div style={{ flex: 1 }}>
          <h3>{finding.title}</h3>
          <div className="row" style={{ gap: 6, marginTop: 4 }}>
            <SeverityBadge severity={finding.severity} />
            <Badge tone="b-info">{CATEGORY_LABEL[finding.category] ?? finding.category}</Badge>
            <Badge tone="b-accent">{finding.evidence_status}</Badge>
            <Badge tone={finding.detection_method === 'ai' ? 'b-medium' : 'b-info'}>
              {finding.detection_method === 'deterministic' ? 'rule engine' : finding.detection_method === 'visual' ? 'visual check' : 'AI analysis'}
            </Badge>
            {finding.rule_code ? <Badge tone="b-info">{finding.rule_code}</Badge> : null}
            <Badge tone={finding.review_status === 'open' ? 'b-high' : finding.review_status === 'resolved' ? 'b-ok' : 'b-info'}>
              {FINDING_STATUS_LABEL[finding.review_status] ?? finding.review_status}
            </Badge>
          </div>
        </div>
        <div className="muted small nowrap">confidence {confidenceLabel(finding.confidence)}</div>
      </div>

      {selected ? (
        <div className="finding-body stack" style={{ paddingTop: 12 }}>
          <div className="small">{finding.description}</div>
          {finding.original_text ? (
            <div>
              <div className="muted small">Where it occurs (v{finding.version ?? review.current_version})</div>
              <div className="excerpt">…{finding.location_excerpt ?? finding.original_text}…</div>
              {finding.location_start === null || finding.location_start === undefined ? (
                <div className="muted small">The exact position could not be located in the current text.</div>
              ) : null}
            </div>
          ) : <div className="muted small">This finding is about something missing from the content.</div>}

          <div>
            <div className="muted small">Why it was flagged</div>
            <div className="small">{finding.explanation}</div>
          </div>

          <div>
            <div className="muted small">Approved source</div>
            {finding.rule_code || ref.code ? (
              <div className="small">
                Rule <b>{finding.rule_code ?? ref.code}</b>{ref.title ? ` — ${ref.title}` : ''}
                {ref.enforcement ? ` (${ref.enforcement}${ref.severity ? `, severity ${ref.severity}` : ''})` : ''}
                {ref.rule_text ? <div className="muted">“{ref.rule_text}”</div> : null}
                {ref.source_section ? <div className="muted">Source: {ref.source_section}{ref.source_page ? `, page ${ref.source_page}` : ''}</div> : null}
              </div>
            ) : ref.fact_id || ref.statement ? (
              <div className="small">
                Approved fact <span className="mono">{String(ref.fact_id ?? '').slice(0, 8)}</span>
                {ref.statement ? <div className="muted">“{ref.statement}”</div> : null}
                {ref.source_location ? <div className="muted">Source: {ref.source_location}</div> : null}
                {ref.expiration_date ? <div className="muted">Expires: {String(ref.expiration_date).slice(0, 10)}</div> : null}
              </div>
            ) : (
              <div className="small muted">{ref.note ?? 'No approved source was found to verify this claim. Human verification is required.'}</div>
            )}
            <div className="small muted" style={{ marginTop: 4 }}>
              Evidence status: <b>{EVIDENCE_LABEL[finding.evidence_status] ?? finding.evidence_status}</b>
            </div>
          </div>

          {finding.suggested_correction || editing ? (
            <div>
              <div className="muted small">Suggested correction</div>
              {editing ? (
                <div className="stack">
                  <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} />
                  <div className="row">
                    <Button size="sm" variant="primary" disabled={busy === 'edit'}
                      onClick={async () => { await call('bg_correction_action', { organization_id: review.organization_id, review_id: review.id, action: 'edit', finding_id: finding.id, edited_text: draft, reason: 'edited by reviewer' }, 'edit'); setEditing(false) }}>
                      Save wording
                    </Button>
                    <Button size="sm" onClick={() => { setEditing(false); setDraft(finding.suggested_correction ?? '') }}>Cancel</Button>
                  </div>
                </div>
              ) : (
                <div className="diff">
                  <div className="diff-box">
                    <div className="diff-label">Original</div>
                    {finding.original_text || <span className="muted">— (nothing to replace)</span>}
                  </div>
                  <div className="diff-box after">
                    <div className="diff-label">Suggested</div>
                    {finding.suggested_correction}
                  </div>
                </div>
              )}
              {finding.correction_rationale ? <div className="small muted" style={{ marginTop: 6 }}>{finding.correction_rationale}</div> : null}
            </div>
          ) : null}

          <div className="row">
            <Button size="sm" variant="primary" disabled={!finding.suggested_correction || busy === 'accept'}
              onClick={() => call('bg_correction_action', { organization_id: review.organization_id, review_id: review.id, action: 'accept', finding_id: finding.id }, 'accept')}>
              <Check size={14} /> Accept suggestion
            </Button>
            <Button size="sm" disabled={busy === 'reject'}
              onClick={() => call('bg_correction_action', { organization_id: review.organization_id, review_id: review.id, action: 'reject', finding_id: finding.id, reason: 'rejected by reviewer' }, 'reject')}>
              <X size={14} /> Reject
            </Button>
            <Button size="sm" onClick={() => { setEditing(true); setDraft(finding.suggested_correction ?? '') }}>Edit wording</Button>
            <Button size="sm" onClick={() => setShowRegen((v) => !v)}><Sparkles size={14} /> Regenerate</Button>
            {finding.suggested_correction ? (
              <Button size="sm" disabled={busy === 'restore'}
                onClick={() => call('bg_correction_action', { organization_id: review.organization_id, review_id: review.id, action: 'restore', finding_id: finding.id }, 'restore')}>
                <RotateCcw size={14} /> Restore original
              </Button>
            ) : null}
            <div className="spacer" />
            <Button size="sm" disabled={busy === 'resolve'}
              onClick={() => call('bg_correction_action', { organization_id: review.organization_id, review_id: review.id, action: 'resolve_manual', finding_id: finding.id, reason: 'resolved by reviewer judgement' }, 'resolve')}>
              <CheckCircle2 size={14} /> Mark resolved
            </Button>
          </div>

          {showRegen ? (
            <div className="stack">
              <Field label="Optional instructions for the correction assistant"
                help="For example: keep it under 90 characters, or use the approved price.">
                <input value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="Optional guidance…" />
              </Field>
              <div className="row">
                <Button size="sm" variant="primary" disabled={busy === 'regenerate'}
                  onClick={async () => { await call('bg_correction_action', { organization_id: review.organization_id, review_id: review.id, action: 'regenerate', finding_id: finding.id, instructions }, 'regenerate'); setShowRegen(false); setInstructions('') }}>
                  {busy === 'regenerate' ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={14} />} Generate suggestion
                </Button>
                <span className="muted small">The assistant only rewrites the flagged span and never invents facts.</span>
              </div>
            </div>
          ) : null}

          {finding.review_status === 'resolved' && finding.resolution_note ? (
            <div className="ok-msg">Resolved: {finding.resolution_note}</div>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}

function ContentTab({ review, versions, findings, call, busy }: { review: Row; versions: Row[]; findings: Row[]; call: any; busy: string | null }) {
  const [selected, setSelected] = useState<number>(review.current_version)
  const [compareTo, setCompareTo] = useState<number | null>(null)
  const content = review.current_text ?? ''
  const spans = useMemo(() => findings
    .filter((f) => f.review_status === 'open' && f.location_start !== null && f.location_start !== undefined && f.version === review.current_version)
    .sort((a, b) => a.location_start - b.location_start), [findings, review.current_version])

  const highlighted: React.ReactNode[] = []
  let cursor = 0
  spans.forEach((f, i) => {
    if (f.location_start < cursor) return
    highlighted.push(<span key={`t${i}`}>{content.slice(cursor, f.location_start)}</span>)
    highlighted.push(<mark key={`m${i}`} title={`${f.severity}: ${f.title}`}>{content.slice(f.location_start, f.location_end)}</mark>)
    cursor = f.location_end
  })
  highlighted.push(<span key="tail">{content.slice(cursor)}</span>)

  const version = versions.find((v) => v.version === selected)
  const compared = compareTo ? versions.find((v) => v.version === compareTo) : null

  return (
    <div className="stack">
      <Card>
        <CardHead title={`Content v${review.current_version}`}
          subtitle={`${content.split(/\s+/).length} words · highlighted spans are open findings`}
          actions={
            <>
              <Button size="sm" disabled={busy === 'restore'}
                onClick={() => call('bg_review_workflow', { organization_id: review.organization_id, review_id: review.id, action: 'restore_version', target_version: selected, reason: `restored v${selected}` }, 'restore')}>
                <RotateCcw size={14} /> Restore v{selected} as new version
              </Button>
            </>
          } />
        <div style={{ padding: '14px 16px', whiteSpace: 'pre-wrap', fontFamily: 'var(--font)' }}>
          {review.current_version === selected ? highlighted : (version?.content ?? '')}
        </div>
      </Card>

      <div className="split">
        <Card>
          <CardHead title="Version history" subtitle="The original submission is never overwritten" />
          {versions.length === 0 ? <Empty title="No versions recorded" /> : (
            <div className="table-wrap">
              <table aria-label="Version history">
                <thead><tr><th>Version</th><th>Source</th><th>Summary</th><th>Author</th><th>When</th><th>Approved</th><th /></tr></thead>
                <tbody>
                  {versions.map((v) => (
                    <tr key={v.id}>
                      <td className="mono">v{v.version}</td>
                      <td><Badge tone="b-info">{(v.change_source ?? '—').replace(/_/g, ' ')}</Badge></td>
                      <td className="small">{v.change_summary ?? '—'}</td>
                      <td className="small">{v.author_email ?? '—'}</td>
                      <td className="muted small nowrap">{relTime(v.created_at)}</td>
                      <td>{v.is_approved ? <Badge tone="b-ok">approved</Badge> : <span className="muted small">—</span>}</td>
                      <td>
                        <button className="btn-link" onClick={() => setSelected(v.version)}>view</button>
                        <button className="btn-link" style={{ marginLeft: 8 }} onClick={() => setCompareTo(v.version)}>
                          <GitCompare size={12} /> compare
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <CardHead title={compared ? `Compare v${compared.version} with v${selected}` : 'Compare versions'}
            subtitle="Select “compare” on any version to diff it against the one you are viewing" />
          <div style={{ padding: '14px 16px' }}>
            {compared && version ? (
              <div className="diff">
                <div className="diff-box">
                  <div className="diff-label">v{compared.version}</div>
                  {diffTokens(compared.content ?? '', version.content ?? '').filter((t) => t.type !== 'same').map((t, i) => (
                    <div key={i}>{t.type === 'add' ? <ins>{t.text}</ins> : t.type === 'del' ? <del>{t.text}</del> : t.text}</div>
                  ))}
                  {(compared.content ?? '') === (version.content ?? '') ? <span className="muted">Identical content.</span> : null}
                </div>
                <div className="diff-box after">
                  <div className="diff-label">v{version.version}</div>
                  {diffTokens(compared.content ?? '', version.content ?? '').filter((t) => t.type !== 'same').map((t, i) => (
                    <div key={i}>{t.type === 'add' ? <ins>{t.text}</ins> : t.type === 'del' ? <del>{t.text}</del> : t.text}</div>
                  ))}
                  {(compared.content ?? '') === (version.content ?? '') ? <span className="muted">Identical content.</span> : null}
                </div>
              </div>
            ) : <div className="muted small">No comparison selected yet.</div>}
          </div>
        </Card>
      </div>
    </div>
  )
}

function diffTokens(a: string, b: string): { type: 'same' | 'add' | 'del'; text: string }[] {
  const aw = a.split(/(\s+)/)
  const bw = b.split(/(\s+)/)
  const out: { type: 'same' | 'add' | 'del'; text: string }[] = []
  let i = 0
  let j = 0
  while (i < aw.length || j < bw.length) {
    if (aw[i] === bw[j]) { out.push({ type: 'same', text: aw[i] ?? '' }); i++; j++ }
    else if (bw.slice(j).includes(aw[i]) && aw[i] !== undefined) { out.push({ type: 'add', text: bw[j] }); j++ }
    else if (aw[i] !== undefined) { out.push({ type: 'del', text: aw[i] }); i++ }
    else { out.push({ type: 'add', text: bw[j] }); j++ }
  }
  return out
}

function DiscussionTab({ review, comments, findings, refresh }: { review: Row; comments: Row[]; findings: Row[]; refresh: () => void }) {
  const session = useSession()
  const toast = useToast()
  const [body, setBody] = useState('')
  const [replyTo, setReplyTo] = useState<string | null>(null)
  const [findingId, setFindingId] = useState('')
  const [busy, setBusy] = useState(false)

  const post = async () => {
    if (!body.trim()) { toast.push('Write a comment first.', 'error'); return }
    setBusy(true)
    const res = await runFn('bg_comment_action', {
      organization_id: review.organization_id, review_id: review.id, action: 'add',
      body, parent_id: replyTo, finding_id: findingId || null,
    })
    setBusy(false)
    if (!res.ok) { toast.push(res.error ?? 'Could not post the comment.', 'error'); return }
    setBody(''); setReplyTo(null); setFindingId(''); refresh()
    const mentioned = (res.output as any)?.mentioned ?? []
    toast.push(mentioned.length ? `Comment posted. Notified ${mentioned.join(', ')}.` : 'Comment posted.', 'success')
  }

  const roots = comments.filter((c) => !c.parent_id)
  const childrenOf = (id: string) => comments.filter((c) => c.parent_id === id)

  return (
    <div className="split">
      <Card>
        <CardHead title="Discussion" subtitle="Comments, replies and finding references are workspace-scoped" />
        <div style={{ padding: '14px 16px' }} className="stack">
          {roots.length === 0 ? <Empty title="No comments yet" hint="Reviewers and creators can discuss the findings here." /> : roots.map((c) => (
            <div key={c.id}>
              <CommentRow comment={c} findings={findings} onReply={() => setReplyTo(c.id)} refresh={refresh} />
              {childrenOf(c.id).map((child) => (
                <div key={child.id} style={{ marginLeft: 18 }}><CommentRow comment={child} findings={findings} onReply={() => setReplyTo(c.id)} refresh={refresh} /></div>
              ))}
            </div>
          ))}
        </div>
      </Card>
      <Card>
        <CardHead title={replyTo ? 'Reply to thread' : 'Add a comment'} />
        <div style={{ padding: '14px 16px' }}>
          <Field label="Comment" help="Mention a colleague with @their.email to notify them.">
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4}
              placeholder={replyTo ? 'Reply…' : 'Share an observation, ask for a change, or reference a finding…'} />
          </Field>
          <Field label="Reference a finding (optional)">
            <select value={findingId} onChange={(e) => setFindingId(e.target.value)}>
              <option value="">No specific finding</option>
              {findings.map((f) => <option key={f.id} value={f.id}>{f.severity} — {f.title.slice(0, 70)}</option>)}
            </select>
          </Field>
          <div className="row">
            <Button variant="primary" disabled={busy} onClick={post}>{busy ? <span className="spinner" aria-hidden="true" /> : <Send size={14} />} Post comment</Button>
            {replyTo ? <Button onClick={() => setReplyTo(null)}>Cancel reply</Button> : null}
            <span className="muted small">Posting as {session.email}</span>
          </div>
        </div>
      </Card>
    </div>
  )
}

function CommentRow({ comment, findings, onReply, refresh }: { comment: Row; findings: Row[]; onReply: () => void; refresh: () => void }) {
  const finding = findings.find((f) => f.id === comment.finding_id)
  const [busy, setBusy] = useState(false)
  return (
    <div className={`comment ${comment.is_resolved ? 'resolved' : ''}`}>
      <div className="comment-head">
        <b>{comment.author_name ?? comment.author_email}</b>
        <span className="muted">{relTime(comment.created_at)}</span>
        {comment.is_resolved ? <Badge tone="b-ok">resolved</Badge> : null}
        <div className="spacer" />
        <button className="btn-link" onClick={onReply}>reply</button>
        <button className="btn-link" disabled={busy} onClick={async () => {
          setBusy(true)
          await runFn('bg_comment_action', {
            organization_id: comment.organization_id, review_id: comment.review_id,
            action: comment.is_resolved ? 'reopen' : 'resolve', comment_id: comment.id,
          })
          setBusy(false)
          refresh()
        }}>{comment.is_resolved ? 'reopen' : 'resolve'}</button>
      </div>
      <div className="small" style={{ marginTop: 3, whiteSpace: 'pre-wrap' }}>{comment.body}</div>
      {finding ? <div className="muted small" style={{ marginTop: 4 }}><MessageSquare size={12} /> on finding: {finding.title}</div> : null}
    </div>
  )
}

function MinimalEditTab({ review, findings, reports, refresh }: {
  review: Row; findings: Row[]; reports: Row[]; refresh: () => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const [instructions, setInstructions] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const open = findings.filter((f) => ['open', 'escalated', 'accepted'].includes(f.review_status))
  const report = reports.find((r) => r.id === selected) ?? reports[0]

  const run = async (action: string, reportId?: string) => {
    setBusy(action)
    const res = await runFn('bg_minimal_correction', {
      organization_id: review.organization_id, review_id: review.id, action,
      report_id: reportId, instructions: instructions || null, reason: 'reviewer decision',
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Done.', 'success')
    if (out.rescan_required) await runFn('bg_review_analyse', { review_id: review.id })
    refresh()
    if (out.report?.id) setSelected(out.report.id)
  }

  return (
    <div className="split">
      <div className="stack">
        {report ? (
          <Card>
            <CardHead title="Proposed minimal edit"
              subtitle={`${(report.change_map ?? []).length} span(s) changed · v${report.review_version} → proposed`}
              actions={<Badge tone={report.status === 'applied' ? 'b-ok' : report.status === 'rejected' ? 'b-critical' : 'b-medium'}>{report.status}</Badge>} />
            <div style={{ padding: 14 }} className="stack">
              {(report.change_map ?? []).length === 0 ? (
                <div className="warn-msg small">No span could be changed safely. See the guard notes on the right.</div>
              ) : (report.change_map ?? []).map((c: Row, i: number) => (
                <div key={i}>
                  <div className="row" style={{ gap: 6 }}>
                    {c.rule_code ? <Badge tone="b-accent">{c.rule_code}</Badge> : <Badge tone="b-info">no rule</Badge>}
                    <span className="small" style={{ fontWeight: 560 }}>{String(c.title).slice(0, 70)}</span>
                    <span className="muted small mono">{c.characters_changed} chars</span>
                  </div>
                  <div className="diff" style={{ marginTop: 6 }}>
                    <div className="diff-box">
                      <div className="diff-label">Original</div>
                      <div className="small">{c.original_text || <span className="muted">— nothing to replace</span>}</div>
                    </div>
                    <div className="diff-box after">
                      <div className="diff-label">Minimal replacement</div>
                      <div className="small">{c.replacement || <span className="muted">— removed</span>}</div>
                    </div>
                  </div>
                  {c.reason ? <div className="muted small" style={{ marginTop: 4 }}>{c.reason}</div> : null}
                </div>
              ))}
              <div className="row">
                <Button size="sm" variant="primary" disabled={busy === 'apply' || report.status === 'applied'}
                  onClick={() => run('apply', report.id)}>
                  {busy === 'apply' ? <span className="spinner" aria-hidden="true" /> : <Check size={14} />} Apply as a new version
                </Button>
                <Button size="sm" disabled={busy === 'reject' || report.status !== 'proposed'}
                  onClick={() => run('reject', report.id)}><X size={14} /> Reject proposal</Button>
                <span className="muted small">
                  Applying creates a new version and re-runs the analysis; findings only close when the re-scan confirms it.
                </span>
              </div>
            </div>
          </Card>
        ) : (
          <Card pad>
            <Empty title="No minimal-edit proposal yet"
              hint="Generate one to see the smallest change that satisfies each rule, with a before/after compliance report." />
          </Card>
        )}

        {reports.length > 1 ? (
          <Card>
            <CardHead title="Earlier proposals" />
            <div className="table-wrap">
              <table aria-label="Edit proposals">
                <thead><tr><th>When</th><th>Status</th><th>Spans</th><th>Fixed</th><th>New</th></tr></thead>
                <tbody>
                  {reports.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => setSelected(r.id)}>
                      <td className="muted small nowrap">{fmtDateTime(r.created_at)}</td>
                      <td><Badge tone={r.status === 'applied' ? 'b-ok' : 'b-info'}>{r.status}</Badge></td>
                      <td className="mono">{(r.change_map ?? []).length}</td>
                      <td className="mono">{r.resolved_count}</td>
                      <td className="mono">{r.new_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : null}
      </div>

      <div className="stack">
        <Card>
          <CardHead title="Generate a minimal edit" subtitle={`${open.length} open finding(s) on this version`} />
          <div style={{ padding: 14 }}>
            <Field label="Optional instructions" help="For example: keep the sentence short, or use the approved price wording.">
              <input value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="Optional guidance for the editor…" />
            </Field>
            <Button variant="primary" disabled={busy === 'propose' || open.length === 0} onClick={() => run('propose')}>
              {busy === 'propose' ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={14} />} Generate minimal edit
            </Button>
            {open.length === 0 ? <div className="muted small" style={{ marginTop: 8 }}>Nothing to correct on this version.</div> : null}
          </div>
        </Card>

        {report ? (
          <>
            <Card>
              <CardHead title="Before / after compliance report"
                subtitle="Deterministic checks re-run on the proposed text" />
              <div style={{ padding: 14 }} className="stack small">
                <div className="row">
                  <Badge tone="b-ok">{report.resolved_count} resolved</Badge>
                  <Badge tone="b-medium">{report.remaining_count} still present</Badge>
                  <Badge tone={Number(report.new_count) > 0 ? 'b-critical' : 'b-info'}>{report.new_count} introduced</Badge>
                </div>
                <div>
                  <div className="muted">Before</div>
                  {(report.before_summary?.items ?? []).length === 0
                    ? <div className="muted">No issue in the checked categories.</div>
                    : <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                        {(report.before_summary?.items ?? []).map((i: Row, k: number) => (
                          <li key={k}>{i.title} <span className="muted">({i.category})</span></li>
                        ))}
                      </ul>}
                </div>
                <div>
                  <div className="muted">After</div>
                  {(report.after_summary?.items ?? []).length === 0
                    ? <div className="ok-msg">All checked issues cleared.</div>
                    : <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                        {(report.after_summary?.items ?? []).map((i: Row, k: number) => (
                          <li key={k}>{i.title} <span className="muted">({i.category})</span></li>
                        ))}
                      </ul>}
                </div>
                <div className="muted">
                  Categories: terminology, required disclaimers, promotional claims and confidentiality. The full
                  engine (including the AI pass) runs again when the edit is applied.
                </div>
                {(report.rules_addressed ?? []).length ? (
                  <div>Rules addressed: {(report.rules_addressed ?? []).map((r: string) => <Badge key={r} tone="b-accent">{r}</Badge>)}</div>
                ) : null}
              </div>
            </Card>
            <Card>
              <CardHead title="Guard rails" subtitle="Edits that would have made things worse were blocked" />
              <div style={{ padding: 14 }} className="small">
                {(report.guard_warnings ?? []).length === 0 ? (
                  <div className="ok-msg">No guard warnings: every edit was minimal and introduced no new claim or prohibited term.</div>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {(report.guard_warnings ?? []).map((w: string, i: number) => <li key={i}>{w}</li>)}
                  </ul>
                )}
              </div>
            </Card>
          </>
        ) : null}
      </div>
    </div>
  )
}

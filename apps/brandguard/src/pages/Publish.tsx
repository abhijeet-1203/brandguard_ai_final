import React, { useState } from 'react'
import { CheckCircle2, FileSignature, PackageCheck, Send, ShieldAlert, XCircle } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, StatusBadge, useToast } from '../components/ui'
import { STATUS_LABEL, fmtDateTime, titleCase } from '../lib/types'
import type { Route } from '../components/Shell'

/** Improvement 3: the governed handoff after approval. */
export function Publish({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id ? safeId(session.activeOrg.id) : null
  const [busy, setBusy] = useState<string | null>(null)
  const [openReview, setOpenReview] = useState<string | null>(null)
  const [gate, setGate] = useState<Row | null>(null)

  const reviews = useSqlQuery<Row>(orgId ? `
    select id, title, status, current_version, content_hash, approved_hash, publish_status, publish_target,
           publish_blocked_reason, published_at, decision_reason
    from bg_reviews where organization_id = '${orgId}' and status in ('approved','rejected','archived','awaiting_review','changes_requested')
    order by case when status = 'approved' then 0 else 1 end, decided_at desc nulls last limit 40` : null, [orgId])
  const events = useSqlQuery<Row>(orgId ? `
    select * from bg_publish_events where organization_id = '${orgId}' order by created_at desc limit 40` : null, [orgId])
  const packages = useSqlQuery<Row>(orgId ? `
    select * from bg_publish_packages where organization_id = '${orgId}' order by created_at desc limit 20` : null, [orgId])

  const check = async (review: Row) => {
    setBusy(review.id + 'prepare')
    const res = await runFn('bg_publish_action', { organization_id: orgId, review_id: review.id, action: 'prepare' })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The gate check failed.', 'error'); return }
    const out: any = res.output
    setGate(out.gate)
    setOpenReview(review.id)
    toast.push(out.message ?? 'Checked.', out.gate?.allowed ? 'success' : 'error')
    reviews.refresh(); events.refresh()
  }

  const act = async (review: Row, action: 'package' | 'push' | 'withdraw') => {
    setBusy(review.id + action)
    const res = await runFn('bg_publish_action', { organization_id: orgId, review_id: review.id, action })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Done.', out.event?.status === 'blocked' || out.event?.status === 'failed' ? 'error' : 'success')
    reviews.refresh(); events.refresh(); packages.refresh()
  }

  const current = reviews.items.find((r) => r.id === openReview)
  const tone: Record<string, string> = { prepare: 'b-info', package: 'b-accent', draft_push: 'b-ok',
    blocked: 'b-critical', failed: 'b-critical', withdrawn: 'b-medium' }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Governed publishing</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Approval is not publication. This is the only path by which approved content leaves BrandGuard AI: the
            approval chain must be satisfied, the approved content hash must still match, and the handoff is always a
            draft — never a publish.
          </p>
        </div>
      </div>

      <Card pad>
        <div className="row small">
          <ShieldAlert size={15} />
          <span>
            Four gates are checked on every attempt: the review is approved, the approval chain is satisfied (including
            a compliance sign-off for sensitive claims and a second reviewer where required), no unresolved mandatory
            finding blocks it, and the content has not changed since approval. Every attempt is written to the
            publishing audit trail.
          </span>
        </div>
      </Card>

      <Card>
        <CardHead title="Approved content ready for handoff" />
        {reviews.loading ? <Loading /> : reviews.items.length === 0 ? (
          <Empty title="Nothing approved yet" hint="Once a review is approved it appears here for the governed handoff." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Publishing queue">
              <thead><tr><th>Submission</th><th>Status</th><th>Pinned hash</th><th>Handoff</th><th>Target</th><th>Actions</th></tr></thead>
              <tbody>
                {reviews.items.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <button className="btn-link" onClick={() => navigate({ page: 'review', id: r.id })}>{r.title}</button>
                      <div className="muted small">v{r.current_version}{r.decision_reason ? ` · ${String(r.decision_reason).slice(0, 60)}` : ''}</div>
                    </td>
                    <td><StatusBadge status={r.status} label={STATUS_LABEL[r.status]} /></td>
                    <td className="small mono">
                      {r.approved_hash ? `${String(r.approved_hash).slice(7, 21)}…` : <span className="muted">not pinned</span>}
                      {r.approved_hash && r.content_hash && r.approved_hash !== r.content_hash ? (
                        <div><Badge tone="b-critical">content changed since approval</Badge></div>
                      ) : null}
                    </td>
                    <td>
                      {r.publish_status
                        ? <Badge tone={r.publish_status === 'drafted' ? 'b-ok' : r.publish_status === 'blocked' ? 'b-critical' : 'b-accent'}>
                            {r.publish_status.replace(/_/g, ' ')}</Badge>
                        : <span className="muted small">not started</span>}
                      {r.publish_blocked_reason ? <div className="muted small" style={{ maxWidth: 260 }}>{r.publish_blocked_reason}</div> : null}
                    </td>
                    <td className="small">{r.publish_target ?? <span className="muted">chain default</span>}</td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <Button size="sm" disabled={busy === r.id + 'prepare'} onClick={() => check(r)}>
                          {busy === r.id + 'prepare' ? <span className="spinner" aria-hidden="true" /> : <CheckCircle2 size={13} />} Check gate
                        </Button>
                        <Button size="sm" disabled={busy === r.id + 'package'} onClick={() => act(r, 'package')}>
                          <FileSignature size={13} /> Package
                        </Button>
                        <Button size="sm" disabled={busy === r.id + 'push'} onClick={() => act(r, 'push')}>
                          <Send size={13} /> Push draft
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {current && gate ? (
        <Card>
          <CardHead title={`Approval chain gate — ${current.title}`}
            subtitle={`Chain: ${gate.chain_name} · ${gate.approvers?.length ?? 0} approver(s) · ${gate.critical_claims ?? 0} sensitive claim finding(s)`}
            actions={<Badge tone={gate.allowed ? 'b-ok' : 'b-critical'}>{gate.allowed ? 'handoff allowed' : 'blocked'}</Badge>} />
          <div style={{ padding: 14 }} className="stack">
            {gate.checks.map((c: Row, i: number) => (
              <div key={i} className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                {c.ok ? <CheckCircle2 size={15} color="var(--ok)" /> : <XCircle size={15} color="var(--critical)" />}
                <div>
                  <b className="small">{c.check}</b>
                  <div className="muted small">{c.detail}</div>
                </div>
              </div>
            ))}
            {!gate.allowed ? <div className="err">{gate.blocked_reason}</div> : null}
          </div>
        </Card>
      ) : null}

      <div className="split-even">
        <Card>
          <CardHead title="Publishing packages" subtitle="The fallback handoff — no connector required" />
          {packages.items.length === 0 ? <Empty title="No package generated yet"
            hint="A package carries the pinned content hash, the approval trail and a tamper-evident seal, so a person can complete the handoff without any integration." /> : (
            <div className="stack" style={{ padding: 14 }}>
              {packages.items.map((p) => (
                <div key={p.id} style={{ borderBottom: '1px solid var(--line)', paddingBottom: 8 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <PackageCheck size={14} />
                    <b className="small">v{p.version}</b>
                    <Badge tone={p.status === 'pushed' ? 'b-ok' : 'b-accent'}>{p.status}</Badge>
                    <span className="muted small">{fmtDateTime(p.created_at)}</span>
                  </div>
                  <div className="small mono">hash {String(p.content_hash).slice(0, 24)}…</div>
                  <div className="small mono">seal {String(p.signature).slice(0, 24)}…</div>
                  <div className="muted small">{p.signature_method}</div>
                  {p.package_path ? <div className="muted small mono">{p.package_path}</div> : null}
                  <div className="small">Target: {p.contents?.target ?? '—'} · approved by {(p.contents?.approved_by ?? []).join(', ') || '—'}</div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardHead title="Publishing audit trail" subtitle="Every attempt, allowed or blocked" />
          {events.items.length === 0 ? <Empty title="No publishing actions recorded yet" /> : (
            <div className="table-wrap">
              <table aria-label="Publishing events">
                <thead><tr><th>When</th><th>Action</th><th>Version</th><th>Hash</th><th>Actor</th><th>Detail</th></tr></thead>
                <tbody>
                  {events.items.map((e) => (
                    <tr key={e.id}>
                      <td className="muted small nowrap">{fmtDateTime(e.created_at)}</td>
                      <td><Badge tone={tone[e.action] ?? 'b-info'}>{e.action.replace(/_/g, ' ')}</Badge>
                        {e.status !== 'ok' ? <Badge tone="b-critical">{e.status}</Badge> : null}</td>
                      <td className="mono">v{e.version ?? '—'}</td>
                      <td className="small mono">{e.content_hash ? `${String(e.content_hash).slice(7, 19)}…` : '—'}</td>
                      <td className="small">{e.actor_email ?? '—'}</td>
                      <td className="small" style={{ maxWidth: 320 }}>{String(e.detail ?? '').slice(0, 180)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

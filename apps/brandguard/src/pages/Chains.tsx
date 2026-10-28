import React, { useState } from 'react'
import { GitBranch, RefreshCw, Save, ShieldCheck } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Field, Loading, Modal, useToast } from '../components/ui'
import { CONTENT_TYPES, titleCase } from '../lib/types'

/** Improvement 3: configurable, multi-step approval chains by content type. */
export function Chains() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState<Row | null>(null)

  const chains = useRows<Row>('bg_approval_chains', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'asc' }], limit: 50,
  }, [orgId])
  const policies = useRows<Row>('bg_policies', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 5 }, [orgId])
  const policy = policies.items[0]

  const save = async () => {
    if (!editing) return
    setBusy('save')
    const res = await runFn('bg_approval_chain', {
      organization_id: orgId, action: 'save', chain_id: editing.id ?? null,
      name: editing.name, content_type: editing.content_type || null,
      min_approvals: Number(editing.min_approvals ?? 1),
      second_reviewer_required: Boolean(editing.second_reviewer_required),
      critical_requires_compliance: Boolean(editing.critical_requires_compliance),
      block_on_mandatory: Boolean(editing.block_on_mandatory),
      publish_target: editing.publish_target || null,
      is_default: Boolean(editing.is_default),
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not save the chain.', 'error'); return }
    toast.push((res.output as any)?.message ?? 'Chain saved.', 'success')
    setEditing(null); chains.refresh()
  }

  const archive = async (chain: Row) => {
    setBusy(chain.id + 'archive')
    const res = await runFn('bg_approval_chain', { organization_id: orgId, action: 'archive', chain_id: chain.id })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not archive.', 'error'); return }
    toast.push('Chain archived.', 'success')
    chains.refresh()
  }

  const canEdit = session.can('actOnInsight')

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Approval chains</h1>
          <p className="muted small" style={{ margin: 0 }}>
            What "approved" means, per content type. Chains are enforced by the backend on every decision and again at
            the publishing handoff — a second reviewer for critical claims, a compliance sign-off for sensitive
            categories, and a block on unresolved mandatory findings are all configurable here.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={chains.refresh}><RefreshCw size={14} /> Refresh</Button>
        {canEdit ? (
          <Button size="sm" variant="primary" onClick={() => setEditing({
            name: '', content_type: '', min_approvals: 1, second_reviewer_required: false,
            critical_requires_compliance: true, block_on_mandatory: true, is_default: false, publish_target: '',
          })}><GitBranch size={14} /> New chain</Button>
        ) : null}
      </div>

      <Card pad>
        <div className="row small">
          <ShieldCheck size={15} />
          <span>
            A review resolves its chain in this order: the chain pinned on the review, then the chain for its content
            type, then the workspace default. Critical and sensitive claim findings always require a second, different
            reviewer, whatever the chain says.
          </span>
        </div>
      </Card>

      <Card>
        <CardHead title="Configured chains" subtitle={`${chains.items.filter((c) => c.status === 'active').length} active`} />
        {chains.loading ? <Loading /> : chains.items.length === 0 ? (
          <Empty title="No chains configured" hint="Open a review to create the workspace default automatically, or add one here." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Approval chains">
              <thead><tr><th>Chain</th><th>Applies to</th><th>Approvals</th><th>Second reviewer</th><th>Compliance</th><th>Block on mandatory</th><th>Publish target</th><th>Status</th><th /></tr></thead>
              <tbody>
                {chains.items.map((c) => (
                  <tr key={c.id}>
                    <td><div style={{ fontWeight: 560 }}>{c.name}</div>
                      {c.is_default ? <Badge tone="b-accent">workspace default</Badge> : null}</td>
                    <td className="small">{c.content_type ? titleCase(c.content_type) : 'any content type'}</td>
                    <td className="mono">{c.min_approvals}</td>
                    <td>{c.second_reviewer_required ? <Badge tone="b-high">required</Badge> : <span className="muted small">as needed</span>}</td>
                    <td>{c.critical_requires_compliance ? <Badge tone="b-ok">on</Badge> : <span className="muted small">off</span>}</td>
                    <td>{c.block_on_mandatory ? <Badge tone="b-high">on</Badge> : <span className="muted small">off</span>}</td>
                    <td className="small">{c.publish_target ?? <span className="muted">none</span>}</td>
                    <td><Badge tone={c.status === 'active' ? 'b-ok' : 'b-info'}>{c.status}</Badge></td>
                    <td>
                      {canEdit ? (
                        <div className="row" style={{ gap: 4 }}>
                          <button className="btn-link" onClick={() => setEditing({ ...c })}>edit</button>
                          {c.status === 'active' ? (
                            <button className="btn-link" disabled={busy === c.id + 'archive'} onClick={() => archive(c)}>archive</button>
                          ) : null}
                        </div>
                      ) : <span className="muted small">read-only</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHead title="Workspace policy defaults"
          subtitle="The chain settings are independent of these, but both are enforced together" />
        <div style={{ padding: 16 }} className="small">
          {policy ? (
            <div className="grid cols-2">
              <div>Separation of duties: <b>{policy.require_separation_of_duties ? 'on' : 'off'}</b></div>
              <div>Block approval on mandatory findings: <b>{policy.block_approval_on_mandatory ? 'on' : 'off'}</b></div>
              <div>Critical requires compliance reviewer: <b>{policy.critical_requires_compliance_reviewer ? 'on' : 'off'}</b></div>
              <div>High risk requires a second reviewer: <b>{policy.high_risk_requires_second_reviewer ? 'on' : 'off'}</b></div>
            </div>
          ) : <span className="muted">No policy record yet.</span>}
          <div className="muted" style={{ marginTop: 8 }}>
            Change these on the Settings &amp; privacy page. Nothing here can enable automatic publication: there is no
            publishing integration that publishes, only a draft push.
          </div>
        </div>
      </Card>

      {editing ? (
        <Modal title={editing.id ? `Edit chain: ${editing.name}` : 'New approval chain'} onClose={() => setEditing(null)}
          footer={<><Button onClick={() => setEditing(null)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'save'} onClick={save}><Save size={14} /> Save chain</Button></>}>
          <Field label="Chain name" required>
            <input value={editing.name ?? ''} onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="e.g. Campaign assets — two reviewers" />
          </Field>
          <Field label="Applies to content type" help="Leave empty to make this the chain for any content type.">
            <select value={editing.content_type ?? ''} onChange={(e) => setEditing({ ...editing, content_type: e.target.value })}>
              <option value="">Any content type</option>
              {CONTENT_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </Field>
          <Field label="Minimum approvals" required help="How many distinct approvers are needed before the chain is satisfied.">
            <input type="number" min={1} max={5} value={editing.min_approvals ?? 1}
              onChange={(e) => setEditing({ ...editing, min_approvals: e.target.value })} />
          </Field>
          <label className="row" style={{ gap: 8, alignItems: 'flex-start', marginBottom: 10 }}>
            <input type="checkbox" style={{ width: 'auto', marginTop: 3 }} checked={Boolean(editing.second_reviewer_required)}
              onChange={(e) => setEditing({ ...editing, second_reviewer_required: e.target.checked })} />
            <span><b className="small">Require a second reviewer</b>
              <div className="muted small">Always enforced anyway when a critical or sensitive claim finding is open.</div></span>
          </label>
          <label className="row" style={{ gap: 8, alignItems: 'flex-start', marginBottom: 10 }}>
            <input type="checkbox" style={{ width: 'auto', marginTop: 3 }} checked={Boolean(editing.critical_requires_compliance)}
              onChange={(e) => setEditing({ ...editing, critical_requires_compliance: e.target.checked })} />
            <span><b className="small">Compliance sign-off for sensitive claims</b>
              <div className="muted small">Promotional claim, confidentiality and legal findings need a compliance reviewer.</div></span>
          </label>
          <label className="row" style={{ gap: 8, alignItems: 'flex-start', marginBottom: 10 }}>
            <input type="checkbox" style={{ width: 'auto', marginTop: 3 }} checked={Boolean(editing.block_on_mandatory)}
              onChange={(e) => setEditing({ ...editing, block_on_mandatory: e.target.checked })} />
            <span><b className="small">Block approval on unresolved mandatory findings</b>
              <div className="muted small">A brand manager can still record a documented override.</div></span>
          </label>
          <label className="row" style={{ gap: 8, alignItems: 'flex-start', marginBottom: 10 }}>
            <input type="checkbox" style={{ width: 'auto', marginTop: 3 }} checked={Boolean(editing.is_default)}
              onChange={(e) => setEditing({ ...editing, is_default: e.target.checked })} />
            <span><b className="small">Workspace default</b>
              <div className="muted small">Used when no chain matches the review's content type.</div></span>
          </label>
          <Field label="Publishing target" help="The tool this content is handed to. The handoff is always a draft.">
            <input value={editing.publish_target ?? ''} onChange={(e) => setEditing({ ...editing, publish_target: e.target.value })}
              placeholder="e.g. CMS drafts folder" />
          </Field>
        </Modal>
      ) : null}
    </div>
  )
}

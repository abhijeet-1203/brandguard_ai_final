import React, { useState } from 'react'
import { AlertTriangle, Database, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Field, Loading, Modal, useToast } from '../components/ui'
import { fmtDateTime } from '../lib/types'

export function Settings() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const org = session.activeOrg
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resetOpen, setResetOpen] = useState(false)
  const [seedSlug, setSeedSlug] = useState('northstar-living')
  const [newWorkspace, setNewWorkspace] = useState({ name: '', slug: '', description: '' })
  const [orgForm, setOrgForm] = useState<Row>({
    name: org?.name ?? '', description: org?.description ?? '', tone_summary: org?.tone_summary ?? '',
    colors: ((org?.settings?.brand_colors ?? []) as string[]).join(', '),
  })
  const [policyDraft, setPolicyDraft] = useState<Row | null>(null)

  const policies = useRows<Row>('bg_policies', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 5 }, [orgId])
  const providers = useRows<Row>('bg_ai_providers', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 10 }, [orgId])
  const policy = policies.items[0] ?? null
  const draft = policyDraft ?? policy

  const savePolicy = async () => {
    if (!draft) return
    setBusy('policy')
    const updates: Row = {}
    ;['require_separation_of_duties', 'block_approval_on_mandatory', 'critical_requires_compliance_reviewer',
      'high_risk_requires_second_reviewer', 'ai_analysis_enabled', 'external_ai_allowed',
      'confidential_content_to_external_ai', 'require_review_after_approved_edit'].forEach((k) => {
        updates[k] = Boolean(draft[k])
      })
    updates.retention_days = Number(draft.retention_days ?? 365)
    updates.analysis_version = draft.analysis_version ?? 'bg-analysis-1.0'
    const res = await runFn('bg_policy_update', { organization_id: orgId, updates, reason: 'updated from Settings' })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not save the policy.', 'error'); return }
    toast.push('Policy saved.', 'success')
    setPolicyDraft(null); policies.refresh()
  }

  const saveOrg = async () => {
    setBusy('org')
    const res = await runFn('bg_workspace_action', {
      organization_id: orgId, action: 'update', name: orgForm.name, description: orgForm.description,
      tone_summary: orgForm.tone_summary,
      brand_colors: String(orgForm.colors ?? '').split(',').map((c: string) => c.trim()).filter(Boolean),
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not save the workspace.', 'error'); return }
    toast.push('Workspace saved.', 'success')
    await session.refresh()
  }

  const provision = async (slug: string, reset: boolean) => {
    setBusy('seed' + slug)
    const res = await runFn('bg_seed_workspace', { slug, reset })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Provisioning failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Workspace provisioned.', 'success')
    await session.refresh()
    if (reset) setResetOpen(false)
  }

  const createWorkspace = async () => {
    setError(null)
    setBusy('create')
    const res = await runFn('bg_workspace_action', { action: 'create', ...newWorkspace })
    setBusy(null)
    if (!res.ok) { setError(res.error ?? 'Could not create the workspace.'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Workspace created.', 'success')
    await session.refresh()
    if (out.organization?.id) session.setActiveOrgId(out.organization.id)
    setNewWorkspace({ name: '', slug: '', description: '' })
  }

  if (!session.can('managePolicy')) {
    return (
      <div className="stack">
        <h1>Settings &amp; privacy</h1>
        <Card pad><div className="err">You do not have permission to view workspace settings (administrator required).</div></Card>
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Settings &amp; privacy</h1>
          <p className="muted small" style={{ margin: 0 }}>Workspace profile, governance policy, AI data flow and retention.</p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { policies.refresh(); providers.refresh() }}><RefreshCw size={14} /> Refresh</Button>
      </div>

      <Card>
        <CardHead title="Workspace profile" subtitle="Brand colours are used by the visual consistency check" />
        <div style={{ padding: 16 }}>
          <Field label="Workspace name"><input value={orgForm.name} onChange={(e) => setOrgForm({ ...orgForm, name: e.target.value })} /></Field>
          <Field label="Description"><textarea rows={2} value={orgForm.description ?? ''} onChange={(e) => setOrgForm({ ...orgForm, description: e.target.value })} /></Field>
          <Field label="Approved tone" help="Shown to reviewers and used as context for tone analysis.">
            <textarea rows={2} value={orgForm.tone_summary ?? ''} onChange={(e) => setOrgForm({ ...orgForm, tone_summary: e.target.value })} />
          </Field>
          <Field label="Brand colours" help="Comma-separated hex values, e.g. #0c2340, #e8b33c">
            <input value={orgForm.colors ?? ''} onChange={(e) => setOrgForm({ ...orgForm, colors: e.target.value })} />
          </Field>
          <Button variant="primary" disabled={busy === 'org'} onClick={saveOrg}>Save workspace</Button>
        </div>
      </Card>

      <Card>
        <CardHead title="Governance policy" subtitle="Controls approval routing and the AI data flow" />
        {policies.loading ? <Loading /> : !policy ? <Empty title="No policy record" hint="Reload the app to create the default policy." /> : (
          <div style={{ padding: 16 }} className="stack">
            {[
              ['require_separation_of_duties', 'Separation of duties', 'A creator cannot approve their own submission.'],
              ['block_approval_on_mandatory', 'Block approval on unresolved mandatory findings', 'Critical and high mandatory findings must be resolved or overridden with a documented reason.'],
              ['critical_requires_compliance_reviewer', 'Critical findings require a compliance reviewer', 'Only a compliance reviewer or administrator may approve content with unresolved critical findings.'],
              ['high_risk_requires_second_reviewer', 'High-risk content needs a second reviewer', 'Requires two recorded approvals before a high or critical risk review is approved.'],
              ['require_review_after_approved_edit', 'Require re-review after editing approved content', 'An approved version that changes must go through analysis and approval again.'],
              ['ai_analysis_enabled', 'AI analysis enabled', 'When off, only deterministic and visual checks run.'],
              ['external_ai_allowed', 'Allow the platform model to receive content', 'Content is analysed by the pod agent on the platform runtime. Turn off to run rule checks only.'],
              ['confidential_content_to_external_ai', 'Send content flagged as confidential to the model', 'Off by default: material matching confidentiality rules is not sent for AI analysis.'],
            ].map(([key, label, help]) => (
              <label key={key} className="row" style={{ alignItems: 'flex-start', gap: 10 }}>
                <input type="checkbox" style={{ width: 'auto', marginTop: 3 }} checked={Boolean(draft?.[key])}
                  onChange={(e) => setPolicyDraft({ ...(draft ?? {}), [key]: e.target.checked })} />
                <span>
                  <b className="small">{label}</b>
                  <div className="muted small">{help}</div>
                </span>
              </label>
            ))}
            <div className="grid cols-2">
              <Field label="Retention period (days)" help="How long review records are kept before they are eligible for deletion.">
                <input type="number" value={draft?.retention_days ?? 365}
                  onChange={(e) => setPolicyDraft({ ...(draft ?? {}), retention_days: e.target.value })} />
              </Field>
              <Field label="Analysis version" help="Recorded on every run so results can be compared across prompt or model changes.">
                <input value={draft?.analysis_version ?? ''} onChange={(e) => setPolicyDraft({ ...(draft ?? {}), analysis_version: e.target.value })} />
              </Field>
            </div>
            <div className="row">
              <Button variant="primary" disabled={busy === 'policy'} onClick={savePolicy}>Save policy</Button>
              {policyDraft ? <Button onClick={() => setPolicyDraft(null)}>Discard changes</Button> : null}
              <span className="muted small">Last changed by {policy?.updated_by ?? '—'} · {fmtDateTime(policy?.updated_at)}</span>
            </div>
            <div className="notice">
              <b>Automatic publication is not enabled and cannot be enabled here.</b> BrandGuard AI records approvals;
              it never publishes content, because no publishing integration is configured. Approval is a human decision
              recorded against a specific version.
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardHead title="AI providers and data flow" subtitle="What leaves this workspace, and where it goes" />
        {providers.loading ? <Loading /> : providers.items.length === 0 ? <Empty title="No provider records" /> : (
          <div className="table-wrap">
            <table aria-label="AI providers">
              <thead><tr><th>Provider</th><th>Model</th><th>Configured</th><th>What is transmitted</th><th>Checked</th></tr></thead>
              <tbody>
                {providers.items.map((p) => (
                  <tr key={p.id}>
                    <td>{p.provider}</td>
                    <td className="small">{p.model ?? '—'}</td>
                    <td><Badge tone={p.configured ? 'b-ok' : 'b-info'}>{p.configured ? 'configured' : 'not configured'}</Badge></td>
                    <td className="small">{p.data_flow_note}</td>
                    <td className="muted small nowrap">{fmtDateTime(p.last_checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ padding: 16 }} className="small muted">
          <ShieldCheck size={14} /> No API keys are stored in this application. Analysis runs through a pod agent on the
          platform runtime; nothing is sent to a third-party model provider by BrandGuard AI itself.
        </div>
      </Card>

      <div className="split-even">
        <Card>
          <CardHead title="Create a workspace" subtitle="You become its administrator" />
          <div style={{ padding: 16 }}>
            {error ? <div className="err" style={{ marginBottom: 12 }}>{error}</div> : null}
            <Field label="Workspace name" required><input value={newWorkspace.name} onChange={(e) => setNewWorkspace({ ...newWorkspace, name: e.target.value })} placeholder="e.g. Acme Home" /></Field>
            <Field label="Workspace key" help="Lowercase letters, numbers and hyphens. Used for file paths.">
              <input value={newWorkspace.slug} onChange={(e) => setNewWorkspace({ ...newWorkspace, slug: e.target.value })} placeholder="e.g. acme-home" />
            </Field>
            <Field label="Description"><input value={newWorkspace.description} onChange={(e) => setNewWorkspace({ ...newWorkspace, description: e.target.value })} /></Field>
            <Button variant="primary" disabled={busy === 'create'} onClick={createWorkspace}>Create workspace</Button>
          </div>
        </Card>

        <Card>
          <CardHead title="Starter brand knowledge" subtitle="Provision a workspace with guidelines, approved facts, assets and submissions" />
          <div style={{ padding: 16 }} className="stack">
            <div className="row small">
              <Database size={14} />
              <span>Provisioning creates real records you can edit: 15 guideline rules, 9 approved facts with evidence,
                4 asset files with perceptual hashes, and 8 submissions to review.</span>
            </div>
            <Field label="Workspace">
              <select value={seedSlug} onChange={(e) => setSeedSlug(e.target.value)}>
                <option value="northstar-living">Northstar Living (home essentials)</option>
                <option value="meridian-foods">Meridian Foods (speciality bakery)</option>
              </select>
            </Field>
            <div className="row">
              <Button disabled={busy === 'seed' + seedSlug} onClick={() => provision(seedSlug, false)}>
                <Sparkles size={14} /> Provision if missing
              </Button>
              <Button variant="danger" onClick={() => setResetOpen(true)}><AlertTriangle size={14} /> Reset workspace data</Button>
            </div>
            <div className="muted small">
              Reset deletes this workspace's BrandGuard records and rebuilds them deterministically. Uploaded files stay in
              pod files.
            </div>
          </div>
        </Card>
      </div>

      {resetOpen ? (
        <Modal title="Reset workspace data" onClose={() => setResetOpen(false)}
          footer={<><Button onClick={() => setResetOpen(false)}>Cancel</Button>
            <Button variant="danger" disabled={busy === 'seed' + seedSlug} onClick={() => provision(seedSlug, true)}>Reset and rebuild</Button></>}>
          <div className="warn-msg">
            This deletes every review, finding, guideline, fact, asset record, comment, decision and audit entry for the
            selected workspace, then rebuilds it from the starter set. This cannot be undone.
          </div>
          <div className="muted small" style={{ marginTop: 10 }}>Workspace: {seedSlug}</div>
        </Modal>
      ) : null}
    </div>
  )
}

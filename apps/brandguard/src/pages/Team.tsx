import React, { useState } from 'react'
import { RefreshCw, UserPlus } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Field, Loading, Modal, useToast } from '../components/ui'
import { ROLES, ROLE_LABEL, fmtDate, titleCase } from '../lib/types'

const ROLE_DESCRIPTION: Record<string, string> = {
  admin: 'Workspace configuration, users, policies, audit log and all content decisions.',
  brand_manager: 'Owns the guidelines and approved facts, approves assets, investigates recurring issues.',
  reviewer: 'Reviews submitted content, inspects findings and evidence, requests changes, approves or rejects.',
  compliance_reviewer: 'Reviews sensitive claims (health, legal, financial, environmental) and must sign off critical findings.',
  creator: 'Submits content, applies corrections, responds to reviewers, resubmits revised versions.',
}

export function Team() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [inviteOpen, setInviteOpen] = useState(false)
  const [form, setForm] = useState({ email: '', role: 'creator', display_name: '' })
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = useRows<Row>('bg_memberships', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'asc' }], limit: 200,
  }, [orgId])

  const audit = useRows<Row>('bg_audit_logs', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'desc' }], limit: 60,
  }, [orgId])

  const act = async (action: string, membership: Row, extra: Row = {}) => {
    setBusy(membership.id + action)
    const res = await runFn('bg_team_action', { organization_id: orgId, action, membership_id: membership.id, ...extra })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    toast.push((res.output as any)?.message ?? 'Done.', 'success')
    rows.refresh()
  }

  const invite = async () => {
    setError(null)
    setBusy('invite')
    const res = await runFn('bg_team_action', { organization_id: orgId, action: 'invite', ...form })
    setBusy(null)
    if (!res.ok) { setError(res.error ?? 'Could not add the member.'); return }
    toast.push((res.output as any)?.message ?? 'Member added.', 'success')
    setInviteOpen(false); setForm({ email: '', role: 'creator', display_name: '' }); rows.refresh()
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Team &amp; permissions</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Roles are enforced on the backend. Hiding a button is never the security boundary.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={rows.refresh}><RefreshCw size={14} /> Refresh</Button>
        {session.can('manageTeam') ? (
          <Button size="sm" variant="primary" onClick={() => { setInviteOpen(true); setError(null) }}><UserPlus size={14} /> Add member</Button>
        ) : null}
      </div>

      <Card>
        <CardHead title={`Members of ${session.activeOrg?.name ?? 'this workspace'}`} subtitle={`${rows.items.length} member(s)`} />
        {rows.loading ? <Loading /> : rows.items.length === 0 ? <Empty title="No members yet" /> : (
          <div className="table-wrap">
            <table aria-label="Workspace members">
              <thead><tr><th>Person</th><th>Role</th><th>Status</th><th>Added</th><th>Actions</th></tr></thead>
              <tbody>
                {rows.items.map((m) => (
                  <tr key={m.id}>
                    <td>
                      <div style={{ fontWeight: 560 }}>{m.display_name ?? m.user_email}</div>
                      <div className="muted small">{m.user_email}</div>
                    </td>
                    <td>
                      <select value={m.role} disabled={!session.can('manageTeam') || busy === m.id + 'change_role'}
                        onChange={(e) => act('change_role', m, { role: e.target.value })} style={{ width: 'auto' }}>
                        {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                      </select>
                      <div className="muted small" style={{ maxWidth: 420 }}>{ROLE_DESCRIPTION[m.role]}</div>
                    </td>
                    <td><Badge tone={m.status === 'active' ? 'b-ok' : m.status === 'invited' ? 'b-medium' : 'b-info'}>{m.status}</Badge></td>
                    <td className="muted small nowrap">{fmtDate(m.created_at)}</td>
                    <td>
                      {session.can('manageTeam') ? (
                        <div className="row" style={{ gap: 4 }}>
                          {m.status === 'active' ? (
                            <button className="btn-link" disabled={busy === m.id + 'suspend'} onClick={() => act('suspend', m)}>suspend</button>
                          ) : (
                            <button className="btn-link" disabled={busy === m.id + 'activate'} onClick={() => act('activate', m)}>activate</button>
                          )}
                          <button className="btn-link" disabled={busy === m.id + 'remove'}
                            onClick={() => { if (window.confirm(`Remove ${m.user_email} from this workspace?`)) act('remove', m) }}>remove</button>
                        </div>
                      ) : <span className="muted small">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="split-even">
        <Card>
          <CardHead title="Role reference" />
          <div style={{ padding: '12px 16px' }} className="stack small">
            {ROLES.map((r) => (
              <div key={r}>
                <b>{ROLE_LABEL[r]}</b>
                <div className="muted">{ROLE_DESCRIPTION[r]}</div>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <CardHead title="Recent permission and membership changes" />
          {audit.items.filter((a) => a.action.startsWith('member.')).length === 0 ? (
            <Empty title="No membership changes recorded" />
          ) : (
            <ul className="timeline" style={{ padding: '4px 16px' }}>
              {audit.items.filter((a) => a.action.startsWith('member.')).slice(0, 10).map((a) => (
                <li key={a.id}><span className="when">{fmtDate(a.created_at)}</span><span>{a.summary}</span></li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {inviteOpen ? (
        <Modal title="Add a workspace member" onClose={() => setInviteOpen(false)}
          footer={<><Button onClick={() => setInviteOpen(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'invite'} onClick={invite}>Add member</Button></>}>
          {error ? <div className="err" style={{ marginBottom: 12 }}>{error}</div> : null}
          <Field label="Email address" required help="The person must be able to sign in with this address.">
            <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" />
          </Field>
          <Field label="Display name"><input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
          <Field label="Role" required help={ROLE_DESCRIPTION[form.role]}>
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
          </Field>
          <div className="muted small">
            Adding a person here grants access to this workspace only. Their permissions inside the workspace are
            checked on the server for every action.
          </div>
        </Modal>
      ) : null}
    </div>
  )
}

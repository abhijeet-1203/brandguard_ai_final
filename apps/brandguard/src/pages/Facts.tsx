import React, { useState } from 'react'
import { BadgeCheck, FilePlus2, RefreshCw, ShieldQuestion } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Field, Loading, Modal, useToast } from '../components/ui'
import { FACT_CATEGORIES, fmtDate, titleCase } from '../lib/types'

const emptyFact = {
  statement: '', category: 'product_specification', scope: '', value_text: '', numeric_value: '',
  unit: '', match_keywords: '', source_location: '', effective_date: '', expiration_date: '', notes: '',
}

export function Facts() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [filter, setFilter] = useState('')
  const [editing, setEditing] = useState<Row | null>(null)
  const [evidenceFor, setEvidenceFor] = useState<Row | null>(null)
  const [evidence, setEvidence] = useState({ title: '', description: '', evidence_type: 'document', source_location: '', strength: 'moderate' })
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = useRows<Row>('bg_facts', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'desc' }], limit: 300,
  }, [orgId])
  const evidenceRows = useRows<Row>('bg_fact_evidence', { limit: 300 }, [orgId])

  const today = new Date().toISOString().slice(0, 10)
  const state = (f: Row) => {
    if (f.expiration_date && String(f.expiration_date).slice(0, 10) < today) return 'expired'
    if (f.verification_status === 'no_evidence') return 'no_evidence'
    if (f.verification_status === 'pending' || f.approval_status === 'pending') return 'pending'
    if (f.verification_status === 'rejected' || f.approval_status === 'rejected') return 'rejected'
    if (f.verification_status === 'superseded') return 'superseded'
    if (f.verification_status === 'verified' && f.approval_status === 'approved') return 'approved'
    return 'pending'
  }
  const tone: Record<string, string> = {
    approved: 'b-ok', expired: 'b-high', pending: 'b-medium', rejected: 'b-critical',
    superseded: 'b-info', no_evidence: 'b-critical',
  }

  const filtered = rows.items.filter((f) => !filter || state(f) === filter)

  const save = async () => {
    if (!editing) return
    setError(null)
    const payload: Row = {
      statement: editing.statement, category: editing.category, scope: editing.scope,
      value_text: editing.value_text, numeric_value: editing.numeric_value === '' ? null : Number(editing.numeric_value),
      unit: editing.unit, source_location: editing.source_location,
      match_keywords: String(editing.match_keywords ?? '').split(',').map((s) => s.trim()).filter(Boolean),
      effective_date: editing.effective_date || null, expiration_date: editing.expiration_date || null,
      notes: editing.notes,
    }
    setBusy('save')
    const res = await runFn('bg_fact_action', {
      organization_id: orgId, action: editing.id ? 'update' : 'create', fact_id: editing.id, payload,
      evidence: null,
    })
    setBusy(null)
    if (!res.ok) { setError(res.error ?? 'The fact could not be saved.'); return }
    toast.push('Approved fact saved.', 'success')
    setEditing(null); rows.refresh()
  }

  const verify = async (fact: Row, status: string) => {
    const note = status === 'rejected' ? window.prompt('Why is this fact being rejected?') : null
    if (status === 'rejected' && !note) return
    setBusy(fact.id + status)
    const res = await runFn('bg_fact_action', {
      organization_id: orgId, action: 'verify', fact_id: fact.id,
      payload: { verification_status: status, note }, reason: note ?? `set to ${status}`,
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not update verification.', 'error'); return }
    toast.push(`Verification status set to ${status}.`, 'success')
    rows.refresh()
  }

  const addEvidence = async () => {
    if (!evidenceFor) return
    setBusy('evidence')
    const res = await runFn('bg_fact_action', {
      organization_id: orgId, action: 'add_evidence', fact_id: evidenceFor.id, evidence,
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'Could not add evidence.', 'error'); return }
    toast.push('Evidence recorded. The scope stored is what the evidence actually supports.', 'success')
    setEvidenceFor(null); setEvidence({ title: '', description: '', evidence_type: 'document', source_location: '', strength: 'moderate' })
    evidenceRows.refresh(); rows.refresh()
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Approved facts and claims</h1>
          <p className="muted small" style={{ margin: 0 }}>
            The only source the analysis treats as approved truth. Expired records are never treated as current.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { rows.refresh(); evidenceRows.refresh() }}><RefreshCw size={14} /> Refresh</Button>
        {session.can('manageFacts') ? (
          <Button size="sm" variant="primary" onClick={() => { setEditing({ ...emptyFact }); setError(null) }}>
            <FilePlus2 size={14} /> New fact
          </Button>
        ) : null}
      </div>

      <Card pad>
        <div className="row">
          <div>
            <label className="small muted" htmlFor="f-state">Verification state</label>
            <select id="f-state" value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All states</option>
              <option value="approved">Verified and approved</option>
              <option value="expired">Approved but expired</option>
              <option value="pending">Pending verification</option>
              <option value="rejected">Rejected</option>
              <option value="superseded">Superseded</option>
              <option value="no_evidence">No supporting evidence</option>
            </select>
          </div>
          <div className="spacer" />
          <div className="muted small">
            {rows.items.filter((f) => state(f) === 'approved').length} approved · {rows.items.filter((f) => state(f) === 'expired').length} expired
          </div>
        </div>
      </Card>

      <Card>
        <CardHead title="Reference library" subtitle="Numeric values here are compared against submitted content by the deterministic engine" />
        {rows.loading ? <Loading label="Loading approved facts…" /> : filtered.length === 0 ? (
          <Empty title="No facts in this view" hint="Add the product specifications, prices, warranty terms and approved statements reviewers keep checking." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Approved facts">
              <thead>
                <tr><th>Statement</th><th>Category</th><th>Value</th><th>State</th><th>Effective</th><th>Expires</th><th>Evidence</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {filtered.map((f) => {
                  const st = state(f)
                  const ev = evidenceRows.items.filter((e) => e.fact_id === f.id)
                  return (
                    <tr key={f.id}>
                      <td style={{ maxWidth: 380 }}>
                        <div style={{ fontWeight: 560 }}>{f.statement}</div>
                        {f.scope ? <div className="muted small">Scope: {f.scope}</div> : null}
                        {f.source_location ? <div className="muted small">Source: {f.source_location}</div> : null}
                      </td>
                      <td className="small">{titleCase(f.category)}</td>
                      <td className="mono">{f.value_text ?? (f.numeric_value ?? '—')}</td>
                      <td>
                        <Badge tone={tone[st]}>{st.replace(/_/g, ' ')}</Badge>
                        {st === 'expired' ? <div className="muted small">requires verification</div> : null}
                      </td>
                      <td className="muted small nowrap">{fmtDate(f.effective_date)}</td>
                      <td className="muted small nowrap">{fmtDate(f.expiration_date)}</td>
                      <td className="small">{ev.length ? ev.map((e) => e.title).join('; ') : <span className="muted">none recorded</span>}</td>
                      <td>
                        <div className="row" style={{ gap: 4 }}>
                          {session.can('manageFacts') ? (
                            <>
                              <button className="btn-link" onClick={() => { setEditing({ ...f, match_keywords: (f.match_keywords ?? []).join(', ') }); setError(null) }}>edit</button>
                              <button className="btn-link" disabled={busy === f.id + 'verified'} onClick={() => verify(f, 'verified')}>verify</button>
                              <button className="btn-link" disabled={busy === f.id + 'rejected'} onClick={() => verify(f, 'rejected')}>reject</button>
                              <button className="btn-link" onClick={() => setEvidenceFor(f)}>+ evidence</button>
                            </>
                          ) : <span className="muted small">read-only</span>}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card pad>
        <div className="row small">
          <ShieldQuestion size={15} />
          <span>
            A claim is only supported to the <b>exact scope</b> recorded here. Evidence that a product passed one
            laboratory test does not substantiate a broader “best on the market” claim — the engine reports that as
            missing evidence, never as proof that a claim is false.
          </span>
        </div>
      </Card>

      {editing ? (
        <Modal title={editing.id ? 'Edit approved fact' : 'New approved fact'} onClose={() => setEditing(null)}
          footer={<><Button onClick={() => setEditing(null)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'save'} onClick={save}>Save</Button></>}>
          {error ? <div className="err" style={{ marginBottom: 12 }}>{error}</div> : null}
          <Field label="Statement" required help="State exactly what is approved, in the words that may be used.">
            <textarea rows={3} value={editing.statement} onChange={(e) => setEditing({ ...editing, statement: e.target.value })} />
          </Field>
          <div className="grid cols-2">
            <Field label="Category" required>
              <select value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })}>
                {FACT_CATEGORIES.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              </select>
            </Field>
            <Field label="Value as published"><input value={editing.value_text ?? ''} onChange={(e) => setEditing({ ...editing, value_text: e.target.value })} placeholder="e.g. one-year limited warranty" /></Field>
            <Field label="Numeric value" help="Used for exact comparison. Warranty in months, prices as a number, percentages as a number.">
              <input type="number" step="any" value={editing.numeric_value ?? ''} onChange={(e) => setEditing({ ...editing, numeric_value: e.target.value })} />
            </Field>
            <Field label="Unit" help="months, currency, percent, m2, year">
              <input value={editing.unit ?? ''} onChange={(e) => setEditing({ ...editing, unit: e.target.value })} />
            </Field>
            <Field label="Effective date"><input type="date" value={editing.effective_date ?? ''} onChange={(e) => setEditing({ ...editing, effective_date: e.target.value })} /></Field>
            <Field label="Expiration date" help="After this date the fact is no longer current approved truth."><input type="date" value={editing.expiration_date ?? ''} onChange={(e) => setEditing({ ...editing, expiration_date: e.target.value })} /></Field>
          </div>
          <Field label="Match keywords (comma separated)" help="Words that make this fact relevant to a sentence, e.g. warranty, guarantee.">
            <input value={editing.match_keywords ?? ''} onChange={(e) => setEditing({ ...editing, match_keywords: e.target.value })} />
          </Field>
          <Field label="Exact scope substantiated" help="Describe precisely what the supporting evidence covers.">
            <textarea rows={2} value={editing.scope ?? ''} onChange={(e) => setEditing({ ...editing, scope: e.target.value })} />
          </Field>
          <Field label="Source location"><input value={editing.source_location ?? ''} onChange={(e) => setEditing({ ...editing, source_location: e.target.value })} /></Field>
          <Field label="Notes"><textarea rows={2} value={editing.notes ?? ''} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} /></Field>
        </Modal>
      ) : null}

      {evidenceFor ? (
        <Modal title={`Evidence for: ${evidenceFor.statement.slice(0, 60)}…`} onClose={() => setEvidenceFor(null)}
          footer={<><Button onClick={() => setEvidenceFor(null)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'evidence'} onClick={addEvidence}><BadgeCheck size={14} /> Record evidence</Button></>}>
          <Field label="Evidence title" required><input value={evidence.title} onChange={(e) => setEvidence({ ...evidence, title: e.target.value })} placeholder="e.g. AHAM certificate AHAM-2026-1188" /></Field>
          <div className="grid cols-2">
            <Field label="Type">
              <select value={evidence.evidence_type} onChange={(e) => setEvidence({ ...evidence, evidence_type: e.target.value })}>
                {['lab_test', 'certificate', 'document', 'internal_record', 'external_source', 'testimonial'].map((t) => (
                  <option key={t} value={t}>{titleCase(t)}</option>))}
              </select>
            </Field>
            <Field label="Strength">
              <select value={evidence.strength} onChange={(e) => setEvidence({ ...evidence, strength: e.target.value })}>
                {['strong', 'moderate', 'weak', 'unverified'].map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Source location"><input value={evidence.source_location} onChange={(e) => setEvidence({ ...evidence, source_location: e.target.value })} /></Field>
          <Field label="What exactly does this evidence cover?" help="The stored scope limits what the platform will treat as substantiated.">
            <textarea rows={3} value={evidence.description} onChange={(e) => setEvidence({ ...evidence, description: e.target.value })} />
          </Field>
        </Modal>
      ) : null}
    </div>
  )
}

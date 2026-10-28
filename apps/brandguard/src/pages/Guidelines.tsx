import React, { useState } from 'react'
import { Archive, BookOpen, CheckCircle2, FileUp, Plus, RefreshCw, Sparkles } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useRows } from '../lib/hooks'
import { runFn, uploadFile, Row } from '../lib/api'
import {
  Badge, Button, Card, CardHead, Empty, Field, Loading, Modal, SeverityBadge, useToast,
} from '../components/ui'
import { ENFORCEMENT_LABEL, GUIDELINE_CATEGORIES, RULE_TYPES, fmtDateTime, titleCase } from '../lib/types'

type Draft = Row & { match_terms_text?: string; replace_with_text?: string }

const empty: Draft = {
  title: '', description: '', category: 'terminology', rule_type: 'prohibited_term', severity: 'medium',
  enforcement: 'mandatory', rule_text: '', match_terms_text: '', replace_with_text: '',
  compliant_examples: '', noncompliant_examples: '', source_section: '', source_page: null,
}

export function Guidelines() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [statusFilter, setStatusFilter] = useState('active')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [editing, setEditing] = useState<Draft | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [docTitle, setDocTitle] = useState('')
  const [uploading, setUploading] = useState(false)
  const [proposals, setProposals] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = useRows<Row>('bg_guidelines', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'code', direction: 'asc' }], limit: 300,
  }, [orgId])

  const versions = useRows<Row>('bg_guideline_versions', { limit: 300 }, [orgId])
  const docs = useRows<Row>('bg_documents', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'desc' }], limit: 100,
  }, [orgId])

  const filtered = rows.items.filter((r) =>
    (statusFilter === 'all' || r.status === statusFilter) && (!categoryFilter || r.category === categoryFilter))

  const act = async (action: string, guideline: Row, payload?: Row, reason?: string) => {
    setBusy(guideline.id + action)
    const res = await runFn('bg_guideline_action', {
      organization_id: orgId, action, guideline_id: guideline.id, payload, reason,
    })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    toast.push((res.output as any)?.message ?? 'Done.', 'success')
    rows.refresh(); versions.refresh()
  }

  const save = async () => {
    if (!editing) return
    setError(null)
    const match = (editing.match_terms_text ?? '').split(',').map((s) => s.trim()).filter(Boolean)
    const replace = (editing.replace_with_text ?? '').split(',').map((s) => s.trim()).filter(Boolean)
    const payload: Row = {
      title: editing.title, description: editing.description, category: editing.category,
      rule_type: editing.rule_type, severity: editing.severity, enforcement: editing.enforcement,
      rule_text: editing.rule_text, source_section: editing.source_section,
      source_page: editing.source_page ? Number(editing.source_page) : null,
      compliant_examples: editing.compliant_examples ? String(editing.compliant_examples).split('\n').filter(Boolean) : [],
      noncompliant_examples: editing.noncompliant_examples ? String(editing.noncompliant_examples).split('\n').filter(Boolean) : [],
      match_terms: match.length ? { terms: match, ...(replace.length ? { replace_with: replace } : {}) } : null,
    }
    const action = editing.id ? 'update' : 'create'
    setBusy('save')
    const res = await runFn('bg_guideline_action', { organization_id: orgId, action, guideline_id: editing.id, payload })
    setBusy(null)
    if (!res.ok) { setError(res.error ?? 'The rule could not be saved.'); return }
    toast.push((res.output as any)?.message ?? 'Rule saved.', 'success')
    setEditing(null); rows.refresh()
  }

  const extract = async () => {
    setError(null)
    if (!docTitle.trim()) { setError('Give the document a title.'); return }
    setUploading(true)
    try {
      const input = document.getElementById('guideline-file') as HTMLInputElement | null
      const file = input?.files?.[0]
      if (!file) { setError('Choose a document to upload.'); setUploading(false); return }
      const uploaded = await uploadFile(file, `/brandguard/${session.activeOrg?.slug ?? 'workspace'}/guidelines`)
      const path = uploaded.path ?? uploaded.file_path
      const ingest = await runFn('bg_document_ingest', {
        organization_id: orgId, title: docTitle, kind: 'guideline_source', file_path: path,
        mime_type: file.type, size_bytes: file.size,
      })
      if (!ingest.ok) { setError(ingest.error ?? 'The document could not be processed.'); setUploading(false); return }
      const doc = ingest.output as any
      if (doc.extraction_status === 'no_text' || doc.extraction_status === 'failed') {
        setError(`${doc.extraction_note} You can still add rules manually.`)
        setUploading(false); docs.refresh(); return
      }
      const res = await runFn('bg_guideline_extract', { organization_id: orgId, document_id: doc.document_id })
      if (!res.ok) { setError(res.error ?? 'Extraction failed.'); setUploading(false); return }
      const out = res.output as any
      setProposals(out.proposals ?? [])
      toast.push(out.message ?? 'Rules proposed.', 'success')
      rows.refresh(); docs.refresh()
    } catch (e: any) {
      setError(e?.message ?? 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  if (!session.can('authorGuidelines')) {
    return (
      <Card>
        <CardHead title="Brand guidelines" subtitle="Read-only: your role cannot author rules" />
        {rows.loading ? <Loading /> : (
          <div className="table-wrap">
            <table aria-label="Brand guidelines">
              <thead><tr><th>Code</th><th>Rule</th><th>Category</th><th>Enforcement</th><th>Severity</th><th>Status</th></tr></thead>
              <tbody>
                {rows.items.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.code}</td>
                    <td><div style={{ fontWeight: 560 }}>{r.title}</div><div className="muted small">{r.rule_text}</div></td>
                    <td className="small">{titleCase(r.category)}</td>
                    <td><Badge tone={r.enforcement === 'mandatory' ? 'b-high' : r.enforcement === 'recommended' ? 'b-medium' : 'b-info'}>{ENFORCEMENT_LABEL[r.enforcement]}</Badge></td>
                    <td><SeverityBadge severity={r.severity} /></td>
                    <td><Badge tone={r.status === 'active' ? 'b-ok' : r.status === 'draft' ? 'b-info' : 'b-medium'}>{r.status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    )
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Brand guidelines</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Rules are only enforced once an authorised person approves them. Extracted or AI-proposed rules stay drafts.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { rows.refresh(); versions.refresh() }}><RefreshCw size={14} /> Refresh</Button>
        <Button size="sm" onClick={() => setUploadOpen(true)}><FileUp size={14} /> Import from document</Button>
        <Button size="sm" variant="primary" onClick={() => { setEditing({ ...empty }); setError(null) }}><Plus size={14} /> New rule</Button>
      </div>

      <Card pad>
        <div className="row">
          <div>
            <label className="small muted" htmlFor="g-status">Status</label>
            <select id="g-status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="active">Active (enforced)</option>
              <option value="draft">Draft</option>
              <option value="pending_approval">Pending approval</option>
              <option value="archived">Archived</option>
              <option value="all">All</option>
            </select>
          </div>
          <div>
            <label className="small muted" htmlFor="g-cat">Category</label>
            <select id="g-cat" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
              <option value="">All categories</option>
              {GUIDELINE_CATEGORIES.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
            </select>
          </div>
          <div className="spacer" />
          <div className="muted small">
            {filtered.length} rule(s) · {rows.items.filter((r) => r.status === 'active').length} enforced
          </div>
        </div>
      </Card>

      <Card>
        <CardHead title="Rule library" subtitle="Mandatory rules block approval when configured; recommended and advisory rules inform the reviewer" />
        {rows.loading ? <Loading label="Loading rules…" /> : filtered.length === 0 ? (
          <Empty title="No rules in this view" hint="Create a rule manually or import them from a brand document." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Guideline rules">
              <thead>
                <tr><th>Code</th><th>Rule</th><th>Category</th><th>Enforcement</th><th>Severity</th><th>Version</th><th>Status</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.code}</td>
                    <td style={{ maxWidth: 420 }}>
                      <div style={{ fontWeight: 560 }}>{r.title}</div>
                      <div className="muted small">{r.rule_text}</div>
                      {r.source_section ? <div className="muted small">Source: {r.source_section}</div> : null}
                      {r.status === 'draft' ? <div className="muted small">Draft — not enforced</div> : null}
                    </td>
                    <td className="small">{titleCase(r.category)}</td>
                    <td><Badge tone={r.enforcement === 'mandatory' ? 'b-high' : r.enforcement === 'recommended' ? 'b-medium' : 'b-info'}>{ENFORCEMENT_LABEL[r.enforcement]}</Badge></td>
                    <td><SeverityBadge severity={r.severity} /></td>
                    <td className="mono">v{r.version}</td>
                    <td><Badge tone={r.status === 'active' ? 'b-ok' : r.status === 'draft' ? 'b-info' : 'b-medium'}>{r.status.replace(/_/g, ' ')}</Badge></td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <button className="btn-link" onClick={() => {
                          setEditing({
                            ...r,
                            match_terms_text: (r.match_terms?.terms ?? []).join(', '),
                            replace_with_text: (r.match_terms?.replace_with ?? []).join(', '),
                            compliant_examples: (r.compliant_examples ?? []).join('\n'),
                            noncompliant_examples: (r.noncompliant_examples ?? []).join('\n'),
                          })
                          setError(null)
                        }}>edit</button>
                        {r.status !== 'active' ? (
                          <button className="btn-link" disabled={busy === r.id + 'approve'} onClick={() => act('approve', r)}>approve</button>
                        ) : null}
                        {r.status === 'draft' ? (
                          <button className="btn-link" disabled={busy === r.id + 'submit_for_approval'} onClick={() => act('submit_for_approval', r)}>submit</button>
                        ) : null}
                        {r.status !== 'archived' ? (
                          <button className="btn-link" disabled={busy === r.id + 'archive'} onClick={() => act('archive', r, undefined, 'archived from the rule library')}>
                            <Archive size={12} /> archive
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="split">
        <Card>
          <CardHead title="Rule version history" subtitle="Every change is snapshotted with its author" />
          {versions.items.length === 0 ? <Empty title="No versions recorded" /> : (
            <div className="table-wrap">
              <table aria-label="Rule versions">
                <thead><tr><th>Rule</th><th>Version</th><th>Summary</th><th>By</th><th>When</th></tr></thead>
                <tbody>
                  {versions.items.slice(0, 25).map((v) => {
                    const g = rows.items.find((x) => x.id === v.guideline_id)
                    return (
                      <tr key={v.id}>
                        <td className="mono">{g?.code ?? '—'}</td>
                        <td className="mono">v{v.version}</td>
                        <td className="small">{v.change_summary}</td>
                        <td className="small">{v.changed_by_email}</td>
                        <td className="muted small nowrap">{fmtDateTime(v.created_at)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card>
          <CardHead title="Imported brand documents" />
          {docs.items.length === 0 ? <Empty title="No documents imported yet"
            hint="Upload a brand book or policy document to propose rules from it." /> : (
            <div className="stack" style={{ padding: '10px 14px' }}>
              {docs.items.slice(0, 8).map((d) => (
                <div key={d.id}>
                  <div className="row" style={{ gap: 6 }}>
                    <BookOpen size={14} />
                    <span className="small" style={{ fontWeight: 560 }}>{d.title}</span>
                    <Badge tone={d.extraction_status === 'extracted' ? 'b-ok' : d.extraction_status === 'failed' ? 'b-critical' : 'b-medium'}>
                      {d.extraction_status}
                    </Badge>
                  </div>
                  <div className="muted small">{d.extraction_note}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {editing ? (
        <Modal title={editing.id ? `Edit rule ${editing.code}` : 'New guideline rule'} onClose={() => setEditing(null)}
          footer={<><Button onClick={() => setEditing(null)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'save'} onClick={save}>Save rule</Button></>}>
          {error ? <div className="err" style={{ marginBottom: 12 }}>{error}</div> : null}
          <Field label="Rule title" required><input value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} /></Field>
          <div className="grid cols-2">
            <Field label="Category" required>
              <select value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })}>
                {GUIDELINE_CATEGORIES.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              </select>
            </Field>
            <Field label="Rule type" required help="Determines which checks apply. Terminology rules are matched exactly.">
              <select value={editing.rule_type} onChange={(e) => setEditing({ ...editing, rule_type: e.target.value })}>
                {RULE_TYPES.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              </select>
            </Field>
            <Field label="Severity" required>
              <select value={editing.severity} onChange={(e) => setEditing({ ...editing, severity: e.target.value })}>
                {['critical', 'high', 'medium', 'low', 'informational'].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
            <Field label="Enforcement level" required
              help="Mandatory = must be followed. Recommended = normally followed. Advisory = informs the reviewer only.">
              <select value={editing.enforcement} onChange={(e) => setEditing({ ...editing, enforcement: e.target.value })}>
                {['mandatory', 'recommended', 'advisory'].map((s) => <option key={s} value={s}>{ENFORCEMENT_LABEL[s]}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Rule text" required help="State the rule exactly as it should be applied.">
            <textarea rows={3} value={editing.rule_text} onChange={(e) => setEditing({ ...editing, rule_text: e.target.value })} />
          </Field>
          <Field label="Terms to match (comma separated)"
            help="Used by the deterministic engine for prohibited, deprecated and required terminology.">
            <input value={editing.match_terms_text ?? ''} onChange={(e) => setEditing({ ...editing, match_terms_text: e.target.value })}
              placeholder="e.g. PureAir Pro, Pure Air Pro" />
          </Field>
          <Field label="Approved replacement (comma separated, optional)">
            <input value={editing.replace_with_text ?? ''} onChange={(e) => setEditing({ ...editing, replace_with_text: e.target.value })}
              placeholder="e.g. Northstar AirPure 300" />
          </Field>
          <div className="grid cols-2">
            <Field label="Compliant examples (one per line)">
              <textarea rows={2} value={editing.compliant_examples ?? ''} onChange={(e) => setEditing({ ...editing, compliant_examples: e.target.value })} />
            </Field>
            <Field label="Non-compliant examples (one per line)">
              <textarea rows={2} value={editing.noncompliant_examples ?? ''} onChange={(e) => setEditing({ ...editing, noncompliant_examples: e.target.value })} />
            </Field>
          </div>
          <div className="grid cols-2">
            <Field label="Source section"><input value={editing.source_section ?? ''} onChange={(e) => setEditing({ ...editing, source_section: e.target.value })} /></Field>
            <Field label="Source page"><input type="number" value={editing.source_page ?? ''} onChange={(e) => setEditing({ ...editing, source_page: e.target.value })} /></Field>
          </div>
          <div className="muted small">
            Saving a change to an active rule returns it to pending approval, so a change cannot take effect without review.
          </div>
        </Modal>
      ) : null}

      {uploadOpen ? (
        <Modal title="Import rules from a brand document" onClose={() => { setUploadOpen(false); setProposals(null) }}
          footer={<>
            <Button onClick={() => { setUploadOpen(false); setProposals(null) }}>Close</Button>
            <Button variant="primary" disabled={uploading} onClick={extract}>
              {uploading ? <span className="spinner" aria-hidden="true" /> : <Sparkles size={14} />} Upload and propose rules
            </Button>
          </>}>
          {error ? <div className="err" style={{ marginBottom: 12 }}>{error}</div> : null}
          <Field label="Document title" required><input value={docTitle} onChange={(e) => setDocTitle(e.target.value)} placeholder="e.g. Brand Book 2026" /></Field>
          <Field label="Document" required help="PDF, DOCX, Markdown or text. The platform converts it to text before extraction.">
            <input id="guideline-file" type="file" accept=".pdf,.docx,.doc,.txt,.md" />
          </Field>
          {proposals ? (
            <div className="stack">
              <div className="notice">
                {proposals.length} candidate rule(s) were proposed and saved as <b>drafts</b>. They are not enforced
                until you approve each one in the rule library.
              </div>
              {proposals.map((p) => (
                <div key={p.id} style={{ borderTop: '1px solid var(--line)', paddingTop: 8 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <Badge tone="b-accent">{p.code}</Badge>
                    <span className="small" style={{ fontWeight: 560 }}>{p.title}</span>
                    <Badge tone={p.enforcement === 'mandatory' ? 'b-high' : 'b-info'}>{p.enforcement}</Badge>
                    <SeverityBadge severity={p.severity} />
                  </div>
                  <div className="small">{p.rule_text}</div>
                  {p.source_quote ? <div className="muted small">“{String(p.source_quote).slice(0, 200)}”</div> : null}
                  {p.ambiguity_note ? <div className="warn-msg small">Ambiguity: {p.ambiguity_note}</div> : null}
                </div>
              ))}
              <Button size="sm" onClick={() => { setProposals(null); setUploadOpen(false) }}>
                <CheckCircle2 size={14} /> Done — review them in the rule library
              </Button>
            </div>
          ) : null}
        </Modal>
      ) : null}
    </div>
  )
}

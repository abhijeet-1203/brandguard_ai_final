import React, { useState } from 'react'
import { Archive, CheckCircle2, FileUp, RefreshCw, Replace } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { computeImageMeta, runFn, uploadFile, Row } from '../lib/api'
import { lemmaClient } from '../lemma-client'
import { Badge, Button, Card, CardHead, Empty, Field, Loading, Modal, useToast } from '../components/ui'
import { ASSET_CATEGORIES, fmtDate, titleCase } from '../lib/types'

export function Assets() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [filter, setFilter] = useState('approved')
  const [registerOpen, setRegisterOpen] = useState(false)
  const [form, setForm] = useState<Row>({ name: '', category: 'logo', usage_restrictions: '', notes: '' })
  const [replaceFor, setReplaceFor] = useState<Row | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = useRows<Row>('bg_assets', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'desc' }], limit: 200,
  }, [orgId])
  const versions = useRows<Row>('bg_asset_versions', { limit: 200 }, [orgId])

  const filtered = rows.items.filter((a) => filter === 'all' || a.approval_status === filter)

  const uploadImage = async (file: File): Promise<Row> => {
    const uploaded = await uploadFile(file, `/brandguard/${session.activeOrg?.slug ?? 'workspace'}/assets`)
    const meta = await computeImageMeta(file).catch(() => null)
    return {
      file_path: uploaded.path ?? uploaded.file_path, mime_type: file.type, size_bytes: file.size,
      width: meta?.width ?? null, height: meta?.height ?? null, ahash: meta?.ahash ?? null, colors: meta?.colors ?? [],
    }
  }

  const register = async () => {
    setError(null)
    const input = document.getElementById('asset-file') as HTMLInputElement | null
    const file = input?.files?.[0]
    if (!form.name?.trim()) { setError('Give the asset a name.'); return }
    if (!file) { setError('Choose a file to upload.'); return }
    setBusy('register')
    try {
      const meta = await uploadImage(file)
      const res = await runFn('bg_asset_action', {
        organization_id: orgId, action: 'register',
        payload: { ...form, ...meta },
      })
      if (!res.ok) { setError(res.error ?? 'The asset could not be registered.'); setBusy(null); return }
      toast.push((res.output as any)?.message ?? 'Asset registered.', 'success')
      setRegisterOpen(false); setForm({ name: '', category: 'logo', usage_restrictions: '', notes: '' })
      rows.refresh(); versions.refresh()
    } catch (e: any) {
      setError(e?.message ?? 'Upload failed.')
    } finally {
      setBusy(null)
    }
  }

  const act = async (action: string, asset: Row, payload?: Row, reason?: string) => {
    setBusy(asset.id + action)
    const res = await runFn('bg_asset_action', { organization_id: orgId, action, asset_id: asset.id, payload, reason })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return }
    toast.push((res.output as any)?.message ?? 'Done.', 'success')
    rows.refresh(); versions.refresh()
  }

  const replace = async () => {
    if (!replaceFor) return
    const input = document.getElementById('replace-file') as HTMLInputElement | null
    const file = input?.files?.[0]
    if (!file) { toast.push('Choose the replacement file.', 'error'); return }
    setBusy('replace')
    try {
      const meta = await uploadImage(file)
      await act('replace', replaceFor, { ...meta, change_summary: 'Replaced from the asset library' })
      setReplaceFor(null)
    } finally { setBusy(null) }
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Asset library</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Approved logos, campaign images and templates. Perceptual hashes are stored so the visual check can compare
            submitted images against these files.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { rows.refresh(); versions.refresh() }}><RefreshCw size={14} /> Refresh</Button>
        <Button size="sm" variant="primary" onClick={() => { setRegisterOpen(true); setError(null) }}><FileUp size={14} /> Upload asset</Button>
      </div>

      <Card pad>
        <div className="row">
          <div>
            <label className="small muted" htmlFor="a-status">Status</label>
            <select id="a-status" value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="approved">Approved</option>
              <option value="pending">Pending approval</option>
              <option value="archived">Archived</option>
              <option value="all">All</option>
            </select>
          </div>
          <div className="spacer" />
          <div className="muted small">{rows.items.filter((a) => a.approval_status === 'approved').length} approved asset(s)</div>
        </div>
      </Card>

      <div className="grid cols-3">
        {rows.loading ? <Card pad><Loading /></Card> : filtered.length === 0 ? (
          <Card pad><Empty title="No assets in this view" hint="Upload the approved logo and campaign assets so reviews can verify what was used." /></Card>
        ) : filtered.map((a) => (
          <Card key={a.id}>
            <div style={{ background: 'var(--surface-2)', padding: 12, borderBottom: '1px solid var(--line)' }}>
              {a.file_path && /\.(png|jpe?g|webp|svg)$/i.test(a.file_path) ? (
                <AssetPreview path={a.file_path} name={a.name} />
              ) : <div className="muted small">No inline preview for this file type.</div>}
            </div>
            <div style={{ padding: 14 }} className="stack">
              <div className="row">
                <div style={{ fontWeight: 580, flex: 1 }}>{a.name}</div>
                <Badge tone={a.approval_status === 'approved' ? 'b-ok' : a.approval_status === 'pending' ? 'b-medium' : 'b-info'}>{a.approval_status}</Badge>
              </div>
              <div className="muted small">
                {titleCase(a.category)} · v{a.version} · {a.width && a.height ? `${a.width}×${a.height}` : 'dimensions unknown'}
              </div>
              {a.usage_restrictions ? <div className="small">Restrictions: {a.usage_restrictions}</div> : null}
              <div className="muted small">
                Hash <span className="mono">{String(a.image_hash ?? '—').slice(0, 12)}</span> · uploaded {fmtDate(a.created_at)}
              </div>
              {a.dominant_colors?.length ? (
                <div className="row" style={{ gap: 4 }}>
                  {a.dominant_colors.map((c: string) => (
                    <span key={c} title={c} style={{ width: 16, height: 16, borderRadius: 4, background: c, border: '1px solid var(--line)' }} />
                  ))}
                </div>
              ) : null}
              {a.notes ? <div className="muted small">{a.notes}</div> : null}
              <div className="row" style={{ gap: 4 }}>
                {session.can('approveAsset') && a.approval_status !== 'approved' ? (
                  <Button size="sm" disabled={busy === a.id + 'approve'} onClick={() => act('approve', a)}><CheckCircle2 size={13} /> Approve</Button>
                ) : null}
                <Button size="sm" onClick={() => setReplaceFor(a)}><Replace size={13} /> Replace</Button>
                {session.can('approveAsset') && a.approval_status !== 'archived' ? (
                  <Button size="sm" disabled={busy === a.id + 'archive'} onClick={() => act('archive', a, undefined, 'archived from the asset library')}>
                    <Archive size={13} /> Archive
                  </Button>
                ) : null}
              </div>
              {versions.items.filter((v) => v.asset_id === a.id).length > 1 ? (
                <details>
                  <summary className="small">Version history ({versions.items.filter((v) => v.asset_id === a.id).length})</summary>
                  <ul className="small">
                    {versions.items.filter((v) => v.asset_id === a.id).map((v) => (
                      <li key={v.id}>v{v.version} — {v.change_summary} ({v.created_by ?? '—'}, {fmtDate(v.created_at)})</li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </div>
          </Card>
        ))}
      </div>

      <Card pad>
        <div className="row small">
          <CheckCircle2 size={14} />
          <span>
            A hash similarity score is <b>evidence for review, not proof</b> of brand compliance. The visual check reports
            “could not be matched” rather than assuming an image is wrong.
          </span>
        </div>
      </Card>

      {registerOpen ? (
        <Modal title="Upload an approved asset" onClose={() => setRegisterOpen(false)}
          footer={<><Button onClick={() => setRegisterOpen(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'register'} onClick={register}>Upload and register</Button></>}>
          {error ? <div className="err" style={{ marginBottom: 12 }}>{error}</div> : null}
          <Field label="Asset name" required><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Northstar Living logo — primary" /></Field>
          <Field label="Category" required>
            <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {ASSET_CATEGORIES.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
            </select>
          </Field>
          <Field label="File" required help="PNG, JPEG, WebP, SVG or PDF. The perceptual hash and dominant colours are computed in your browser.">
            <input id="asset-file" type="file" accept=".png,.jpg,.jpeg,.webp,.svg,.pdf" />
          </Field>
          <Field label="Usage restrictions"><textarea rows={2} value={form.usage_restrictions} onChange={(e) => setForm({ ...form, usage_restrictions: e.target.value })} placeholder="e.g. Keep proportions. Do not recolour." /></Field>
          <Field label="Notes"><textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          <div className="muted small">A new upload starts as <b>pending approval</b> and is not used for comparison until an approver signs it off.</div>
        </Modal>
      ) : null}

      {replaceFor ? (
        <Modal title={`Replace ${replaceFor.name}`} onClose={() => setReplaceFor(null)}
          footer={<><Button onClick={() => setReplaceFor(null)}>Cancel</Button>
            <Button variant="primary" disabled={busy === 'replace'} onClick={replace}>Upload new version</Button></>}>
          <div className="notice">
            The previous file stays in version history, so historical reviews still point at the asset that was actually used.
          </div>
          <Field label="New file" required><input id="replace-file" type="file" accept=".png,.jpg,.jpeg,.webp,.svg,.pdf" /></Field>
        </Modal>
      ) : null}
    </div>
  )
}

function AssetPreview({ path, name }: { path: string; name: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res: any = await lemmaClient.files.getUrl(path)
        if (!cancelled && res) setUrl(res.url ?? (res as any).app_url ?? null)
      } catch { if (!cancelled) setFailed(true) }
    })()
    return () => { cancelled = true }
  }, [path])
  if (failed) return <div className="muted small">Preview unavailable — use the file link in the workspace.</div>
  if (!url) return <div className="skeleton" style={{ height: 90 }} />
  return <img src={url} alt={`${name} preview`} style={{ maxWidth: '100%', maxHeight: 140, display: 'block', margin: '0 auto' }} />
}

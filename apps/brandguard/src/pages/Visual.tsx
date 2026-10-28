import React, { useState } from 'react'
import { Image as ImageIcon, RefreshCw, ScanEye } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, useToast } from '../components/ui'
import { fmtDateTime } from '../lib/types'
import type { Route } from '../components/Shell'

export function Visual({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id ? safeId(session.activeOrg.id) : null
  const [busy, setBusy] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const reviewsWithImages = useSqlQuery<Row>(orgId ? `
    select id, title, channel, status, current_version, image_paths
    from bg_reviews
    where organization_id = '${orgId}' and image_paths is not null
    order by created_at desc limit 40` : null, [orgId])

  const scans = useSqlQuery<Row>(orgId ? `
    select * from bg_visual_scans where organization_id = '${orgId}' order by created_at desc limit 40` : null, [orgId])

  const run = async (reviewId: string) => {
    setBusy(reviewId)
    const res = await runFn('bg_visual_scan', { organization_id: orgId, review_id: reviewId })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The visual scan failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Visual scan complete.', 'success')
    scans.refresh()
    if (out.scans?.[0]?.image_path) setOpen(out.scans[0].image_path)
  }

  const current = scans.items.find((s) => s.image_path === open) ?? scans.items[0]

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Visual compliance</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Reads the creative: text inside the image is transcribed by a vision model and checked against the
            workspace rules, while palette, margins, text bands and logo placement are measured from the pixels.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => scans.refresh()}><RefreshCw size={14} /> Refresh</Button>
      </div>

      <Card>
        <CardHead title="Creatives attached to submissions" subtitle="Run a scan to measure the image and read its text" />
        {reviewsWithImages.loading ? <Loading /> : reviewsWithImages.items.length === 0 ? (
          <Empty title="No creatives attached yet"
            hint="Attach an image (PNG, JPEG or WebP) when submitting content and it will appear here." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Creatives">
              <thead><tr><th>Submission</th><th>Images</th><th>Status</th><th>Scanned</th><th /></tr></thead>
              <tbody>
                {reviewsWithImages.items.map((r) => {
                  const images: Row[] = Array.isArray(r.image_paths) ? r.image_paths : []
                  const done = images.filter((i) => scans.items.some((s) => s.image_path === i.path))
                  return (
                    <tr key={r.id}>
                      <td>
                        <button className="btn-link" onClick={() => navigate({ page: 'review', id: r.id })}>{r.title}</button>
                        <div className="muted small">{r.channel ?? 'unspecified channel'} · v{r.current_version}</div>
                      </td>
                      <td className="small">{images.map((i) => i.name).join(', ') || '—'}</td>
                      <td><Badge tone={done.length === images.length ? 'b-ok' : 'b-medium'}>
                        {done.length}/{images.length} scanned</Badge></td>
                      <td className="muted small nowrap">
                        {done.length ? fmtDateTime(done[0].created_at) : '—'}
                      </td>
                      <td>
                        <Button size="sm" disabled={busy === r.id} onClick={() => run(r.id)}>
                          {busy === r.id ? <span className="spinner" aria-hidden="true" /> : <ScanEye size={13} />} Scan
                        </Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {scans.items.length > 0 ? (
        <div className="row">
          <label className="small muted" htmlFor="scan-pick">Scan</label>
          <select id="scan-pick" value={current?.image_path ?? ''} onChange={(e) => setOpen(e.target.value)}
            style={{ width: 'auto', minWidth: 320 }}>
            {scans.items.map((s) => (
              <option key={s.id} value={s.image_path}>
                {s.image_name} — score {s.score} ({fmtDateTime(s.created_at)})
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {current ? (
        <div className="split">
          <Card>
            <CardHead title={`${current.image_name} — measured regions`}
              subtitle={current.overlay_path
                ? 'The overlay is written back to pod files: green = content bounds, blue = text bands, yellow = logo block, red = off-palette cells'
                : 'No overlay could be produced for this file'}
              actions={<Badge tone={Number(current.score) >= 80 ? 'b-ok' : Number(current.score) >= 55 ? 'b-medium' : 'b-critical'}>
                {current.score}/100</Badge>} />
            <div style={{ padding: 16 }} className="stack">
              <OverlayPreview path={current.overlay_path} name={current.image_name} />
              <div className="grid cols-2">
                <div className="small">
                  <div className="muted">Dimensions</div>
                  <div>{current.width ?? '—'} × {current.height ?? '—'} px</div>
                </div>
                <div className="small">
                  <div className="muted">Palette conformance</div>
                  <div>{current.palette_conformance !== null && current.palette_conformance !== undefined
                    ? `${Math.round(Number(current.palette_conformance) * 100)}% of pixels within tolerance of the approved palette`
                    : 'not measured'}</div>
                </div>
                <div className="small">
                  <div className="muted">Content margins</div>
                  <div>{current.geometry?.margins
                    ? Object.entries(current.geometry.margins).map(([k, v]) => `${k} ${Math.round(Number(v) * 100)}%`).join(' · ')
                    : 'not measured'}</div>
                </div>
                <div className="small">
                  <div className="muted">Text bands detected</div>
                  <div>{current.geometry?.text_bands?.length ?? 0}</div>
                </div>
                <div className="small">
                  <div className="muted">Logo-like block</div>
                  <div>{current.geometry?.logo_block
                    ? `${current.geometry.logo_block.region} at x ${current.geometry.logo_block.x}, y ${current.geometry.logo_block.y} (${current.geometry.logo_block.hex})`
                    : 'not detected'}</div>
                </div>
                <div className="small">
                  <div className="muted">Template comparison</div>
                  <div>{current.geometry?.template
                    ? `${current.geometry.template.name} · similarity ${current.geometry.template.similarity}`
                    : 'no approved asset matched'}</div>
                </div>
              </div>
              {(current.regions ?? []).length > 0 ? (
                <div className="stack">
                  <div className="muted small">Measured issues</div>
                  {current.regions.map((r: Row, i: number) => (
                    <div key={i} className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                      <SeverityBadge severity={r.severity} />
                      <span className="small">{r.detail}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="ok-msg small">No geometry or palette issue was measured on this creative.</div>}
            </div>
          </Card>

          <div className="stack">
            <Card>
              <CardHead title="Text extracted from the image"
                subtitle={current.ocr_source === 'vision_model'
                  ? 'Transcribed by the vision model — this runtime has no OCR engine, so the transcription is what a vision model read from the pixels'
                  : 'No text could be extracted'} />
              <div style={{ padding: 16 }}>
                {current.ocr_text
                  ? <pre className="excerpt" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{current.ocr_text}</pre>
                  : <div className="muted small">Nothing was read from this image.</div>}
              </div>
            </Card>
            <Card>
              <CardHead title={`Findings from the creative (${current.findings_count ?? 0})`}
                subtitle="The same rule categories used on submitted copy, applied to the extracted text" />
              {Number(current.findings_count ?? 0) === 0 ? (
                <Empty title="No rule breaches in the extracted text"
                  hint="Text that passes the terminology, disclaimer, claim and confidentiality checks is recorded as a pass." />
              ) : (
                <div style={{ padding: 16 }} className="small">
                  {Number(current.findings_count)} finding(s) were recorded against the submission and appear on the
                  review page with the other findings.{' '}
                  {current.review_id ? (
                    <button className="btn-link" onClick={() => navigate({ page: 'review', id: current.review_id })}>
                      Open the review
                    </button>
                  ) : null}
                </div>
              )}
            </Card>
            <Card>
              <CardHead title="What this scan could not determine" />
              <div style={{ padding: 16 }} className="small">
                {(current.limitations ?? []).length === 0 ? <span className="muted">No limitations recorded.</span> : (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {(current.limitations ?? []).map((l: string, i: number) => <li key={i}>{l}</li>)}
                  </ul>
                )}
              </div>
            </Card>
          </div>
        </div>
      ) : (
        <Card pad><Empty title="No visual scan yet" hint="Scan a creative to measure it and read its text." /></Card>
      )}
    </div>
  )
}

function OverlayPreview({ path, name }: { path?: string | null; name: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (!path) return
      try {
        const { lemmaClient } = await import('../lemma-client')
        const res: any = await lemmaClient.files.getUrl(path)
        if (!cancelled) setUrl(res.url ?? res.app_url ?? null)
      } catch { if (!cancelled) setFailed(true) }
    })()
    return () => { cancelled = true }
  }, [path])
  if (!path) return <div className="muted small"><ImageIcon size={13} /> No overlay available for this file.</div>
  if (failed) return <div className="muted small">Overlay written to pod files but the preview link could not be created.</div>
  if (!url) return <div className="skeleton" style={{ height: 220 }} />
  return <img src={url} alt={`Measured regions overlay for ${name}`}
    style={{ maxWidth: '100%', border: '1px solid var(--line)', borderRadius: 8 }} />
}

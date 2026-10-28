import React, { useState } from 'react'
import { FileImage, Layers, RefreshCw, ScanEye, Type } from 'lucide-react'
import { useSession } from '../lib/session'
import { safeId, useSqlQuery } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, useToast } from '../components/ui'
import { fmtDateTime } from '../lib/types'
import type { Route } from '../components/Shell'

/** Improvement 2: multi-format, page-level creative compliance. */
export function Creative({ navigate }: { navigate: (r: Route) => void }) {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id ? safeId(session.activeOrg.id) : null
  const [busy, setBusy] = useState<string | null>(null)
  const [openScan, setOpenScan] = useState<string | null>(null)

  const candidates = useSqlQuery<Row>(orgId ? `
    select r.id, r.title, r.channel, r.status, r.current_version, r.image_paths, r.attachment_paths
    from bg_reviews r
    where r.organization_id = '${orgId}'
      and (r.image_paths is not null or r.attachment_paths is not null)
    order by r.created_at desc limit 40` : null, [orgId])

  const scans = useSqlQuery<Row>(orgId ? `
    select * from bg_creative_scans where organization_id = '${orgId}' order by created_at desc limit 30` : null, [orgId])
  const pages = useSqlQuery<Row>(orgId ? `
    select * from bg_creative_pages where organization_id = '${orgId}' order by page_number asc limit 200` : null, [orgId])

  const run = async (row: Row, filePath?: string) => {
    setBusy(row.id)
    const res = await runFn('bg_creative_scan', { organization_id: orgId, review_id: filePath ? null : row.id, file_path: filePath })
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The creative scan failed.', 'error'); return }
    const out: any = res.output
    toast.push(out.message ?? 'Creative scan complete.', 'success')
    scans.refresh(); pages.refresh()
    if (out.scan_id) setOpenScan(out.scan_id)
  }

  const current = scans.items.find((s) => s.id === openScan) ?? scans.items[0]
  const currentPages = current ? pages.items.filter((p) => p.scan_id === current.id) : []

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Creative compliance</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Images (PNG, JPEG, WebP, TIFF, GIF, BMP) and multi-page PDFs, analysed page by page with a separate
            evidence overlay for each page, word boxes with reading confidence, a dual-read cross-check, measured
            contrast and text size, and logo stretch / recolour / clear-space verification.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { scans.refresh(); pages.refresh() }}><RefreshCw size={14} /> Refresh</Button>
      </div>

      <Card>
        <CardHead title="Creatives attached to submissions" subtitle="Scan one to analyse every page" />
        {candidates.loading ? <Loading /> : candidates.items.length === 0 ? (
          <Empty title="No creatives attached yet" hint="Attach an image or PDF when submitting content and it will appear here." />
        ) : (
          <div className="table-wrap">
            <table aria-label="Creatives">
              <thead><tr><th>Submission</th><th>Files</th><th>Scanned</th><th /></tr></thead>
              <tbody>
                {candidates.items.map((r) => {
                  const images: Row[] = Array.isArray(r.image_paths) ? r.image_paths : []
                  const attachments: string[] = Array.isArray(r.attachment_paths) ? r.attachment_paths : []
                  const done = scans.items.filter((s) => s.review_id === r.id)
                  return (
                    <tr key={r.id}>
                      <td>
                        <button className="btn-link" onClick={() => navigate({ page: 'review', id: r.id })}>{r.title}</button>
                        <div className="muted small">{r.channel ?? 'unspecified channel'} · v{r.current_version}</div>
                      </td>
                      <td className="small">
                        {images.map((i) => <div key={i.path}>{i.name}</div>)}
                        {attachments.map((p) => <div key={p}>{String(p).split('/').pop()}</div>)}
                        {images.length + attachments.length === 0 ? '—' : null}
                      </td>
                      <td className="muted small nowrap">{done.length ? `${done.length} scan(s)` : '—'}</td>
                      <td>
                        <Button size="sm" disabled={busy === r.id} onClick={() => run(r)}>
                          {busy === r.id ? <span className="spinner" aria-hidden="true" /> : <ScanEye size={13} />} Scan pages
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
          <select id="scan-pick" value={current?.id ?? ''} onChange={(e) => setOpenScan(e.target.value)}
            style={{ width: 'auto', minWidth: 360 }}>
            {scans.items.map((s) => (
              <option key={s.id} value={s.id}>
                {s.file_name} — {s.file_kind}, {s.analysed_pages} page(s), score {s.score} ({fmtDateTime(s.created_at)})
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {current ? (
        <>
          <Card pad>
            <div className="row">
              <div>
                <h2>{current.file_name}</h2>
                <div className="muted small">
                  {current.file_kind} · decoder {current.decoder ?? 'not recorded'} · {current.analysed_pages} page(s)
                  · {current.findings_count} finding(s) · engine {current.engine}
                </div>
              </div>
              <div className="spacer" />
              <Badge tone={Number(current.score) >= 80 ? 'b-ok' : Number(current.score) >= 55 ? 'b-medium' : 'b-critical'}>
                {current.score}/100
              </Badge>
              <Badge tone="b-info">{current.ocr_engine}</Badge>
            </div>
            <div className="notice small" style={{ marginTop: 10 }}>{current.ocr_note}</div>
          </Card>

          <div className="stack">
            {currentPages.map((page) => (
              <Card key={page.id}>
                <CardHead title={`Page ${page.page_number}`}
                  subtitle={page.width ? `${page.width}×${page.height} px · ${(page.words ?? []).length} word box(es) · ${(page.regions ?? []).length} measured region(s)`
                    : 'pixel measurements unavailable for this page'}
                  actions={<Badge tone={page.status === 'completed' ? 'b-ok' : 'b-medium'}>{page.status}</Badge>} />
                <div className="split">
                  <div style={{ padding: 14 }} className="stack">
                    {page.overlay_path ? <Overlay path={page.overlay_path} name={`page ${page.page_number}`} /> : (
                      <div className="warn-msg small">
                        No evidence overlay was produced for this page
                        {(page.limitations ?? []).length ? `: ${(page.limitations ?? [])[0]}` : '.'}
                      </div>
                    )}
                    <div className="grid cols-2 small">
                      <div><div className="muted">Palette conformance</div>
                        <div>{page.geometry?.palette_conformance !== undefined && page.geometry?.palette_conformance !== null
                          ? `${Math.round(Number(page.geometry.palette_conformance) * 100)}%`
                          : 'not measured'}</div></div>
                      <div><div className="muted">Margins</div>
                        <div>{page.geometry?.margins
                          ? Object.entries(page.geometry.margins).map(([k, v]) => `${k} ${Math.round(Number(v) * 100)}%`).join(' · ')
                          : 'not measured'}</div></div>
                      <div><div className="muted">Text bands</div>
                        <div>{(page.geometry?.text_bands ?? []).length} detected</div></div>
                      <div><div className="muted">Logo block</div>
                        <div>{page.geometry?.logo_block
                          ? `${page.geometry.logo_block.hex}, aspect ${page.geometry.logo_block.aspect_ratio}, clear space ${page.geometry.logo_block.clear_space_ratio}×`
                          : 'not located'}</div></div>
                    </div>
                    {page.accessibility ? (
                      <div className="small">
                        <div className="muted"><Type size={12} /> Accessibility, measured</div>
                        <div>
                          Contrast: {page.accessibility.contrast_checks} band(s) checked,
                          {' '}{page.accessibility.contrast_failures} below {page.accessibility.minimum_required}:1
                          {page.accessibility.worst_contrast !== null ? ` (worst ${page.accessibility.worst_contrast}:1)` : ''}
                        </div>
                        <div>
                          Text size: {page.accessibility.size_failures} of {page.accessibility.size_checks} band(s) below
                          the {page.accessibility.minimum_text_px} px floor used here
                        </div>
                        <div className="muted">{page.accessibility.measurement_note}</div>
                      </div>
                    ) : null}
                  </div>

                  <div style={{ padding: 14 }} className="stack">
                    <div>
                      <div className="muted small">Text read from this page</div>
                      {page.text
                        ? <pre className="excerpt" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{page.text}</pre>
                        : <div className="muted small">No text was read from this page.</div>}
                    </div>
                    {page.dual_read?.performed ? (
                      <div className="small">
                        <div className="muted">Dual-read cross-check</div>
                        <div className="row" style={{ gap: 6 }}>
                          <Badge tone={(page.dual_read.text_agreement ?? 0) >= 0.8 ? 'b-ok' : 'b-medium'}>
                            text agreement {page.dual_read.text_agreement ?? '—'}
                          </Badge>
                          <Badge tone="b-info">mean word confidence {page.dual_read.mean_word_confidence ?? '—'}</Badge>
                        </div>
                        <div className="muted">{page.dual_read.note}</div>
                        {(page.dual_read.low_confidence_words ?? []).length ? (
                          <div className="muted">Low-confidence words: {(page.dual_read.low_confidence_words ?? []).join(', ')}</div>
                        ) : null}
                        {(page.dual_read.disagreements ?? []).map((d: string, i: number) => (
                          <div key={i} className="warn-msg small">{d}</div>
                        ))}
                      </div>
                    ) : <div className="muted small">No dual read for this page.</div>}
                    {(page.logo_checks ?? []).length ? (
                      <div className="small">
                        <div className="muted">Logo verification</div>
                        {(page.logo_checks ?? []).map((c: Row, i: number) => (
                          <div key={i} className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                            <Badge tone={c.ok ? 'b-ok' : 'b-high'}>{c.ok ? 'pass' : 'fail'}</Badge>
                            <span>{c.check}<div className="muted">{c.evidence}</div></span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {(page.regions ?? []).length ? (
                      <div className="stack small">
                        <div className="muted">Measured issues</div>
                        {(page.regions ?? []).map((r: Row, i: number) => (
                          <div key={i} className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                            <SeverityBadge severity={r.severity} />
                            <span>{r.detail}</span>
                          </div>
                        ))}
                      </div>
                    ) : <div className="ok-msg small">No geometry, palette, accessibility or logo issue measured on this page.</div>}
                    {(page.limitations ?? []).length ? (
                      <details>
                        <summary className="small">What this page's scan could not determine</summary>
                        <ul className="small">{(page.limitations ?? []).map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
                      </details>
                    ) : null}
                  </div>
                </div>
              </Card>
            ))}
          </div>

          {(current.limitations ?? []).length ? (
            <Card>
              <CardHead title="Scan limitations" subtitle="Recorded rather than glossed over" />
              <ul className="small" style={{ padding: '12px 16px 12px 34px' }}>
                {(current.limitations ?? []).map((l: string, i: number) => <li key={i}>{l}</li>)}
              </ul>
            </Card>
          ) : null}
        </>
      ) : (
        <Card pad><Empty title="No creative scan yet" hint="Scan an attached creative to analyse it page by page." /></Card>
      )}
    </div>
  )
}

function Overlay({ path, name }: { path: string; name: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const { lemmaClient } = await import('../lemma-client')
        const res: any = await lemmaClient.files.getUrl(path)
        if (!cancelled) setUrl(res.url ?? res.app_url ?? null)
      } catch { if (!cancelled) setFailed(true) }
    })()
    return () => { cancelled = true }
  }, [path])
  if (failed) return <div className="muted small"><FileImage size={13} /> Overlay written to pod files; preview link unavailable.</div>
  if (!url) return <div className="skeleton" style={{ height: 200 }} />
  return (
    <figure style={{ margin: 0 }}>
      <img src={url} alt={`Evidence overlay for ${name}`} style={{ maxWidth: '100%', border: '1px solid var(--line)', borderRadius: 8 }} />
      <figcaption className="muted small">
        <Layers size={12} /> green = content bounds · blue = text band passing contrast · red = failing contrast or off-palette cell · yellow = logo block
      </figcaption>
    </figure>
  )
}

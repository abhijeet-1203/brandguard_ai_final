import React, { useState } from 'react'
import { ArrowRight, FileUp, Image as ImageIcon, Sparkles, Trash2, X } from 'lucide-react'
import { useSession } from '../lib/session'
import { computeImageMeta, runFn, uploadFile, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Field, useToast } from '../components/ui'
import { CONTENT_TYPES } from '../lib/types'
import type { Route } from '../components/Shell'

type Attached = { path: string; name: string; size: number; kind: 'document' | 'image' }

export function NewReview({ navigate }: { navigate: (r: Route) => void }) {
  const { activeOrg, email, can } = useSession()
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [contentType, setContentType] = useState('social_post')
  const [campaign, setCampaign] = useState('')
  const [audience, setAudience] = useState('')
  const [channel, setChannel] = useState('')
  const [language, setLanguage] = useState('en')
  const [notes, setNotes] = useState('')
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Attached[]>([])
  const [images, setImages] = useState<Row[]>([])
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('')
  const [error, setError] = useState<string | null>(null)

  if (!can('createContent')) {
    return <Card pad><div className="err">You do not have permission to submit content in this workspace.</div></Card>
  }

  const orgSlug = activeOrg?.slug ?? 'workspace'

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    setError(null)
    try {
      for (const file of Array.from(files)) {
        setStage(`Uploading ${file.name}…`)
        if (file.size > 15 * 1024 * 1024) {
          setError(`${file.name} is larger than 15 MB. Upload a smaller file or paste the text.`)
          continue
        }
        const uploaded = await uploadFile(file, `/brandguard/${orgSlug}/reviews`)
        const path = uploaded.path ?? uploaded.file_path ?? ''
        const isImage = /^image\//.test(file.type)
        if (isImage) {
          const meta = await computeImageMeta(file).catch(() => null)
          setImages((prev) => [...prev, {
            path, name: file.name, width: meta?.width ?? null, height: meta?.height ?? null,
            ahash: meta?.ahash ?? null, colors: meta?.colors ?? [],
          }])
          toast.push(`${file.name} attached. Its perceptual hash was computed in your browser for the visual check.`, 'success')
        } else {
          setAttachments((prev) => [...prev, { path, name: file.name, size: file.size, kind: 'document' }])
          toast.push(`${file.name} attached. Text is extracted server-side when you submit.`, 'success')
        }
      }
    } catch (e: any) {
      setError(e?.message ?? 'The upload failed.')
    } finally {
      setBusy(false)
      setStage('')
    }
  }

  const submit = async (asDraft: boolean) => {
    setError(null)
    if (!title.trim()) { setError('Give the submission a title so reviewers can find it.'); return }
    if (!text.trim() && attachments.length === 0) {
      setError('Paste the content or attach a document. A review needs something to analyse.'); return
    }
    setBusy(true)
    setStage('Saving the submission…')
    const res = await runFn('bg_review_create', {
      organization_id: activeOrg?.id, title, content_type: contentType, campaign, audience, channel, language,
      context_notes: notes, content_text: text,
      attachments: attachments.map((a) => ({ path: a.path, name: a.name, kind: 'document' })),
      images, submit: !asDraft,
    })
    if (!res.ok) {
      setBusy(false); setStage(''); setError(res.error ?? 'The submission could not be saved.'); return
    }
    const out: any = res.output
    toast.push(out.message ?? 'Submission saved.', 'success')
    if (!asDraft) {
      setStage('Running the compliance analysis… this usually takes under a minute.')
      const analysis = await runFn('bg_review_analyse', { review_id: out.review_id })
      if (!analysis.ok) {
        toast.push(analysis.error ?? 'The analysis failed. The content was saved and can be retried.', 'error')
      } else {
        toast.push('Analysis complete. Findings are ready to inspect.', 'success')
      }
    }
    setBusy(false); setStage('')
    navigate({ page: 'review', id: out.review_id })
  }

  return (
    <div className="stack">
      <div>
        <h1>New review</h1>
        <p className="muted small" style={{ margin: 0 }}>
          Submit content for a compliance review. The original submission is preserved and never overwritten.
        </p>
      </div>

      {error ? <div className="err" role="alert">{error}</div> : null}

      <div className="split">
        <Card>
          <CardHead title="What are you submitting?" />
          <div style={{ padding: 16 }}>
            <Field label="Content title" required>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. AirPure 300 launch — Instagram post" />
            </Field>
            <div className="grid cols-2">
              <Field label="Content type" required>
                <select value={contentType} onChange={(e) => setContentType(e.target.value)}>
                  {CONTENT_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </Field>
              <Field label="Campaign or project">
                <input value={campaign} onChange={(e) => setCampaign(e.target.value)} placeholder="e.g. AirPure 300 launch" />
              </Field>
              <Field label="Intended audience">
                <input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder="e.g. Home owners" />
              </Field>
              <Field label="Publication channel">
                <input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="e.g. Instagram, email, print" />
              </Field>
              <Field label="Language">
                <input value={language} onChange={(e) => setLanguage(e.target.value)} />
              </Field>
            </div>
            <Field label="Context or reviewer instructions"
              help="Reviewers see this. It is treated as untrusted data by the analysis engine.">
              <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3}
                placeholder="Anything the reviewer should know…" />
            </Field>

            <Field label="Content" help="Paste the copy exactly as it will be published.">
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12}
                placeholder="Paste the content here…" />
            </Field>

            <div className="row">
              <label className="btn btn-sm" style={{ cursor: 'pointer' }}>
                <FileUp size={14} /> Attach documents or images
                <input type="file" multiple accept=".pdf,.docx,.doc,.txt,.md,.csv,.png,.jpg,.jpeg,.webp,.svg"
                  style={{ display: 'none' }} onChange={(e) => handleFiles(e.target.files)} disabled={busy} />
              </label>
              <span className="muted small">PDF, DOCX, TXT, MD, CSV, PNG, JPEG, WebP, SVG · up to 15 MB each</span>
            </div>

            {attachments.length > 0 || images.length > 0 ? (
              <div style={{ marginTop: 12 }} className="stack">
                {attachments.map((a) => (
                  <div key={a.path} className="row card-pad" style={{ border: '1px solid var(--line)', borderRadius: 8 }}>
                    <Badge tone="b-info">document</Badge>
                    <span className="small">{a.name}</span>
                    <span className="muted small">{Math.round(a.size / 1024)} KB</span>
                    <div className="spacer" />
                    <button className="btn-link" onClick={() => setAttachments((p) => p.filter((x) => x.path !== a.path))}>
                      <Trash2 size={13} /> remove
                    </button>
                  </div>
                ))}
                {images.map((i) => (
                  <div key={i.path} className="row card-pad" style={{ border: '1px solid var(--line)', borderRadius: 8 }}>
                    <Badge tone="b-accent"><ImageIcon size={11} /> image</Badge>
                    <span className="small">{i.name}</span>
                    <span className="muted small">{i.width}×{i.height} · hash {String(i.ahash).slice(0, 8)}…</span>
                    <div className="spacer" />
                    <button className="btn-link" onClick={() => setImages((p) => p.filter((x) => x.path !== i.path))}>
                      <Trash2 size={13} /> remove
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </Card>

        <div className="stack">
          <Card>
            <CardHead title="What happens next" />
            <div style={{ padding: '14px 16px' }} className="stack small">
              <div className="row"><Sparkles size={14} /><span>The engine runs exact rule checks (terminology, numbers, disclaimers, confidentiality, formatting, accessibility).</span></div>
              <div className="row"><Sparkles size={14} /><span>An AI pass reviews tone, messaging and claims against your <b>approved</b> guidelines and facts only.</span></div>
              <div className="row"><Sparkles size={14} /><span>Findings are stored with their evidence status and source reference, or marked unverified.</span></div>
              <div className="row"><ArrowRight size={14} /><span>Low-risk content waits for a normal review; high-risk findings route to the right reviewer.</span></div>
              <div className="row"><X size={14} /><span>Nothing is published automatically — approval is always a human decision.</span></div>
            </div>
          </Card>
          <Card>
            <CardHead title="Analysis uses this workspace's sources" />
            <div style={{ padding: '14px 16px' }} className="small">
              <div>Workspace: <b>{activeOrg?.name}</b></div>
              <div className="muted">Submitted by {email}</div>
              <div className="muted" style={{ marginTop: 6 }}>
                Only this workspace's active guidelines, approved facts and approved assets are supplied to the model.
                Content from other workspaces is never retrieved.
              </div>
            </div>
          </Card>
          <Card pad>
            <div className="row">
              <Button variant="primary" disabled={busy} onClick={() => submit(false)}>
                {busy ? <span className="spinner" aria-hidden="true" /> : null} Submit and analyse
              </Button>
              <Button disabled={busy} onClick={() => submit(true)}>Save as draft</Button>
            </div>
            {stage ? <div className="muted small" style={{ marginTop: 8 }}>{stage}</div> : null}
          </Card>
        </div>
      </div>
    </div>
  )
}

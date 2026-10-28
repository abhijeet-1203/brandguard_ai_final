import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { AlertTriangle, Check, Info, X } from 'lucide-react'
import { severityTone, statusTone } from '../lib/types'

export function Card({ children, className = '', pad = false }: { children: React.ReactNode; className?: string; pad?: boolean }) {
  return <section className={`card ${pad ? 'card-pad' : ''} ${className}`}>{children}</section>
}

export function CardHead({ title, subtitle, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="card-head">
      <div style={{ flex: 1, minWidth: 0 }}>
        <h2>{title}</h2>
        {subtitle ? <div className="muted small">{subtitle}</div> : null}
      </div>
      {actions}
    </div>
  )
}

export function Badge({ tone = 'b-info', children, title }: { tone?: string; children: React.ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      <span className="dot" aria-hidden="true" />
      {children}
    </span>
  )
}

export function SeverityBadge({ severity }: { severity?: string }) {
  return <Badge tone={severityTone(severity)}>{severity ?? 'unknown'}</Badge>
}

export function StatusBadge({ status, label }: { status?: string; label?: string }) {
  return <Badge tone={statusTone(status)}>{label ?? (status ?? '').replace(/_/g, ' ')}</Badge>
}

export function Button({
  children, onClick, variant = 'default', size, disabled, type = 'button', title,
}: {
  children: React.ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'danger' | 'ghost'
  size?: 'sm'
  disabled?: boolean
  type?: 'button' | 'submit'
  title?: string
}) {
  const cls = ['btn', variant === 'primary' ? 'btn-primary' : '', variant === 'danger' ? 'btn-danger' : '',
    variant === 'ghost' ? 'btn-ghost' : '', size === 'sm' ? 'btn-sm' : ''].join(' ')
  return (
    <button className={cls} onClick={onClick} disabled={disabled} type={type} title={title}>
      {children}
    </button>
  )
}

export function Field({ label, help, children, required }: { label: string; help?: string; children: React.ReactNode; required?: boolean }) {
  return (
    <div className="field">
      <label>
        {label} {required ? <span aria-hidden="true">*</span> : null}
      </label>
      {children}
      {help ? <span className="help">{help}</span> : null}
    </div>
  )
}

export function Empty({ title, hint, action }: { title: string; hint?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {hint ? <p>{hint}</p> : null}
      {action}
    </div>
  )
}

export function Loading({ rows = 3, label }: { rows?: number; label?: string }) {
  return (
    <div style={{ padding: 16 }} role="status" aria-live="polite">
      <span className="muted small" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span className="spinner" aria-hidden="true" /> {label ?? 'Loading…'}
      </span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ marginBottom: 8, width: `${90 - i * 12}%` }} />
      ))}
    </div>
  )
}

export function Metric({ label, value, note }: { label: string; value: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {note ? <div className="metric-note">{note}</div> : null}
    </div>
  )
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2 style={{ flex: 1 }}>{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose} title="Close dialog"><X size={16} /></Button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  )
}

type Toast = { id: number; text: string; tone: 'info' | 'error' | 'success' }
const ToastContext = createContext<{ push: (text: string, tone?: Toast['tone']) => void }>({ push: () => {} })

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((text: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === 'error' ? 9000 : 5000)
  }, [])
  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="toast-wrap" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`} role="status">
            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'flex-start' }}>
              {t.tone === 'error' ? <AlertTriangle size={15} /> : t.tone === 'success' ? <Check size={15} /> : <Info size={15} />}
              <span>{t.text}</span>
            </span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return <div className="err" role="alert">{children}</div>
}

export function TableWrap({ head, children, label }: { head: React.ReactNode; children: React.ReactNode; label: string }) {
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <thead>{head}</thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Bar({ value, max, tone = 'var(--accent)' }: { value: number; max: number; tone?: string }) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0
  return (
    <div style={{ background: 'var(--surface-2)', borderRadius: 4, height: 8, overflow: 'hidden', minWidth: 60 }}>
      <div style={{ width: `${width}%`, background: tone, height: '100%' }} />
    </div>
  )
}

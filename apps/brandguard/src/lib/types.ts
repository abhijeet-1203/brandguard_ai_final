export type Row = Record<string, any>

export const ROLES = ['admin', 'brand_manager', 'reviewer', 'compliance_reviewer', 'creator'] as const
export type Role = (typeof ROLES)[number]

export const ROLE_LABEL: Record<string, string> = {
  admin: 'Administrator',
  brand_manager: 'Brand manager',
  reviewer: 'Reviewer',
  compliance_reviewer: 'Compliance reviewer',
  creator: 'Content creator',
}

export const ROLE_RANK: Record<string, number> = {
  admin: 5, brand_manager: 4, compliance_reviewer: 3, reviewer: 2, creator: 1,
}

export const CAPABILITIES = {
  authorGuidelines: ['admin', 'brand_manager'],
  approveGuideline: ['admin', 'brand_manager'],
  manageFacts: ['admin', 'brand_manager', 'compliance_reviewer'],
  approveAsset: ['admin', 'brand_manager'],
  createContent: ['admin', 'brand_manager', 'reviewer', 'compliance_reviewer', 'creator'],
  decideReview: ['admin', 'brand_manager', 'reviewer', 'compliance_reviewer'],
  complianceDecision: ['admin', 'compliance_reviewer'],
  manageTeam: ['admin'],
  managePolicy: ['admin'],
  actOnInsight: ['admin', 'brand_manager'],
} as const

export type Capability = keyof typeof CAPABILITIES

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'informational']

export const SEVERITY_LABEL: Record<string, string> = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', informational: 'Informational',
}

export const CATEGORY_LABEL: Record<string, string> = {
  tone: 'Tone', terminology: 'Terminology', messaging: 'Messaging', factual_accuracy: 'Factual accuracy',
  promotional_claim: 'Promotional claim', visual: 'Visual identity', accessibility: 'Accessibility',
  confidentiality: 'Confidentiality', formatting: 'Formatting',
}

export const EVIDENCE_LABEL: Record<string, string> = {
  supported: 'Supported by a source', contradicted: 'Contradicted by a source',
  not_found: 'No approved evidence found', expired: 'Source expired', ambiguous: 'Ambiguous — needs judgement',
  stylistic: 'Stylistic guidance', unverified: 'Could not be verified',
}

export const STATUS_LABEL: Record<string, string> = {
  draft: 'Draft', submitted: 'Submitted', analysing: 'Analysing', analysis_failed: 'Analysis failed',
  awaiting_review: 'Awaiting review', changes_requested: 'Changes requested', resubmitted: 'Resubmitted',
  approved: 'Approved', rejected: 'Rejected', archived: 'Archived',
}

export const FINDING_STATUS_LABEL: Record<string, string> = {
  open: 'Open', accepted: 'Accepted', rejected: 'Rejected', resolved: 'Resolved', escalated: 'Escalated',
  wont_fix: 'Will not fix',
}

export const CONTENT_TYPES: { value: string; label: string }[] = [
  { value: 'social_post', label: 'Social media post' },
  { value: 'marketing_email', label: 'Marketing email' },
  { value: 'advertisement', label: 'Advertisement' },
  { value: 'blog_post', label: 'Blog post' },
  { value: 'product_description', label: 'Product description' },
  { value: 'landing_page', label: 'Landing page copy' },
  { value: 'promotional_banner', label: 'Promotional banner' },
  { value: 'product_brochure', label: 'Product brochure' },
  { value: 'marketing_document', label: 'Marketing document' },
  { value: 'other', label: 'Other' },
]

export const GUIDELINE_CATEGORIES = [
  'tone', 'terminology', 'messaging', 'factual_accuracy', 'promotional_claim', 'visual_identity',
  'accessibility', 'confidentiality', 'legal_compliance', 'formatting',
]

export const RULE_TYPES = [
  'prohibited_term', 'required_term', 'deprecated_term', 'tone_rule', 'messaging_rule', 'numeric_fact',
  'required_disclaimer', 'claim_policy', 'confidentiality_rule', 'visual_rule', 'accessibility_rule',
  'formatting_rule',
]

export const FACT_CATEGORIES = [
  'product_specification', 'price', 'warranty', 'discount', 'availability', 'company_description',
  'marketing_statement', 'certification', 'promotional_claim', 'disclaimer', 'other',
]

export const ASSET_CATEGORIES = [
  'logo', 'logo_variant', 'image', 'campaign_image', 'template', 'font', 'brand_manual', 'other',
]

export const ENFORCEMENT_LABEL: Record<string, string> = {
  mandatory: 'Mandatory', recommended: 'Recommended', advisory: 'Advisory',
}

export const INSIGHT_LABEL: Record<string, string> = {
  recurring_terminology: 'Recurring terminology issue', recurring_claim: 'Repeated unsupported claims',
  guideline_ambiguity: 'Likely guideline ambiguity', asset_misuse: 'Visual asset misuse',
  high_revision_rate: 'High revision rate', reviewer_disagreement: 'Reviewer disagreement',
  rejected_corrections: 'Rejected corrections', insufficient_data: 'Insufficient data',
}

export function statusTone(status?: string): string {
  switch (status) {
    case 'approved': return 'b-ok'
    case 'rejected': return 'b-critical'
    case 'analysis_failed': return 'b-critical'
    case 'changes_requested': return 'b-high'
    case 'awaiting_review': case 'resubmitted': return 'b-accent'
    case 'analysing': case 'submitted': return 'b-medium'
    case 'archived': return 'b-info'
    default: return 'b-info'
  }
}

export function severityTone(sev?: string): string {
  switch (sev) {
    case 'critical': return 'b-critical'
    case 'high': return 'b-high'
    case 'medium': return 'b-medium'
    case 'low': return 'b-low'
    default: return 'b-info'
  }
}

export function fmtDate(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (isNaN(d.getTime())) return String(value)
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function fmtDateTime(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (isNaN(d.getTime())) return String(value)
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function relTime(value?: string | null): string {
  if (!value) return '—'
  const then = new Date(value).getTime()
  if (isNaN(then)) return '—'
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 31) return `${days}d ago`
  return fmtDate(value)
}

export function titleCase(value?: string | null): string {
  if (!value) return ''
  return String(value).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function pct(part: number, total: number): string {
  if (!total) return '—'
  return `${Math.round((part / total) * 100)}%`
}

export function confidenceLabel(c?: number | null): string {
  if (c === null || c === undefined) return 'not stated'
  return `${Math.round(Number(c) * 100)}% estimate`
}

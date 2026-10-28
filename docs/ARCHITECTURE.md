# BrandGuard AI — architecture

## 1. Where the product runs

BrandGuard AI is a **pod app**: a static React bundle served by the platform, with the pod
itself as the backend. There is no separate application server, no in-memory state, and no
second database. That is deliberate — it means authorisation, audit and data residency are
handled by the same runtime for the UI and the automation.

```
┌──────────────────────────────┐        ┌──────────────────────────────────────────┐
│ Browser: React + TS SPA      │        │ Pod runtime                              │
│  • reads: datastore SQL,     │  auth  │  • 22 tables (workspace-scoped rows)      │
│    records API               │ ─────► │  • 18 functions (all mutations + analysis)│
│  • writes: pod functions     │        │  • 3 agents (LLM, schema-constrained)     │
│  • live: change WebSocket    │        │  • pod files: /brandguard/...             │
└──────────────────────────────┘        └──────────────────────────────────────────┘
```

The client never holds credentials or model keys. Every privileged action is a function
call, and each function re-derives the caller's membership and role before doing anything.

## 2. Backend functions

| Function | Type | Responsibility |
| --- | --- | --- |
| `bg_bootstrap` | API | Idempotent session bootstrap: membership, default policy, provider disclosure records |
| `bg_workspace_action` | API | Create/update/archive a workspace and its brand profile (admin only) |
| `bg_team_action` | API | Invite, re-role, suspend, remove members; guards the last administrator (admin only) |
| `bg_policy_update` | API | Approval, AI-flow and retention policy (admin only) |
| `bg_guideline_action` | API | Create/edit/approve/archive rules with version snapshots |
| `bg_guideline_extract` | JOB | Propose draft rules from an uploaded brand document |
| `bg_fact_action` | API | Approved facts, verification states and evidence records |
| `bg_asset_action` | API | Register, approve, replace, archive assets with hashes and colours |
| `bg_document_ingest` | API | Register a pod file and extract its text (converted markdown) |
| `bg_review_create` | API | Validate, extract attachments, store version 1 and its segments |
| `bg_review_analyse` | JOB | The compliance engine (see §3) |
| `bg_correction_action` | JOB | Accept/reject/edit/regenerate/apply/restore corrections |
| `bg_review_workflow` | API | State machine, approvals, overrides, escalation, audit |
| `bg_comment_action` | API | Comments, replies, mentions, thread resolution |
| `bg_insights_generate` | API | Governance memory: recurring problems and guideline gaps |
| `bg_eval_run` | JOB | Live reliability harness used by the Quality page |
| `bg_campaign_scan` | API | Cross-channel consistency: signal extraction per review, conflict detection, weighted score |
| `bg_provenance_scan` | API | Claim → document/passage/owner/expiry tracing, validity re-evaluation, alerts, graph |
| `bg_visual_scan` | JOB | PNG decode and measurement, overlay generation, vision transcription, text checks |
| `bg_risk_score` | API | Explainable risk factors, band, per-review scoring, outcome recording |
| `bg_root_cause` | JOB | Grouping and cause classification, proposed rule generation, draft creation |
| `bg_minimal_correction` | JOB | Minimal-edit proposal, guard rails, before/after compliance report, apply |

## 3. The compliance engine (`bg_review_analyse`)

**Stage 1 — ingestion.** The review's current text, its segments and any attached document
text (page-marked by the platform's converter) are loaded. An empty submission is refused
before anything else happens.

**Stage 2 — reference retrieval.** Only this workspace's *active* guidelines, *approved*
facts (with their expiration dates) and registered assets are retrieved. Nothing from
another workspace is ever queried.

**Stage 3 — deterministic checks.**
- prohibited / deprecated / required terminology (exact, word-boundary matching)
- mandatory disclaimers, gated by an `applies_when` condition so a disclaimer is only
  demanded when it is relevant
- numeric comparison against approved facts, **unit-aware**: only units the engine can read
  unambiguously (months, currency, percent, m², year, kg, cm, mm, hours, days, litres) are
  compared; anything else is reported as *not machine-comparable* instead of guessed at.
  When the sentence does not name the fact's subject, the finding is `ambiguous`, not
  `contradicted`
- expired approved facts → `expired` (never treated as current truth)
- promotional claims (guarantees, superlatives, quantified benefits, comparative, health,
  environmental, certification, financial) checked against approved evidence; absent
  evidence is `not_found`, never a contradiction
- confidentiality patterns (internal-only, embargo, unreleased, cost/margin, identifiers)
- formatting (capital runs, exclamation stacking)
- HTML accessibility (missing/empty alt text, vague link text, skipped heading levels)
- visual checks: perceptual-hash comparison against approved/archived assets and dominant
  colour distance against the workspace palette

**Stage 4 — AI analysis.** The agent receives the content (delimited and labelled
untrusted), the applicable rules with their codes and enforcement levels, the approved
facts with ids and scopes, the asset list, and the limitations already recorded by the
deterministic stage. It must return the schema-declared JSON.

**Stage 5 — validation.** Every returned finding is checked: allowed category, allowed
severity, allowed evidence status, confidence clamped to 0–1, cited rule code present in
the supplied set, cited fact id present in the supplied set, and the quoted span actually
present in the content. Anything that fails is dropped or repaired and the reason is
recorded as a run limitation. A malformed reply is retried once, then reported honestly as
"no AI findings were used" — the run is never presented as a clean result.

**Stage 6 — reconciliation.** Deterministic and AI findings are merged on a fingerprint
(category + normalised quoted text + rule code); the higher severity wins and a correction
from either source is preserved. On re-analysis, findings that are no longer detected are
marked **resolved with the version that confirmed it**, still-open ones are updated in
place, and re-detected ones are reopened.

**Stage 7 — routing.** Risk level is derived from the worst open severity. Critical
claims route to the compliance reviewer when policy requires it; an eligible reviewer who
is not the creator is assigned and notified.

**Stage 8 — persistence.** Findings, the run record (model, analysis version, statistics,
sources used, limitations) and an audit entry are written. A failure writes
`analysis_failed` with the error — never a completed review.

## 3b. The six added engines

**Cross-channel consistency (`bg_campaign_scan`).** Each submitted piece of a campaign is
reduced to comparable signals — prices, warranty terms, discounts, product-name candidates,
slogans, availability dates, claim wording. Product names are matched against the approved
vocabulary with fuzzy comparison, so a near-miss spelling (`PureAir Pro` vs
`Northstar AirPure 300`) is caught rather than treated as a different product. Signals that
carry two or more different values across two or more channels become conflicts; the score is
100 minus the weighted penalty of each conflict, and the penalty list is returned so the
number is explainable.

**Claim provenance (`bg_provenance_scan`).** One link per approved fact and one per finding
that cited a fact, each carrying the document, passage, owner, evidence strength and expiry.
Support is re-evaluated every run: expired, expiring within 60 days, unsupported (no
evidence or rejected), superseded, orphaned (source document gone, or no evidence and no
source location), otherwise valid. Alerts are written to the workspace's administrators at
most once a week per claim, and the graph is returned as nodes and edges.

**Visual compliance (`bg_visual_scan`).** Two independent layers. *Pixels*: a stdlib PNG
decoder (8-bit greyscale/RGB/RGBA, non-interlaced) yields the palette, content bounding box,
safe margins, text bands, the largest saturated block in the left third, and the closest
approved asset by perceptual hash; those measurements are drawn back onto the image and
uploaded as an overlay. *Text*: the vision agent transcribes every visible word, and that
transcription is run through the terminology, disclaimer, claim and confidentiality checks
before any finding is written. Every check that could not be performed is recorded as a
limitation on the scan row.

**Risk triage (`bg_risk_score`).** Factors are computed from stored records — worst open
finding severity, volume of serious findings, claim uncertainty, expired or expiring
evidence, sensitive claim categories, public audience/channel, exposed content type, the
creator's recent outcome history, prior revisions and policy blocking risk. The score is the
sum of the factor points, the band follows fixed thresholds, and the explanation names the
largest contributors. A separate action records the actual outcome of each scored review so
the bands can be evaluated against decisions rather than asserted.

**Root cause (`bg_root_cause`).** Findings are grouped by rule, campaign, content type and
author. A group qualifies when it has at least three cases across two or more reviews, and the
cause is classified from the evidence: expired source → outdated source; ≥40% ambiguous or
unverified → rule clarity; one campaign only → campaign spike; one content type → training
gap; one author → coaching; three or more reviews otherwise → process gap. For the causes a
rule change can address, the extractor agent is given the failures and proposes one revised
rule, which is stored as a proposal with its supporting cases and quotes.

**Minimal correction (`bg_minimal_correction`).** The editor agent returns one edit per
finding. Spans are located in the original text, overlapping edits are dropped, and the
remaining ones are applied from the end so offsets stay valid. Three guards run per edit: no
new claim wording, no newly introduced prohibited term, and no rewrite beyond 1.8× the
original length. The proposed text is then re-checked deterministically and the result is
stored as a before/after report (resolved / still present / introduced) before the reviewer
decides whether to apply it.

## 4. Authorisation model

Two ledgers apply to every call: the platform's grants on the function, and the caller's
own workspace role.

1. The **app** renders according to the caller's role (UX only).
2. Every **mutation** goes through a function that resolves `bg_memberships` for
   `(workspace, caller email)` and refuses the action unless the role allows it.
3. **Ownership** is re-checked on the record itself: a review, guideline, fact or asset
   whose `organization_id` differs from the requested workspace is refused.
4. **Policy guards** are evaluated at decision time, not at render time: separation of
   duties, mandatory-finding blocking (with documented override), compliance-reviewer
   requirement for critical findings, second-reviewer rule, and the rule that the analysed
   version must match the current version.

Role capabilities:

| Capability | admin | brand manager | compliance reviewer | reviewer | creator |
| --- | :-: | :-: | :-: | :-: | :-: |
| Author / approve guidelines | ✓ | ✓ | | | |
| Maintain approved facts | ✓ | ✓ | ✓ | | |
| Approve assets | ✓ | ✓ | | | |
| Submit content | ✓ | ✓ | ✓ | ✓ | ✓ |
| Decide reviews | ✓ | ✓ | ✓ | ✓ | |
| Approve critical findings | ✓ | | ✓ | | |
| Manage team / policy / workspace | ✓ | | | | |

## 4b. Added tables (2026-10-23)

`bg_campaign_scans`, `bg_claim_provenance`, `bg_visual_scans`, `bg_risk_scores`,
`bg_policy_suggestions`, `bg_edit_reports`, plus `risk_score` / `risk_band` / `risk_factors`
columns on `bg_reviews`. Existing tables were not modified.

## 5. Frontend structure

```
src/
  App.tsx              route table, AuthGuard, error boundary
  components/Shell.tsx sidebar, topbar, workspace switcher, notifications
  components/ui.tsx    design-system primitives (card, badge, table, modal, toast…)
  lib/api.ts           SDK wrappers: SQL, records, function runs, uploads, image hashing
  lib/session.tsx      one bootstrap per page load, memberships, active workspace, role
  lib/hooks.ts         SQL/row hooks with explicit refresh, bounded analysis watcher
  pages/               19 screens (dashboard, reviews, review detail, new review, guidelines,
                       facts, assets, approvals, analytics, insights, audit, team, settings,
                       quality, campaigns, risk, visual, provenance, policy)
```

Data discipline: reads happen once per dependency change through the hooks; the review
queue also subscribes to the datastore change stream so it stays fresh without polling. The
only polling is a bounded watcher (3 s interval, 4 min ceiling) on a *single* review while
its analysis is running — a deliberate exception to the no-polling rule, documented here.

## 6. Files

- `/brandguard/<workspace>/reviews|guidelines|assets` — uploads, converted to text by the
  platform when they are documents.
- `/brandguard/assets/*.png` — the seeded approved assets, generated deterministically by
  `bg_seed_workspace` with their perceptual hashes computed from the same pixels.
- `/brandguard/<workspace>/reports` — exported analytics reports.

Uploaded files are workspace-prefixed, validated by extension and size, and paths are
rejected if they are relative or contain traversal segments.

## Round 5 (2026-10-28)

Nine functions were added without changing any existing one.

| Function | Type | Responsibility |
| --- | --- | --- |
| `bg_eval_seed` | API | The labelled evaluation workspace, its 13 rules and 7 approved facts, and the 83 hand-labelled cases |
| `bg_eval_accuracy` | JOB | Runs the cases through the real engine and scores them; resumable in batches; `score_stored` scores already-analysed cases instantly |
| `bg_risk_calibration` | API | Observed rework per predicted band, calibration gap, monotonicity, Brier score, insufficient-data states |
| `bg_reviewer_agreement` | API | Pairwise agreement and Cohen's kappa per rule, per-reviewer strictness index |
| `bg_creative_scan` | JOB | Multi-format decode, page-level geometry and overlays, vision transcription with word boxes, dual-read cross-check, contrast and text-size measurement, logo stretch/recolour/clear-space checks |
| `bg_approval_chain` | API | Chain configuration by content type and resolution of the steps that apply to a review |
| `bg_fact_lifecycle` | API | Expiry watch, owner notification, re-verification tasks, dependency tracking for pending reviews |
| `bg_publish_action` | API | The four-gate handoff: approval, chain, mandatory findings, pinned hash; draft-only connector push; signed package fallback; publishing audit |
| `bg_probe_runtime` | API | Runtime capability diagnostic (which image, OCR and PDF libraries actually exist) |

`bg_creative_scan` decodes PNG natively and everything else through ImageMagick's `convert`,
reading a P6 PPM stream, and reads PDF pages from the platform's converted page JPEGs via
`pod.files.download_child(<full child path>)`. Text extraction is delegated to
`brandguard-creative-inspector`, which returns word boxes, per-word confidence and a second
independent read; the two reads are compared and the agreement is stored on the page record.

`bg_publish_action` never publishes. It resolves the chain, verifies that the current content
hash still matches the hash pinned at approval, and either pushes a **draft** through an
authorised connector or writes a signed package containing the pinned hash, the approval trail
and a SHA-256 integrity seal. Every attempt is written to `bg_publish_events` and the audit log.

Eleven tables were added: `bg_eval_cases`, `bg_eval_runs`, `bg_eval_results`,
`bg_calibration_runs`, `bg_agreement_scores`, `bg_creative_scans`, `bg_creative_pages`,
`bg_approval_chains`, `bg_fact_tasks`, `bg_publish_events`, `bg_publish_packages`, plus seven
columns on `bg_reviews` (`approval_chain_id`, `content_hash`, `approved_hash`, `publish_status`,
`publish_target`, `published_at`, `publish_blocked_reason`).

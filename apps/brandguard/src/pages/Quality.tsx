import React, { useState } from 'react'
import { Activity, AlertTriangle, FlaskConical, Gauge, PlayCircle, RefreshCw, Users } from 'lucide-react'
import { useSession } from '../lib/session'
import { useRows } from '../lib/hooks'
import { runFn, Row } from '../lib/api'
import { Badge, Button, Card, CardHead, Empty, Loading, SeverityBadge, useToast } from '../components/ui'
import { CATEGORY_LABEL, fmtDateTime, pct } from '../lib/types'

/** Improvement 1: measured accuracy, calibrated risk and reviewer agreement. */
export function Quality() {
  const session = useSession()
  const toast = useToast()
  const orgId = session.activeOrg?.id
  const [busy, setBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<'accuracy' | 'calibration' | 'agreement' | 'harness'>('accuracy')

  const runs = useRows<Row>('bg_eval_runs', {
    filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }],
    sort: [{ field: 'created_at', direction: 'desc' }], limit: 20,
  }, [orgId])
  const cases = useRows<Row>('bg_eval_cases', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 500 }, [orgId])
  const calibration = useRows<Row>('bg_calibration_runs', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 40 }, [orgId])
  const agreement = useRows<Row>('bg_agreement_scores', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 100 }, [orgId])
  const riskScores = useRows<Row>('bg_risk_scores', { filters: [{ field: 'organization_id', op: 'eq', value: orgId ?? '' }], limit: 500 }, [orgId])

  const latest = runs.items.find((r) => r.status === 'completed') ?? runs.items[0]
  const metrics: Row = latest?.metrics ?? {}
  const perCategory: Row[] = latest?.per_category ?? []
  const perRule: Row[] = latest?.per_rule ?? []
  const ruleAgreement = agreement.items.filter((a) => a.scope === 'rule')
  const reviewerAgreement = agreement.items.filter((a) => a.scope === 'reviewer')
  const decided = riskScores.items.filter((s) => ['approved', 'changes_requested', 'rejected'].includes(s.outcome))

  const call = async (label: string, fn: string, payload: Row) => {
    setBusy(label)
    const res = await runFn(fn, payload)
    setBusy(null)
    if (!res.ok) { toast.push(res.error ?? 'The action failed.', 'error'); return null }
    const out: any = res.output
    toast.push(out.message ?? 'Done.', 'success')
    runs.refresh(); calibration.refresh(); agreement.refresh(); riskScores.refresh(); cases.refresh()
    return out
  }

  const MetricRow = ({ label, block, kind }: { label: string; block: Row; kind?: string }) => (
    <tr>
      <td>
        <div style={{ fontWeight: 560 }}>{label}</div>
        {kind ? <div className="muted small">{kind}</div> : null}
      </td>
      <td className="mono">{block.true_positives ?? '—'}</td>
      <td className="mono">{block.false_positives ?? '—'}</td>
      <td className="mono">{block.false_negatives ?? '—'}</td>
      <td className="mono">{block.precision ?? '—'}</td>
      <td className="mono">{block.recall ?? '—'}</td>
      <td className="mono">{block.false_positive_rate ?? '—'}</td>
      <td className="mono">{block.sample_size ?? '—'}</td>
      <td>
        {block.reliable
          ? <Badge tone="b-ok">reliable</Badge>
          : <Badge tone="b-medium" title={block.note ?? ''}>insufficient data</Badge>}
      </td>
    </tr>
  )

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Quality, accuracy and calibration</h1>
          <p className="muted small" style={{ margin: 0 }}>
            What the engine actually gets right, measured against {cases.items.length} hand-labelled cases, and whether
            the risk bands predict rework. Metrics without enough evidence say so instead of showing a number.
          </p>
        </div>
        <div className="spacer" />
        <Button size="sm" onClick={() => { runs.refresh(); calibration.refresh(); agreement.refresh(); cases.refresh() }}>
          <RefreshCw size={14} /> Refresh
        </Button>
        <Button size="sm" variant="primary" disabled={busy === 'seed'}
          onClick={() => call('seed', 'bg_eval_seed', { action: 'seed' })}>
          {busy === 'seed' ? <span className="spinner" aria-hidden="true" /> : <FlaskConical size={14} />} Seed labelled cases
        </Button>
        <Button size="sm" variant="primary" disabled={busy === 'run'}
          onClick={() => call('run', 'bg_eval_accuracy', { action: 'run', mode: 'deterministic' })}>
          {busy === 'run' ? <span className="spinner" aria-hidden="true" /> : <PlayCircle size={14} />} Run evaluation
        </Button>
      </div>

      <div className="row" role="tablist" aria-label="Quality views">
        {([['accuracy', 'Measured accuracy'], ['calibration', 'Risk calibration'],
           ['agreement', 'Reviewer agreement'], ['harness', 'Reliability harness']] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={`chip ${tab === key ? 'on' : ''}`}
            onClick={() => setTab(key as any)}>{label}</button>
        ))}
      </div>

      {tab === 'accuracy' ? (
        <>
          {!latest ? (
            <Card pad><Empty title="No evaluation run recorded yet"
              hint="Seed the labelled cases, then run the evaluation. Each case is analysed by the real engine and compared against its label." /></Card>
          ) : latest.status !== 'completed' ? (
            <Card pad><Loading label={`Evaluation run is ${latest.status}…`} /></Card>
          ) : (
            <>
              <div className="grid cols-4">
                <Card><div className="metric"><div className="metric-label">Precision</div>
                  <div className="metric-value">{metrics.precision ?? '—'}</div>
                  <div className="metric-note">{metrics.true_positives ?? 0} true / {metrics.false_positives ?? 0} false positives</div></div></Card>
                <Card><div className="metric"><div className="metric-label">Recall</div>
                  <div className="metric-value">{metrics.recall ?? '—'}</div>
                  <div className="metric-note">{metrics.false_negatives ?? 0} missed of {metrics.flagged_cases ?? 0} expected</div></div></Card>
                <Card><div className="metric"><div className="metric-label">Clean content passed</div>
                  <div className="metric-value">{metrics.clean_cases_passed ?? 0}/{metrics.clean_cases ?? 0}</div>
                  <div className="metric-note">no false alarm on correct copy</div></div></Card>
                <Card><div className="metric"><div className="metric-label">Outcome accuracy</div>
                  <div className="metric-value">{metrics.outcome_accuracy ?? '—'}</div>
                  <div className="metric-note">{metrics.cases_run ?? 0} case(s) executed, {metrics.cases_errored ?? 0} errored</div></div></Card>
              </div>

              <Card pad>
                <div className="row small">
                  <FlaskConical size={14} />
                  <span>
                    Mode <b>{latest.engine_mode}</b>, engine version {latest.engine_version}, run {fmtDateTime(latest.completed_at)}
                    {latest.duration_ms ? ` in ${(latest.duration_ms / 1000).toFixed(1)} s` : ''}. Advisories
                    (severities {((metrics.advisory_severities_excluded ?? []) as string[]).join(', ')}) are excluded from
                    the false-positive count, because a formatting suggestion is not a wrong flag.
                  </span>
                </div>
              </Card>

              <Card>
                <CardHead title="Accuracy per category" subtitle="Counted from the labelled cases; a category needs at least 5 relevant cases before its metric is treated as reliable" />
                <div className="table-wrap">
                  <table aria-label="Accuracy per category">
                    <thead><tr><th>Category</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>FP rate</th><th>Sample</th><th>Status</th></tr></thead>
                    <tbody>
                      {perCategory.map((c) => (
                        <MetricRow key={c.category} label={CATEGORY_LABEL[c.category] ?? c.category} block={c} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card>
                <CardHead title="Accuracy per rule" subtitle="Which rules fire correctly and which miss" />
                <div className="table-wrap">
                  <table aria-label="Accuracy per rule">
                    <thead><tr><th>Rule</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>FP rate</th><th>Sample</th><th>Status</th></tr></thead>
                    <tbody>
                      {perRule.map((r) => <MetricRow key={r.rule_code} label={r.rule_code} block={r} />)}
                    </tbody>
                  </table>
                </div>
              </Card>

              {(latest.insufficient ?? []).length > 0 ? (
                <Card>
                  <CardHead title="Insufficient data" subtitle="Metrics the platform will not present as reliable yet" />
                  <ul className="small" style={{ padding: '12px 16px 12px 34px' }}>
                    {(latest.insufficient ?? []).map((i: Row, k: number) => (
                      <li key={k}><b>{i.scope}</b> — {i.note}</li>
                    ))}
                  </ul>
                </Card>
              ) : null}
            </>
          )}
        </>
      ) : null}

      {tab === 'calibration' ? (
        <>
          <Card pad>
            <div className="row">
              <Gauge size={15} />
              <span className="small">
                Calibration compares each band's predicted midpoint with the rework rate actually observed in decided
                reviews. A band needs {calibration.items[0]?.sufficient === false ? 'at least 5' : 'at least 5'} decided
                reviews before its figure is reported as reliable.
              </span>
              <div className="spacer" />
              <Button size="sm" disabled={busy === 'cal'} onClick={() => call('cal', 'bg_risk_calibration', { organization_id: orgId })}>
                {busy === 'cal' ? <span className="spinner" aria-hidden="true" /> : <RefreshCw size={14} />} Recalibrate
              </Button>
            </div>
          </Card>

          <Card>
            <CardHead title="Predicted band vs observed rework" subtitle={`${decided.length} decided review(s) carry a risk score`} />
            {calibration.items.length === 0 ? (
              <Empty title="No calibration recorded yet"
                hint="Score reviews and decide them, then recalibrate. Until then the platform reports insufficient data rather than a figure." />
            ) : (
              <div className="table-wrap">
                <table aria-label="Risk calibration">
                  <thead><tr><th>Band</th><th>Predicted midpoint</th><th>Decided</th><th>Needed rework</th><th>Observed rate</th><th>Gap</th><th>Status</th></tr></thead>
                  <tbody>
                    {calibration.items.map((c) => (
                      <tr key={c.id}>
                        <td><SeverityBadge severity={c.band} /></td>
                        <td className="mono">{c.predicted_midpoint}</td>
                        <td className="mono">{c.decided_count}</td>
                        <td className="mono">{c.rework_count}</td>
                        <td className="mono">{c.observed_rate ?? '—'}</td>
                        <td className="mono">{c.observed_rate !== null && c.observed_rate !== undefined
                          ? (Number(c.observed_rate) - Number(c.predicted_midpoint)).toFixed(3) : '—'}</td>
                        <td>{c.sufficient
                          ? <Badge tone="b-ok">enough data</Badge>
                          : <Badge tone="b-medium" title={c.note ?? ''}>insufficient data</Badge>}
                          {c.note ? <div className="muted small" style={{ maxWidth: 280 }}>{c.note}</div> : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHead title="Reliability view" subtitle="What the numbers above do and do not support" />
            <div style={{ padding: 16 }} className="stack small">
              <div className="row">
                <Badge tone={decided.length >= 5 ? 'b-ok' : 'b-medium'}>{decided.length} decided review(s)</Badge>
                <Badge tone="b-info">Brier score {latest?.metrics?.brier_score ?? '—'}</Badge>
                <Badge tone="b-info">bands with enough data: {calibration.items.filter((c) => c.sufficient).length}/{calibration.items.length || 4}</Badge>
              </div>
              <div className="muted">
                The Brier score is the mean squared error of score/100 against the binary "needed rework" outcome.
                Lower is better; 0.25 is what always predicting 50% scores. It is a measure, not a claim of
                statistical calibration.
              </div>
              {decided.length === 0 ? (
                <div className="warn-msg">
                  <b>Insufficient data.</b> No scored review has reached a decision yet, so the platform cannot say
                  whether high-risk scores predict rework. Decide some reviews and recalibrate.
                </div>
              ) : (
                <div className="notice">
                  With {decided.length} decided review(s), treat the band ordering as a signal to check, not proof.
                  The app will report a band as reliable only once it has at least 5 decided reviews.
                </div>
              )}
            </div>
          </Card>
        </>
      ) : null}

      {tab === 'agreement' ? (
        <>
          <Card pad>
            <div className="row">
              <Users size={15} />
              <span className="small">
                Agreement scoring separates two very different problems: a rule that reviewers read differently
                (kappa below 0.4) and one reviewer who is simply stricter than their colleagues.
              </span>
              <div className="spacer" />
              <Button size="sm" disabled={busy === 'agree'} onClick={() => call('agree', 'bg_reviewer_agreement', { organization_id: orgId })}>
                {busy === 'agree' ? <span className="spinner" aria-hidden="true" /> : <RefreshCw size={14} />} Recompute
              </Button>
            </div>
          </Card>

          <div className="split-even">
            <Card>
              <CardHead title="Per rule" subtitle="Pairwise agreement and Cohen's kappa" />
              {ruleAgreement.length === 0 ? <Empty title="No agreement data yet" hint="Reviewers must decide findings on the same rules for this to mean anything." /> : (
                <div className="table-wrap">
                  <table aria-label="Rule agreement">
                    <thead><tr><th>Rule</th><th>Decisions</th><th>Agreement</th><th>Kappa</th><th>Reading</th></tr></thead>
                    <tbody>
                      {ruleAgreement.map((a) => (
                        <tr key={a.id}>
                          <td className="mono">{a.subject_key}</td>
                          <td className="mono">{a.decision_count}</td>
                          <td className="mono">{a.agreement_rate ?? '—'}</td>
                          <td className="mono">{a.kappa ?? '—'}</td>
                          <td>
                            <Badge tone={a.interpretation === 'consistent' ? 'b-ok'
                              : a.interpretation === 'insufficient_data' ? 'b-medium'
                                : a.interpretation === 'unclear_rule' ? 'b-high' : 'b-info'}>
                              {a.interpretation.replace(/_/g, ' ')}
                            </Badge>
                            <div className="muted small" style={{ maxWidth: 260 }}>{a.detail}</div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card>
              <CardHead title="Per reviewer" subtitle="Strictness index — not a performance score" />
              {reviewerAgreement.length === 0 ? <Empty title="No reviewer data yet" /> : (
                <div className="table-wrap">
                  <table aria-label="Reviewer strictness">
                    <thead><tr><th>Reviewer</th><th>Decisions</th><th>Rejection rate</th><th>Reading</th></tr></thead>
                    <tbody>
                      {reviewerAgreement.map((a) => (
                        <tr key={a.id}>
                          <td className="small">{a.subject_key}</td>
                          <td className="mono">{a.decision_count}</td>
                          <td className="mono">{a.strictness_index ?? '—'}</td>
                          <td>
                            <Badge tone={a.interpretation === 'strict_reviewer' ? 'b-info' : a.interpretation === 'consistent' ? 'b-ok' : 'b-medium'}>
                              {a.interpretation.replace(/_/g, ' ')}
                            </Badge>
                            <div className="muted small" style={{ maxWidth: 240 }}>{a.detail}</div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </>
      ) : null}

      {tab === 'harness' ? (
        <Card>
          <CardHead title="Live reliability harness" subtitle="Exercises the real pipeline: state machine, permissions, workspace isolation, ingestion, evidence handling" />
          <div style={{ padding: 16 }}>
            <Button variant="primary" disabled={busy === 'eval'} onClick={() => call('eval', 'bg_eval_run', { organization_id: orgId })}>
              {busy === 'eval' ? <span className="spinner" aria-hidden="true" /> : <Activity size={14} />} Run reliability checks
            </Button>
            <div className="muted small" style={{ marginTop: 10 }}>
              Results are reported on the run record for <span className="mono">bg_eval_run</span>. Every check executes
              against this live workspace and reports what actually happened, including skips.
            </div>
          </div>
        </Card>
      ) : null}

      <Card pad>
        <div className="row small">
          <AlertTriangle size={14} />
          <span>
            Nothing on this page is an estimate. Accuracy figures come from executed runs against the labelled set;
            calibration comes from decisions recorded on real reviews; metrics with too little evidence are labelled
            insufficient data rather than shown as a number.
          </span>
        </div>
      </Card>
    </div>
  )
}

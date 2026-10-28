# Test report

All tests run against the **live pod** through the same functions the app calls. The suite
creates an isolated `quality-assurance` workspace with three approved rules, three approved
facts (including one expired) and seven fixture submissions that are analysed by the real
engine. No mocks, no in-memory substitutes.

Run it with:

```bash
cd brandguard/tests && python -m pytest -v
```

## Executed results

| Run | Command | Result |
| --- | --- | --- |
| Suite run 1 | `pytest brandguard/tests -v` | 25 passed, 3 failed (348 s) |
| Suite run 2 (after the first fixes) | `pytest brandguard/tests -v` | 26 passed, 2 failed (307 s) |
| Fixed tests re-run | `pytest brandguard/tests -v -k "rescan or audit"` | 2 passed (367 s) |
| **Final full suite** | `pytest brandguard/tests -v` | **28 passed, 0 failed (396 s)** |

The failures were not test noise — two of them exposed real defects, which were fixed and
re-verified:

1. **A finding that was corrected and re-scanned stayed "accepted" instead of "resolved".**
   The reconciliation step only closed findings whose prior status was exactly `open`.
   Fixed: `open`, `accepted` and `escalated` are all treated as unresolved, so an applied
   correction is closed only when the re-scan confirms it is gone.
2. **A required disclaimer was demanded on content with no discount at all.** The
   deterministic check applied `required_disclaimer` rules unconditionally. Fixed: rules
   carry an `applies_when` condition and are only enforced when the content matches it
   (verified by `test_required_disclaimer_applies_only_when_relevant`).
3. The third failure was a test-fixture artefact: the QA workspace's rules had been created
   by direct record writes, so no `guideline.created` audit entry existed. The test now
   creates and attempts to approve a rule through the real workflow — which also proves the
   separation-of-duties guard on guideline approval.

## What is covered

**Engine (`test_engine.py`)**
- prohibited terminology is detected with the approved replacement
- correct content produces no contradiction and no high-severity false positive
- a wrong warranty duration is flagged as `contradicted` with the approved fact cited and a
  usable suggested correction
- facts whose unit cannot be read unambiguously are not compared numerically
- a required disclaimer fires only when it applies
- an expired approved fact is surfaced as `expired`, not as current truth
- an unsupported claim is `not_found`, never `contradicted`
- confidentiality markers are flagged as ambiguous, for a human to confirm
- findings are never duplicated inside a review
- every finding carries a title, explanation, allowed severity, allowed evidence status and
  a detection method
- correction cycle: accept → apply (new version, analysis invalidated) → re-scan →
  finding resolved, with the original submission preserved as a version

**Workflow, permissions, isolation (`test_workflow.py`)**
- invalid state transitions are refused
- approval is blocked before a successful analysis
- separation of duties blocks self-approval
- a decision without a reason is refused
- unresolved mandatory findings block approval for a second reviewer
- an override without a documented reason is refused
- cross-workspace access is refused
- a submission with no content is refused
- an unsupported file type is reported as failed, with a note
- approval is refused when the analysed version is older than the current version
- guideline authoring is audited, and a rule author cannot approve their own rule

**Analytics consistency (`test_analytics.py`)**
- findings-by-category equals the stored rows it is drawn from
- open high-risk counts are traceable to stored findings
- evidence statuses stay inside the allowed set
- the seeded workspace is internally consistent (active rules have approvers, reviews have
  creators)

## Live reliability harness (in-app)

`bg_eval_run`, exposed on **Quality & evaluation**, executes eleven checks against the live
workspace on demand. Last executed result on the seeded workspace: **11 passed, 0 failed,
0 skipped** — workflow transition guard, approval-before-analysis block, self-approval
refusal, cross-workspace refusal, unsupported-file honesty, empty-submission refusal,
expired-fact surfacing, missing-evidence vs contradiction separation, no duplicate
findings, re-analysis requirement after revision, and failed runs recorded as failures.

## Labelled evaluation dataset

`src/pages/Quality.tsx` carries eleven labelled cases (correct content, tone, terminology,
contradicted fact, unsupported claim, expired reference, ambiguous claim, health claim,
confidential material, accessibility, corrected content) with the approved reference, the
expected finding and the expected evidence status. Cases E2–E10 are exercised as real
reviews in the workspace, and the engine tests above assert the same expectations.

**No precision/recall percentage is reported.** The labelled set is small and was not built
to be a statistically meaningful benchmark, so quoting an accuracy figure from it would
overstate what has been measured. What has been measured is stated above.

## Not covered

- OCR and text-in-image analysis (not implemented — see `LIMITATIONS.md`)
- Visual similarity beyond perceptual-hash comparison
- Load/performance testing
- Multi-browser end-to-end automation (the deployed app was exercised manually in the
  agent browser: every screen loaded, and the review, correction, approval, analytics and
  audit flows were driven end to end)

## Verification of the 2026-10-23 features

These six features were verified by executing them against the live Northstar Living
workspace and reading back the stored results — the same standard as the suite above. They are
not yet part of the pytest suite; that is the next test task, and the honest status is recorded
here rather than implied.

| Feature | Executed | Observed result |
| --- | --- | --- |
| Cross-channel consistency | `bg_campaign_scan` over all campaigns | AirPure 300 launch: score 34/100 across 6 channels with a price conflict ($399 vs $499), a warranty conflict (1 year vs 3 years) and a near-miss product name (`AirPure 300` vs the approved `Northstar AirPure 300`); the Winter offer campaign scored 78 with a `PureAir Pro` naming conflict |
| Claim provenance | `bg_provenance_scan` | 15 claim links traced (11 valid, 3 expired, 1 unsupported), 2 alerts raised, graph of 30 nodes / 36 edges; alerts are deduplicated to once a week per claim |
| Visual compliance | `bg_visual_scan` on a 900×560 creative with real text | Pixel layer: palette conformance 97.9%, content bounds, safe margins, 4 text bands, logo block located in the left third (#e6b13b); text layer: exact transcription of 7 lines; 5 findings recorded (critical confidential marker, missing disclaimer, prohibited product name, unsupported claim), with limitations recorded for font and size |
| Risk triage | `bg_risk_score` across the workspace | 8 submissions scored: 4 critical (88–98), 2 high (53–66), each with a factor breakdown naming the points and the reason |
| Root cause | `bg_root_cause` | 8 reviews and 75 findings analysed into 9 suggestions: 5 process-gap, 2 rule-clarity, 1 outdated-source, 1 training-gap, 1 campaign-spike, each with a proposed rule text and its supporting cases |
| Minimal correction | `bg_minimal_correction` propose | 3 spans rewritten with rule mapping (`FACT-001`, `CLAIM-001`, `TERM-002`), 1 issue resolved, 0 introduced, 5 edits blocked by the guard rails (overlapping spans, non-minimal rewrites, unlocatable spans), each with a stated reason |

Negative paths were exercised too: a claim with no evidence is reported as `not_found` rather
than contradicted, an expired source is reported as `expired`, a creative with unreadable
regions is reported with limitations, and every generated proposal requires a human approval
before it can take effect.

## Round 5 verification (2026-10-28)

Executed against the live pod, reading back stored results.

| Area | Executed | Observed |
| --- | --- | --- |
| Evaluation framework | `bg_eval_seed` then `bg_eval_accuracy` (deterministic) | 83 cases seeded across 13 rules and 7 facts; full sweep scored 83/83 with 0 errors |
| Measured accuracy | same | precision **0.347**, recall **0.689**, F1 **0.462**, outcome accuracy **0.518**; clean controls 1/23, flagged cases caught 42/60; per-category and per-rule metrics with sample sizes; 12 metrics marked insufficient data |
| Risk calibration | `bg_risk_calibration` | 4 bands computed; 0 decided reviews, so every band reports `sufficient: false` with an explicit insufficient-data note, and 8 scored-but-undecided reviews are listed as excluded |
| Reviewer agreement | `bg_reviewer_agreement` | 36 decisions from 2 reviewers across 11 rule groups; 0 rule groups have two reviewers, so every metric reports insufficient data with the reason |
| Multi-format creative scan | `bg_creative_scan` on a 3-page PDF | decoder `imagemagick`; 3 pages at 1211×1568; **3 evidence overlays written**, one per page; palette conformance 0.995–0.998; text bands and logo blocks located per page; word boxes returned with box agreement 1.0; dual-read agreement 1.0 / 0.827 / 0.564; contrast measured (worst 21:1) and 3 bands flagged below the 12 px floor |
| Approval chains | `bg_approval_chain` resolve | Chain resolved with 5 steps; second-reviewer requirement triggered by sensitive claim findings; mandatory-finding block reported |
| Fact lifecycle | `bg_fact_lifecycle` scan + schedule | Tasks created for expiring/expired facts, dependency lists attached, notifications deduplicated to once a week; schedule `fact-lifecycle-daily` created |
| Governed publishing | `bg_publish_action` prepare/package/push | Gate reports all four checks with their evidence; connector path reports no connector installed; signed package written with the pinned hash and integrity seal; every attempt audited |

Two defects in the round-5 code were found and fixed during this verification: the evaluation
runner read findings before the analysis JOB had finished (which produced a bogus recall of 0),
and PDF page images were fetched with the wrong `download_child` call, which silently disabled
pixel measurement on every page.

The engine defects listed in `LIMITATIONS.md` §23 were **found** by this evaluation and are
deliberately not fixed, because this round's scope excluded changing the existing engine.

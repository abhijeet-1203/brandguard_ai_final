# Using BrandGuard AI

The deployed app: **https://brandguard.apps.lemma.work**

Sign in with the same account you use for this pod. Your role in the selected workspace
is shown in the top bar; the workspace switcher moves between workspaces you belong to.

## The five-minute walkthrough

1. **Dashboard** — real metrics from stored reviews: how many submissions, what is waiting
   for a human, approval rate, open high-risk findings, the most frequent issue categories
   and the most-repeated rule breaches. Every card links to the records behind it.
2. **Content reviews → open "AirPure 300 launch — Instagram post"** — a submission with
   known problems. The **Findings** tab lists each issue with severity, evidence status,
   the exact span it applies to, why it was flagged, the approved rule or fact that
   supports it, and a suggested correction:
   - the deprecated product name (`TERM-001`, exact match, with the approved replacement),
   - a price that contradicts the approved fact (marked *contradicted*, because the source
     says $499),
   - claims with no approved evidence (marked *no approved evidence found* — not "false"),
   - a tone issue flagged by the AI against the workspace's own tone rule.
   Accept a suggestion, then **Apply accepted corrections**: a new version is created, the
   analysis is invalidated, and the re-scan marks the finding **resolved** only when it is
   actually gone.
3. **Approval centre** — reviews waiting for a person, ranked by risk, with the role
   required to decide. Approve, request changes or reject with a reason. Try approving a
   submission that has unresolved mandatory findings: the backend refuses and offers a
   documented override (brand manager or administrator only). Try approving your own
   submission: separation of duties refuses it.
4. **Brand guidelines** — 14 approved rules plus a draft. Edit a rule and it returns to
   *pending approval*; approval is a separate, audited act. **Import from document** uploads
   a brand book, extracts text, proposes rules with their source quotes and saves them as
   drafts.
5. **Approved facts** — the only source treated as approved truth, with effective and
   expiration dates and evidence records. The expired founding fact and the rejected
   "99.9% of viruses" claim are visible: the engine reports the first as *expired* and the
   second as *no approved evidence*.
6. **Asset library** — approved logos and a superseded 2021 logo, each with a perceptual
   hash. The "partner kit — logo lock-up" submission uses the superseded file, and the
   visual check reports it as an outdated asset with the similarity score.
7. **Governance memory** — patterns computed from the review history: which rule keeps
   being breached, which rule repeatedly produces findings that need human judgement
   (a guideline-ambiguity signal), how many cases and which reviews support it. A brand
   manager can turn an insight into a **draft** rule; nothing is enforced until it is
   approved.
8. **Quality & evaluation** — press **Run reliability checks** to execute eleven live checks
   (state-machine guards, permission boundaries, workspace isolation, ingestion honesty,
   evidence handling) and see exactly what happened.
9. **Audit log / Team & permissions / Settings & privacy** — the decision trail, the role
   model (roles are enforced server-side), the approval policy, the AI data-flow disclosure
   and the workspace reset.

## Reset

**Settings & privacy → Reset workspace data** deletes a workspace's records and rebuilds
them deterministically (administrator only). Everything else in the app writes real records
you can edit, so feel free to experiment.

## What it will not do

It will not publish content, and it will not approve anything by itself. There is no
publishing integration, and approval is recorded against a specific version by a named
person. Where a check cannot be performed (text inside an image, for example) the app says
so instead of guessing.

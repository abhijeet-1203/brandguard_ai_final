# Known limitations

Honest status of what is and is not implemented. Nothing below is presented as working.

## Analysis

1. **No OCR.** The runtime has no OCR engine, so text embedded inside images is *not*
   read. The engine reports "no text extraction (this runtime has no OCR)" and the
   accessibility stage says text-in-image was not checked. Uploading a text-based document
   or pasting the text is the supported alternative.
2. **Visual comparison is a heuristic.** Logo/asset matching uses an 8×8 perceptual hash
   computed in the browser at upload time and stored on the asset; the engine compares
   hashes and dominant colours. A similarity score is evidence for review, not proof of
   brand compliance, and images whose hash cannot be compared are reported as *unverified*
   rather than non-compliant. Distortion, recolouring and typography checks that need pixel
   analysis are not implemented.
3. **Accessibility checks cover HTML markup only** (alt text presence, link text, heading
   order). Contrast, font size, reading order and full WCAG conformance are not evaluated,
   and the UI says so.
4. **Numeric comparison is unit-aware but literal.** Only values whose unit the engine can
   read unambiguously are compared; other facts are reported as not machine-comparable.
   Ranges ("3–5 days"), fractions ("half the price") and implicit comparisons are not
   evaluated numerically.
5. **Confidentiality detection is pattern-based.** It flags markers and identifiers; it
   cannot promise to find every piece of confidential material, and it says so in the
   finding.
6. **Confidence is an estimate.** Values come from the model and from rule strength; they
   are not calibrated probabilities. The UI labels them "estimate".
7. **The AI pass depends on the platform model.** If it is unavailable the run records the
   limitation, keeps the deterministic findings, and does not fabricate a clean result.

## Product

8. **No publishing integration.** BrandGuard AI records approvals; it never publishes.
   `auto_publish_enabled` exists in policy but is documented as not wired to any publisher,
   and no code path publishes content.
9. **Single-pod tenancy.** Workspaces are rows in one pod. Isolation is enforced on every
   function (ownership + membership) and verified by test, but a pod member who queries the
   datastore directly (outside the app) can see all rows, because the platform's row-level
   security granularity is per user, not per workspace. The app never exposes cross-workspace
   data.
10. **Team membership is by email.** A member is granted access by adding their email; the
    platform's own pod membership is the outer boundary. Suspending a membership blocks the
    app's checks but does not remove pod access.
11. **Comments are review-level.** Text-range anchoring stores offsets and is displayed as a
    range reference, but the UI does not render inline text-range threads.
12. **Retention is declarative.** The retention period is configurable and recorded, but no
    automated purge job runs; resetting a workspace (administrator) is the implemented
    deletion path.
13. **Notifications are in-app** (plus the platform's own inbox). No email or Slack
    delivery is configured.
14. **Guideline extraction quality varies with the document.** Extracted rules are drafts
    with their source quote and always require human approval; a poorly structured PDF may
    produce few or imprecise proposals, which the UI reports rather than hides.

## Test coverage

15. The suite covers the engine, workflow guards, permissions, isolation and analytics
    consistency against live fixtures. It does **not** measure general model accuracy: no
    precision/recall figure is quoted for the AI pass, because no labelled ground truth of
    sufficient size has been executed. The deterministic rules are exercised against known
    inputs and expected outcomes.

## Added 2026-10-23

16. **Text inside an image is read by a vision model, not by an OCR engine.** This runtime has
    no OCR library, so the transcription is what a vision model read from the pixels. It is
    accurate on clear text, it is labelled as a transcription in the UI, and words it cannot
    read are reported as unreadable rather than guessed.
17. **The pixel layer measures PNG files.** The stdlib decoder handles 8-bit greyscale, RGB and
    RGBA PNGs. JPEG and WebP creatives still get the vision transcription, but their palette,
    margins, text bands and logo geometry are reported as not measured rather than estimated.
18. **Geometry comparison needs an approved asset with a comparable hash.** Without one, the
    scan says so and only reports absolute measurements. Logo distortion, typography and
    print-colour accuracy are not determined.
19. **The risk score is a triage aid, not a calibrated probability.** Factors are weighted by
    judgement, the thresholds are fixed, and the app says so wherever the score appears. The
    outcomes table measures whether the bands predict rework, and with a small number of
    decided reviews it is a signal rather than proof.
20. **Cross-channel conflicts only cover what can be compared literally.** Prices, warranty
    terms, discounts, product names, slogans and dates are compared; tone, imagery and
    narrative consistency across channels are not, and a channel that is absent from the
    campaign is reported as missing coverage rather than as agreement.
21. **Root causes are interpretations.** The classification is derived from stored findings
    (ambiguity rate, expiry, concentration by campaign/type/author) and every suggestion shows
    its supporting cases. It is a hypothesis for a brand manager to accept, edit or dismiss -
    never an automatic policy change, and never proof that the rule caused the problem.
22. **Minimal edits cannot add a missing sentence.** The engine replaces flagged spans, so a
    missing disclaimer or a required statement with no span to replace is reported as
    something the edit could not fix, rather than being inserted silently.

## Round 5 (2026-10-28)

23. **Measured engine accuracy is currently low, and the causes are known.** Precision 0.347 and
    recall 0.689 over 83 labelled cases. Three defects are behind it: `required_term` rules have
    no relevance gate, unsupported-claim findings are all attributed to the first `claim_policy`
    rule, and formatting/accessibility/tone/messaging findings carry no rule code. Fixing them
    requires editing the existing engine, which this round was asked not to touch. The numbers
    are published as measured.
24. **No OCR engine exists in this runtime.** No tesseract, no Pillow, no numpy, no cv2, and the
    function dependency builder is not installed. Text inside a creative is read by a vision
    model, with word boxes and reading confidence and a second independent read, and every scan
    states this. It is a transcription, not OCR.
25. **Slide decks are not decodable.** PPT/PPTX/KEY/ODP cannot be rendered here (no office
    converter). The scanner says so and asks for a PDF export; PDFs are analysed page by page.
26. **Contrast and text size are measured at the image's own resolution.** Text height in pixels
    therefore assumes the page is viewed at that size, and the contrast measurement compares the
    darkest and lightest pixels inside a detected band — it does not model font weight, antialiasing
    or the actual glyph coverage.
27. **Publishing is draft-only and connector-less here.** No connector is installed in this
    organization, so the draft push reports that honestly and the signed package is the working
    path. The package's seal is a deterministic SHA-256 integrity seal over the content hash,
    manifest hash, workspace and version — tamper-evident, but **not** a third-party signature,
    because no signing key is configured.
28. **Calibration and reviewer agreement need real decision volume.** Both currently report
    insufficient data: no scored review has been decided, and no rule has been decided on by two
    reviewers. Until decisions accumulate, the reliability view says so instead of showing a
    figure.
29. **The labelled set is 83 cases, not a benchmark.** It is large enough to expose the defects
    above and to give per-category sample sizes, but it is not a statistically meaningful
    benchmark and no accuracy figure should be generalised from it.

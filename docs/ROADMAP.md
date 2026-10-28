# Roadmap

Ordered by the value each item adds to the governance loop, not by novelty.

**Next**
1. **OCR pipeline** for text-in-image and scanned documents, so the accessibility and
   confidentiality checks cover image-only assets (the honest "not checked" states become
   real checks).
2. **Inline text-range comments** rendered against the content, using the offsets already
   stored on `bg_comments`.
3. **Second-reviewer enforcement by default** and configurable multi-step approval chains
   per content type (the policy flag and the decision history already support this).
4. **Scheduled re-verification of approved facts**: a schedule that expires facts, notifies
   owners and opens a re-verification task before the expiration date.
5. **Retention execution**: a scheduled job that purges records past the workspace's
   retention period and records the purge in the audit log.

**Then**
6. **Connector-based publishing** behind explicit policy (draft-to-CMS, never silent
   publish) with the approval version pinned to the published artefact.
7. **Vector retrieval** over long brand documents so rules and facts are selected by
   relevance for large corpora instead of the current bounded context window.
8. **Reviewer agreement scoring** on guideline ambiguity, using paired decisions to
   distinguish a genuinely unclear rule from a strict reviewer.
9. **Asset diffing**: side-by-side visual comparison with feature matching instead of the
   hash heuristic, plus font and colour-token extraction from brand manuals.
10. **Evaluation dataset expansion** to a labelled corpus large enough to report measured
    precision/recall for the AI pass, with per-category false-positive tracking.

**Later**
11. Multi-language rule sets and locale-aware terminology checks.
12. Workspace-level SLOs (review turnaround, approval latency) with breach alerts.
13. An offline/air-gapped analysis mode that runs only the deterministic engine, for
    organisations that cannot send content to a hosted model.

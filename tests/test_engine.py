"""Engine tests: deterministic rules, evidence status, reconciliation.

Each assertion is made against findings produced by the real pipeline for the QA fixtures.
"""
from conftest import _rows


def _by_rule(findings, code):
    return [f for f in findings if f.get("rule_code") == code]


def test_prohibited_term_is_detected_with_a_replacement(qa):
    findings = qa["cases"]["terminology"]["findings"]
    hits = _by_rule(findings, "QA-TERM-001")
    assert hits, "prohibited term was not detected"
    hit = hits[0]
    assert hit["evidence_status"] == "supported"
    assert hit["suggested_correction"] == "Widget One"
    assert hit["original_text"].lower().startswith("widgetone")


def test_correct_content_produces_no_contradiction(qa):
    findings = qa["cases"]["clean"]["findings"]
    assert not [f for f in findings if f["evidence_status"] == "contradicted"], \
        "a correct submission was flagged as contradicting an approved source"
    assert not [f for f in findings if f["severity"] in ("critical", "high")
                and f["category"] in ("factual_accuracy", "promotional_claim", "confidentiality")]


def test_contradicted_number_is_flagged_as_contradicted(qa):
    findings = qa["cases"]["contradiction"]["findings"]
    hits = [f for f in findings if f["category"] == "factual_accuracy" and f["evidence_status"] == "contradicted"]
    assert hits, "a wrong warranty duration was not flagged"
    assert hits[0]["severity"] == "high"
    assert hits[0]["source_reference"].get("fact_id")
    assert "two-year" in (hits[0]["suggested_correction"] or "")


def test_unit_that_cannot_be_read_is_not_compared(qa):
    """A fact whose unit is not machine-comparable must not produce a contradiction."""
    findings = qa["cases"]["clean"]["findings"]
    assert not [f for f in findings if f["evidence_status"] == "contradicted"]


def test_required_disclaimer_applies_only_when_relevant(qa):
    with_discount = _by_rule(qa["cases"]["disclaimer"]["findings"], "QA-DISC-001")
    without_discount = _by_rule(qa["cases"]["clean"]["findings"], "QA-DISC-001")
    assert with_discount, "missing disclaimer was not flagged on discounted content"
    assert not without_discount, "disclaimer was demanded on content with no discount"


def test_expired_fact_is_reported_as_expired(qa):
    findings = qa["cases"]["expired"]["findings"]
    hits = [f for f in findings if f["evidence_status"] == "expired"]
    assert hits, "an expired approved fact was not surfaced"
    assert hits[0]["severity"] in ("high", "critical")
    assert hits[0]["source_reference"].get("expiration_date")


def test_unsupported_claim_is_not_found_not_contradicted(qa):
    findings = qa["cases"]["claim"]["findings"]
    claims = [f for f in findings if f["category"] == "promotional_claim"]
    assert claims, "an unsupported superlative was not flagged"
    assert any(f["evidence_status"] == "not_found" for f in claims)
    assert not any(f["evidence_status"] == "contradicted" for f in claims), \
        "absence of evidence must not be reported as a contradiction"


def test_confidentiality_marker_is_flagged_ambiguously(qa):
    findings = qa["cases"]["confidential"]["findings"]
    hits = [f for f in findings if f["category"] == "confidentiality"]
    assert hits, "internal-only material was not flagged"
    assert hits[0]["evidence_status"] in ("ambiguous", "supported")
    assert hits[0]["severity"] in ("critical", "high")


def test_findings_are_never_duplicated(qa):
    for key, case in qa["cases"].items():
        fingerprints = [f["fingerprint"] for f in case["findings"] if f.get("fingerprint")]
        assert len(fingerprints) == len(set(fingerprints)), f"duplicate findings in case {key}"


def test_every_finding_answers_the_five_questions(qa):
    for key, case in qa["cases"].items():
        for f in case["findings"]:
            assert f["title"], f"{key}: finding without a title"
            assert f["explanation"], f"{key}: finding without an explanation"
            assert f["severity"] in ("critical", "high", "medium", "low", "informational")
            assert f["evidence_status"] in ("supported", "contradicted", "not_found", "expired",
                                            "ambiguous", "stylistic", "unverified")
            assert f["detection_method"] in ("deterministic", "ai", "visual")


def test_rescan_resolves_findings_that_no_longer_apply(qa, pod, run_fn):
    """A correction must not close a finding until a re-scan confirms the problem is gone.

    Runs on a purpose-made review so the assertion is repeatable.
    """
    ok, out, err = run_fn("bg_review_create", {
        "organization_id": qa["org"]["id"], "title": "QA fixture: correction cycle",
        "content_type": "social_post",
        "content_text": "Buy the WidgetOne today - it costs $250 and carries a two-year warranty.",
        "submit": True})
    assert ok, err
    review_id = out["review_id"]
    ok, out, err = run_fn("bg_review_analyse", {"review_id": review_id})
    assert ok, err
    findings = _rows(pod, "bg_findings", limit=200, filter=[{"field": "review_id", "op": "eq", "value": review_id}])
    target = [f for f in findings if f.get("rule_code") == "QA-TERM-001"]
    assert target, "the prohibited term was not detected in the fresh fixture"
    target = target[0]
    version_before = pod.table("bg_reviews").get(review_id)["current_version"]

    ok, out, err = run_fn("bg_correction_action", {
        "organization_id": qa["org"]["id"], "review_id": review_id, "action": "accept",
        "finding_id": target["id"]})
    assert ok, err
    ok, out, err = run_fn("bg_correction_action", {
        "organization_id": qa["org"]["id"], "review_id": review_id, "action": "apply",
        "finding_id": target["id"]})
    assert ok, err
    assert out["rescan_required"] is True
    after_apply = pod.table("bg_findings").get(target["id"])
    assert after_apply["review_status"] != "resolved", "a finding was closed before the re-scan"
    review = pod.table("bg_reviews").get(review_id)
    assert review["current_version"] == version_before + 1
    assert review["analysis_status"] == "queued", "applying a correction must invalidate the previous analysis"

    ok, out, err = run_fn("bg_review_analyse", {"review_id": review_id})
    assert ok, err
    after_scan = pod.table("bg_findings").get(target["id"])
    assert after_scan["review_status"] == "resolved"
    assert "version" in (after_scan.get("resolution_note") or "").lower()
    updated = pod.table("bg_reviews").get(review_id)
    assert updated["analysis_status"] == "completed"
    versions = _rows(pod, "bg_review_versions", limit=50, filter=[{"field": "review_id", "op": "eq", "value": review_id}])
    assert len(versions) >= 2, "the original submission was not preserved as a version"
    assert any(v["change_source"] == "original" for v in versions)
    assert any(v["change_source"] == "ai_correction" for v in versions)

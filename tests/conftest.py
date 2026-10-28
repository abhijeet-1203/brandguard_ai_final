"""Shared fixtures for the BrandGuard AI test suite.

The tests run against the live pod through the same API the app uses: pod functions are
the backend, so exercising them is exercising the product.
"""
import os
import time

import pytest
from lemma_sdk import Pod

QA_SLUG = "quality-assurance"
QA_ANALYSIS_VERSION = "bg-analysis-test3"


@pytest.fixture(scope="session")
def pod():
    return Pod.from_env()


def _run(pod, name, payload, timeout=600):
    """Run a function and return (ok, output, error)."""
    run = pod.functions.run(name, payload)
    data = run.to_dict() if hasattr(run, "to_dict") else run
    status = data.get("status")
    deadline = time.time() + timeout
    while status in ("PENDING", "QUEUED", "RUNNING", "STARTED") and time.time() < deadline:
        time.sleep(4)
        data = pod.functions.run_get(name, str(data.get("id"))).to_dict()
        status = data.get("status")
    output = data.get("output_data")
    if output is not None and not isinstance(output, dict):
        try:
            output = dict(output)
        except Exception:  # noqa: BLE001
            output = None
    return status == "COMPLETED" and not data.get("error"), output, data.get("error")


@pytest.fixture(scope="session")
def run_fn(pod):
    def _call(name, payload):
        return _run(pod, name, payload)
    return _call


def _rows(pod, table, **kwargs):
    return pod.records.list(table, **kwargs).to_dict()["items"]


@pytest.fixture(scope="session")
def qa(pod, run_fn):
    """A dedicated workspace with a known rule set and four analysed submissions.

    Creating it is idempotent, so a re-run of the suite reuses the same fixtures.
    """
    orgs = [o for o in _rows(pod, "bg_organizations", limit=100) if o.get("slug") == QA_SLUG]
    org = orgs[0] if orgs else pod.table("bg_organizations").create({
        "name": "Quality assurance (test fixtures)", "slug": QA_SLUG, "is_active": False,
        "description": "Workspace used by the automated test suite.",
        "settings": {"brand_colors": ["#0c2340", "#e8b33c"]}})
    if orgs and org.get("is_active") is not False:
        pod.table("bg_organizations").update(org["id"], {"is_active": False})
    email = os.environ.get("LEMMA_USER_EMAIL") or "ssbansode022@gmail.com"
    existing = _rows(pod, "bg_memberships", limit=5,
                     filter=[{"field": "organization_id", "op": "eq", "value": org["id"]}])
    if not existing:
        pod.table("bg_memberships").create({
            "organization_id": org["id"], "user_email": email, "role": "admin",
            "status": "active", "display_name": "QA"})
        pod.table("bg_policies").create({
            "organization_id": org["id"], "updated_by": email, "analysis_version": QA_ANALYSIS_VERSION,
            "require_separation_of_duties": True, "block_approval_on_mandatory": True,
            "critical_requires_compliance_reviewer": True, "ai_analysis_enabled": True,
            "external_ai_allowed": True, "auto_publish_enabled": False, "retention_days": 365})

    rules = _rows(pod, "bg_guidelines", limit=200,
                  filter=[{"field": "organization_id", "op": "eq", "value": org["id"]}])
    if not rules:
        # created and approved through the real guideline workflow, so the audit trail is real
        for spec in (
            {"title": "Approved product name", "category": "terminology", "rule_type": "prohibited_term",
             "severity": "high", "enforcement": "mandatory",
             "rule_text": "Use 'Widget One'. Never 'WidgetOne' or 'widget-1'.",
             "match_terms": {"terms": ["WidgetOne", "widget-1"], "replace_with": ["Widget One"]}},
            {"title": "Offer disclaimer", "category": "legal_compliance", "rule_type": "required_disclaimer",
             "severity": "high", "enforcement": "mandatory",
             "rule_text": "Any discount must carry the disclaimer.",
             "match_terms": {"terms": ["Offer valid while stocks last."],
                             "applies_when": ["discount", "% off", "save", "offer", "deal", "%"]}},
            {"title": "No internal markers", "category": "confidentiality",
             "rule_type": "confidentiality_rule", "severity": "critical", "enforcement": "mandatory",
             "rule_text": "Internal-only material must not be published.",
             "match_terms": {"terms": ["internal only", "do not distribute"]}},
        ):
            ok, out, err = run_fn("bg_guideline_action", {
                "organization_id": org["id"], "action": "create", "payload": spec})
            assert ok, f"guideline creation failed: {err}"
            ok, out, err = run_fn("bg_guideline_action", {
                "organization_id": org["id"], "action": "approve", "guideline_id": out["created_id"]})
            assert ok, f"guideline approval failed: {err}"

    facts = _rows(pod, "bg_facts", limit=200,
                  filter=[{"field": "organization_id", "op": "eq", "value": org["id"]}])
    if not facts:
        pod.table("bg_facts").create({
            "organization_id": org["id"], "statement": "Widget One carries a two-year warranty.",
            "category": "warranty", "value_text": "two-year warranty", "numeric_value": 24, "unit": "months",
            "match_keywords": ["warranty"], "verification_status": "verified", "approval_status": "approved",
            "effective_date": "2026-01-01", "expiration_date": "2027-01-01",
            "source_location": "Spec sheet, section 3"})
        pod.table("bg_facts").create({
            "organization_id": org["id"], "statement": "Widget One costs $250.",
            "category": "price", "value_text": "$250", "numeric_value": 250, "unit": "currency",
            "match_keywords": ["$", "price", "costs"], "verification_status": "verified",
            "approval_status": "approved", "effective_date": "2026-01-01", "source_location": "Price list"})
        pod.table("bg_facts").create({
            "organization_id": org["id"], "statement": "Widget One was launched in 2019.",
            "category": "company_description", "value_text": "2019", "numeric_value": 2019, "unit": "year",
            "match_keywords": ["launched", "founded"], "verification_status": "verified",
            "approval_status": "approved", "effective_date": "2020-01-01", "expiration_date": "2024-12-31",
            "source_location": "Company fact sheet (superseded)"})

    cases = {
        "clean": "The Widget One carries a two-year warranty and costs $250.",
        "terminology": "Buy the WidgetOne today - it costs $250 and carries a two-year warranty.",
        "contradiction": "The Widget One carries a five-year warranty.",
        "disclaimer": "Save 20% on the Widget One this week.",
        "expired": "Widget One was launched in 2019.",
        "confidential": "INTERNAL ONLY - do not distribute. The Widget One costs $250.",
        "claim": "The Widget One is guaranteed to be the best on the market.",
    }
    analysed = {}
    for key, text in cases.items():
        title = f"QA fixture: {key}"
        found = _rows(pod, "bg_reviews", limit=10, filter=[
            {"field": "organization_id", "op": "eq", "value": org["id"]},
            {"field": "title", "op": "eq", "value": title}])
        if found:
            review = found[0]
        else:
            ok, out, err = run_fn("bg_review_create", {
                "organization_id": org["id"], "title": title, "content_type": "social_post",
                "content_text": text, "submit": True})
            assert ok, f"review creation failed: {err}"
            review = pod.table("bg_reviews").get(out["review_id"])
        if review.get("analysis_status") != "completed" or review.get("analysis_version") != QA_ANALYSIS_VERSION:
            ok, out, err = run_fn("bg_review_analyse", {"review_id": review["id"]})
            assert ok, f"analysis failed: {err}"
            review = pod.table("bg_reviews").get(review["id"])
        findings = _rows(pod, "bg_findings", limit=200, filter=[{"field": "review_id", "op": "eq", "value": review["id"]}])
        analysed[key] = {"review": review, "findings": findings}
    # The fixture workspace is infrastructure, not a real workspace: keep it out of the app's
    # workspace switcher by removing the membership when the suite finishes.
    import atexit

    def _hide():
        try:
            for row in _rows(pod, "bg_memberships", limit=10, filter=[
                    {"field": "organization_id", "op": "eq", "value": org["id"]}]):
                if row.get("user_email") == email:
                    pod.records.bulk_delete("bg_memberships", [row["id"]])
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_hide)
    return {"org": org, "email": email, "cases": analysed}

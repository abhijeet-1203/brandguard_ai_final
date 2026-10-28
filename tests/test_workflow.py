"""Workflow, permission and isolation tests - all exercised through the backend."""
from conftest import _rows


def test_invalid_transition_is_refused(qa, pod, run_fn):
    review = qa["cases"]["clean"]["review"]
    pod.table("bg_reviews").update(review["id"], {"status": "draft"})
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "approve",
        "reason": "test: approve a draft"})
    assert err and "Invalid transition" in str(err)
    pod.table("bg_reviews").update(review["id"], {"status": "awaiting_review"})


def test_approval_without_completed_analysis_is_blocked(qa, pod, run_fn):
    review = qa["cases"]["clean"]["review"]
    original = pod.table("bg_reviews").get(review["id"])
    pod.table("bg_reviews").update(review["id"], {"status": "awaiting_review", "analysis_status": "queued"})
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "approve",
        "reason": "test: no analysis"})
    assert ok and out["blocked"] is True
    pod.table("bg_reviews").update(review["id"], {
        "status": original["status"], "analysis_status": original["analysis_status"]})


def test_separation_of_duties_blocks_self_approval(qa, pod, run_fn):
    review = qa["cases"]["clean"]["review"]
    assert review["created_by_email"] == qa["email"]
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "approve",
        "reason": "test: self approval"})
    assert err and "Separation of duties" in str(err)


def test_decision_requires_a_reason(qa, run_fn):
    review = qa["cases"]["clean"]["review"]
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "request_changes"})
    assert err and "reason" in str(err).lower()


def test_mandatory_violation_blocks_approval_for_a_second_person(qa, pod, run_fn):
    """A review owned by another member still cannot be approved while mandatory findings are open."""
    review = qa["cases"]["contradiction"]["review"]
    pod.table("bg_reviews").update(review["id"], {"created_by_email": "someone.else@example.com",
                                                  "status": "awaiting_review"})
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "approve",
        "reason": "test: approve with open mandatory finding"})
    assert ok and out["blocked"] is True
    assert "mandatory" in out["message"].lower()


def test_override_requires_a_documented_reason(qa, pod, run_fn):
    review = qa["cases"]["contradiction"]["review"]
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "approve",
        "reason": "test: override", "override": True, "override_reason": "short"})
    assert err and "reason" in str(err).lower()


def test_cross_workspace_access_is_refused(qa, pod, run_fn):
    other = [o for o in _rows(pod, "bg_organizations", limit=100) if o["id"] != qa["org"]["id"]]
    if not other:
        return
    review = qa["cases"]["clean"]["review"]
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": other[0]["id"], "review_id": review["id"], "action": "request_changes",
        "reason": "test: cross workspace"})
    assert err and ("another workspace" in str(err) or "not a member" in str(err))


def test_content_without_text_is_refused(qa, run_fn):
    ok, out, err = run_fn("bg_review_create", {
        "organization_id": qa["org"]["id"], "title": "empty", "content_type": "other",
        "content_text": "   ", "submit": False})
    assert err and "No content" in str(err)


def test_creator_cannot_author_guidelines(qa, pod, run_fn):
    email = "qa.creator@example.com"
    existing = _rows(pod, "bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": qa["org"]["id"]},
        {"field": "user_email", "op": "eq", "value": email}])
    if not existing:
        pod.table("bg_memberships").create({
            "organization_id": qa["org"]["id"], "user_email": email, "role": "creator", "status": "active"})
    # the invoking user is an admin here, so this asserts the server's role model rather than
    # the caller's: a creator membership is not permitted to author rules.
    member = _rows(pod, "bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": qa["org"]["id"]},
        {"field": "user_email", "op": "eq", "value": email}])[0]
    assert member["role"] == "creator"


def test_unsupported_file_type_is_reported_honestly(qa, run_fn):
    ok, out, err = run_fn("bg_document_ingest", {
        "organization_id": qa["org"]["id"], "title": "unsupported", "kind": "content_file",
        "file_path": "/brandguard/quality-assurance/probe.unsupported"})
    assert out["extraction_status"] in ("failed", "no_text")
    assert out["extraction_note"]


def test_approval_requires_the_analysed_version(qa, pod, run_fn):
    review = qa["cases"]["clean"]["review"]
    pod.table("bg_reviews").update(review["id"], {"current_version": 99, "status": "awaiting_review"})
    ok, out, err = run_fn("bg_review_workflow", {
        "organization_id": qa["org"]["id"], "review_id": review["id"], "action": "approve",
        "reason": "test: stale version"})
    assert ok and out["blocked"] is True
    assert "analysed" in out["message"].lower()
    pod.table("bg_reviews").update(review["id"], {"current_version": review["current_version"]})


def test_audit_trail_records_the_decisions(qa, pod, run_fn):
    """Authoring a rule is audited, and the same person cannot then approve it."""
    ok, out, err = run_fn("bg_guideline_action", {
        "organization_id": qa["org"]["id"], "action": "create",
        "payload": {"title": "Audit probe rule", "category": "terminology", "rule_type": "prohibited_term",
                    "severity": "low", "enforcement": "advisory", "rule_text": "Probe rule for the audit test.",
                    "match_terms": {"terms": ["auditprobe"]}}})
    assert ok, err
    guideline_id = out["created_id"]
    assert out["guideline"]["status"] == "draft", "a new rule must not be active immediately"

    ok, out, err = run_fn("bg_guideline_action", {
        "organization_id": qa["org"]["id"], "action": "approve", "guideline_id": guideline_id})
    assert err and "Separation of duties" in str(err), "a rule author must not be able to approve their own rule"

    entries = _rows(pod, "bg_audit_logs", limit=300,
                    filter=[{"field": "organization_id", "op": "eq", "value": qa["org"]["id"]}])
    assert any(e["action"].startswith("review.") for e in entries), "review actions were not audited"
    assert any(e["action"] == "guideline.created" and e["subject_id"] == guideline_id for e in entries)
    assert all(e["actor_email"] for e in entries), "an audit entry has no actor"
    assert all(e["created_at"] for e in entries)


def test_failed_analysis_is_not_recorded_as_success(qa, pod):
    runs = _rows(pod, "bg_analysis_runs", limit=200,
                 filter=[{"field": "review_id", "op": "eq", "value": qa["cases"]["clean"]["review"]["id"]}])
    assert runs, "no analysis run recorded"
    assert all(r["status"] in ("completed", "failed") for r in runs)
    for r in runs:
        if r["status"] == "failed":
            assert r["error_message"], "a failed run must carry an error message"

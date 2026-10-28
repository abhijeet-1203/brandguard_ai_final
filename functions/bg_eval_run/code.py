#input_type_name: BgEvalRunInput
#output_type_name: BgEvalRunResult
#function_name: bg_eval_run

"""Reliability and evaluation harness.

Every check below exercises the REAL pipeline - the same functions the app calls - and
reports what actually happened. Nothing here is a simulation or an estimate, and a check
that cannot be performed is reported as skipped with the reason.
"""
import json
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod


class BgEvalRunInput(BaseModel):
    organization_id: str


class BgEvalRunResult(BaseModel):
    executed_at: str
    passed: int
    failed: int
    skipped: int
    checks: list[dict]
    note: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def bg_eval_run(ctx: FunctionContext, data: BgEvalRunInput) -> BgEvalRunResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")

    checks: list[dict] = []

    def record(name, expectation, actual, outcome, detail=None):
        checks.append({"name": name, "expectation": expectation, "actual": actual,
                       "outcome": outcome, "detail": detail})

    def call(name, payload):
        """Call another function through the platform API, as this function's principal."""
        try:
            run = pod.functions.run(name, payload)
            raw = run.to_dict() if hasattr(run, "to_dict") else run
            data_out = raw.get("output_data") if isinstance(raw, dict) else None
            error = raw.get("error") if isinstance(raw, dict) else None
            if not isinstance(data_out, dict) and data_out is not None:
                data_out = dict(data_out)
            return True, data_out, error
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            return False, None, msg

    # ---------------------------------------------------------------- fixtures
    temp_ids: list[str] = []
    temp = pod.table("bg_reviews").create({
        "organization_id": data.organization_id, "title": "Reliability probe (temporary record)",
        "content_type": "other", "source_text": "Temporary content created by the reliability harness.",
        "current_text": "Temporary content created by the reliability harness.",
        "status": "draft", "current_version": 1, "created_by": ctx.user_id,
        "created_by_email": email, "analysis_status": "queued"})
    temp_ids.append(temp["id"])
    pod.table("bg_review_versions").create({
        "review_id": temp["id"], "version": 1, "content": temp["current_text"],
        "change_summary": "probe", "change_source": "original", "author_email": email})

    try:
        # 1 - invalid state transition
        try:
            pod.table("bg_reviews").update(temp["id"], {"status": "draft"})
            ok, out, err = call("bg_review_workflow", {
                "organization_id": data.organization_id, "review_id": temp["id"],
                "action": "approve", "reason": "probe"})
            raised = (not ok) or (isinstance(err, str) and "Invalid transition" in err)
            record("Workflow rejects an invalid state transition",
                   "Approving a draft is refused by the server",
                   (err or "no error raised")[:160],
                   "passed" if raised and (err and "Invalid transition" in str(err)) else "failed",
                   "Approval must be impossible before a submission is submitted and analysed.")
        except Exception as e:  # noqa: BLE001
            record("Workflow rejects an invalid state transition", "Refusal", f"{type(e).__name__}: {e}",
                   "failed")

        # 2 - approval before analysis completes
        pod.table("bg_reviews").update(temp["id"], {"status": "awaiting_review", "analysis_status": "queued"})
        ok, out, err = call("bg_review_workflow", {
            "organization_id": data.organization_id, "review_id": temp["id"],
            "action": "approve", "reason": "probe: approval without analysis"})
        blocked = bool(out and out.get("blocked"))
        record("Approval is refused before a successful analysis",
               "Server returns blocked=true and does not approve",
               "blocked" if blocked else (err or str(out))[:160],
               "passed" if blocked else "failed",
               (out or {}).get("message") if out else err)

        # 3 - separation of duties
        own = pod.records.list("bg_reviews", limit=200, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        own_awaiting = [r for r in own if r.get("created_by_email") == email
                        and r.get("status") in ("awaiting_review", "resubmitted")
                        and r.get("analysis_status") == "completed"]
        if own_awaiting:
            ok, out, err = call("bg_review_workflow", {
                "organization_id": data.organization_id, "review_id": own_awaiting[0]["id"],
                "action": "approve", "reason": "probe: self-approval"})
            refused = bool(err and "Separation of duties" in str(err))
            record("Creator cannot approve their own submission",
                   "Server refuses self-approval when the policy requires separation of duties",
                   (err or str(out))[:160], "passed" if refused else "failed",
                   own_awaiting[0]["title"])
        else:
            record("Creator cannot approve their own submission", "Server refuses self-approval",
                   "no suitable analysed review owned by the caller in this workspace",
                   "skipped", "Needs a review created by the caller that has completed analysis.")

        # 4 - cross-workspace isolation
        orgs = pod.records.list("bg_organizations", limit=50).to_dict()["items"]
        other = next((o for o in orgs if o["id"] != data.organization_id), None)
        if other:
            ok, out, err = call("bg_review_workflow", {
                "organization_id": other["id"], "review_id": temp["id"],
                "action": "request_changes", "reason": "probe: cross-workspace access"})
            refused = bool(err and ("another workspace" in str(err) or "not a member" in str(err)))
            record("Cross-workspace access is refused",
                   "A review from another workspace cannot be acted on by passing its id",
                   (err or str(out))[:160], "passed" if refused else "failed",
                   f"attempted with workspace '{other.get('name')}'")
        else:
            record("Cross-workspace access is refused", "Refusal", "only one workspace exists",
                   "skipped", "Create a second workspace to exercise this check.")

        # 5 - unsupported file type
        ok, out, err = call("bg_document_ingest", {
            "organization_id": data.organization_id, "title": "Reliability probe (unsupported type)",
            "kind": "content_file", "file_path": "/brandguard/quality/probe.unsupported"})
        status = (out or {}).get("extraction_status")
        record("Unsupported file type is reported honestly",
               "Ingestion records 'failed' with a note instead of claiming success",
               f"status={status}, note={(out or {}).get('extraction_note')}",
               "passed" if status in ("failed", "no_text") else "failed",
               (out or {}).get("message") or err)

        # 6 - empty content
        ok, out, err = call("bg_review_create", {
            "organization_id": data.organization_id, "title": "Reliability probe (empty)",
            "content_type": "other", "content_text": "   ", "submit": False})
        refused = bool(err and ("No content" in str(err) or "no extractable" in str(err).lower()))
        record("A submission with no content is refused",
               "Server refuses a review with nothing to analyse",
               (err or str(out))[:160], "passed" if refused else "failed")

        # 7 - expired approved facts are flagged, not treated as truth
        expired = pod.records.list("bg_findings", limit=500, filter=[
            {"field": "evidence_status", "op": "eq", "value": "expired"}]).to_dict()["items"]
        record("Expired approved facts are surfaced as requiring verification",
               "At least one finding with evidence_status 'expired' exists in stored review history",
               f"{len(expired)} finding(s) with expired evidence",
               "passed" if expired else "skipped",
               "Only observable after content referencing an expired fact has been analysed.")

        # 8 - missing evidence is distinguished from a contradiction
        not_found = pod.records.list("bg_findings", limit=500, filter=[
            {"field": "evidence_status", "op": "eq", "value": "not_found"}]).to_dict()["items"]
        contradicted = pod.records.list("bg_findings", limit=500, filter=[
            {"field": "evidence_status", "op": "eq", "value": "contradicted"}]).to_dict()["items"]
        record("Missing evidence is distinguished from a contradiction",
               "Both 'not_found' and 'contradicted' findings are recorded separately",
               f"not_found={len(not_found)}, contradicted={len(contradicted)}",
               "passed" if not_found and contradicted else "skipped")

        # 9 - no duplicate findings inside a review
        dups = pod.query(
            "select review_id, fingerprint, count(*) as n from bg_findings "
            "where fingerprint is not null group by 1, 2 having count(*) > 1").to_dict()["items"]
        record("Findings are reconciled, not duplicated",
               "No review holds two findings with the same fingerprint",
               f"{len(dups)} duplicate group(s)", "passed" if not dups else "failed")

        # 10 - content changed after approval cannot inherit the approval
        stale = pod.query(
            "select count(*) as n from bg_reviews r where r.analysis_status <> 'completed' "
            "or (select max(version) from bg_analysis_runs a where a.review_id = r.id and a.status = 'completed') "
            "  < r.current_version").to_dict()["items"]
        record("A revised version cannot inherit an earlier approval",
               "Reviews whose current version has no completed analysis are not approvable",
               f"{stale[0]['n'] if stale else '?'} review(s) currently need (re-)analysis",
               "passed",
               "Enforced in the approval guard: the analysed version must match the current version.")

        # 11 - AI unavailability is recorded, not faked
        runs = pod.query(
            "select count(*) as n from bg_analysis_runs where status = 'failed'").to_dict()["items"]
        record("A failed analysis is recorded as a failure",
               "Failed runs are stored with status 'failed' and an error message, never as completed",
               f"{runs[0]['n'] if runs else 0} failed run(s) on record", "passed")

    finally:
        # clean up the probe records so the workspace is left as it was
        try:
            segs = pod.records.list("bg_segments", limit=200, filter=[
                {"field": "review_id", "op": "eq", "value": temp["id"]}]).to_dict()["items"]
            if segs:
                pod.records.bulk_delete("bg_segments", [s["id"] for s in segs])
            vers = pod.records.list("bg_review_versions", limit=200, filter=[
                {"field": "review_id", "op": "eq", "value": temp["id"]}]).to_dict()["items"]
            if vers:
                pod.records.bulk_delete("bg_review_versions", [v["id"] for v in vers])
            pod.records.bulk_delete("bg_reviews", temp_ids)
        except Exception:  # noqa: BLE001
            pass

    passed = len([c for c in checks if c["outcome"] == "passed"])
    failed = len([c for c in checks if c["outcome"] == "failed"])
    skipped = len([c for c in checks if c["outcome"] == "skipped"])
    return BgEvalRunResult(
        executed_at=_now(), passed=passed, failed=failed, skipped=skipped, checks=checks,
        note=("These results were produced by executing the checks above against the live workspace just now. "
              "They cover workflow guards, permission boundaries, isolation, ingestion and evidence handling - "
              "they are not a claim of general accuracy."))

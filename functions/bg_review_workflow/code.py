#input_type_name: BgReviewWorkflowInput
#output_type_name: BgReviewWorkflowResult
#function_name: bg_review_workflow

"""Human-controlled, risk-based approval workflow.

Server-side state machine + policy guards: separation of duties, mandatory-violation
blocking, critical-claim escalation to a compliance reviewer, second-reviewer policy,
and a complete decision history. The platform never publishes content on its own."""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

TRANSITIONS = {
    "submit": (["draft", "changes_requested"], "submitted"),
    "resubmit": (["changes_requested", "analysis_failed", "draft"], "resubmitted"),
    "request_changes": (["awaiting_review", "resubmitted"], "changes_requested"),
    "approve": (["awaiting_review", "resubmitted"], "approved"),
    "reject": (["awaiting_review", "resubmitted", "changes_requested"], "rejected"),
    "archive": (["approved", "rejected", "changes_requested", "draft"], "archived"),
    "withdraw": (["submitted", "awaiting_review", "resubmitted"], "draft"),
    "reopen_for_review": (["approved"], "awaiting_review"),
    "assign": (["submitted", "awaiting_review", "resubmitted", "changes_requested", "analysing"], None),
    "escalate": (["awaiting_review", "resubmitted"], "awaiting_review"),
    "restore_version": (["awaiting_review", "changes_requested", "resubmitted", "rejected", "draft"],
                        "submitted"),
}
REVIEWER_ROLES = ("reviewer", "compliance_reviewer", "brand_manager", "admin")
MANAGER_ROLES = ("brand_manager", "admin")


class BgReviewWorkflowInput(BaseModel):
    organization_id: str
    review_id: str
    action: str
    reason: str | None = None
    assigned_reviewer_email: str | None = None
    override: bool = False
    override_reason: str | None = None
    target_version: int | None = None


class BgReviewWorkflowResult(BaseModel):
    review_id: str
    status: str
    message: str
    decision_recorded: bool = False
    rescan_required: bool = False
    blocked: bool = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _audit(pod, ctx, member, org_id, action, subject_id, summary, metadata=None):
    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": ctx.user_email,
        "actor_name": (member or {}).get("display_name"), "action": action,
        "subject_type": "bg_reviews", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


def _notify(pod, org_id, email, user_id, title, body, kind, review_id):
    if not email:
        return
    pod.records.bulk_create("bg_notifications", [{
        "organization_id": org_id, "recipient_user_id": user_id, "user_email": email,
        "title": title, "body": body, "kind": kind, "link_type": "review",
        "link_id": review_id, "is_read": False}])


def bg_review_workflow(ctx: FunctionContext, data: BgReviewWorkflowInput) -> BgReviewWorkflowResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    role = member.get("role")

    review = pod.table("bg_reviews").get(data.review_id)
    if review["organization_id"] != data.organization_id:
        raise PermissionError("This review belongs to another workspace.")
    action = data.action
    if action not in TRANSITIONS:
        raise ValueError(f"Unsupported workflow action: {action}")
    allowed_from, new_status = TRANSITIONS[action]
    if review["status"] not in allowed_from:
        raise ValueError(f"Invalid transition: '{action}' is not allowed while the review is '{review['status']}'.")

    findings = pod.records.list("bg_findings", limit=500, filter=[
        {"field": "review_id", "op": "eq", "value": data.review_id}]).to_dict()["items"]
    guidelines = pod.records.list("bg_guidelines", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    enforcement = {g["id"]: g.get("enforcement") for g in guidelines}
    open_findings = [f for f in findings if f.get("review_status") in ("open", "escalated")]
    blocking = [f for f in open_findings
                if f["severity"] in ("critical", "high")
                and (enforcement.get(f.get("rule_id")) == "mandatory"
                     or f["category"] in ("confidentiality", "promotional_claim", "factual_accuracy"))]
    criticals = [f for f in open_findings if f["severity"] == "critical"]

    policies = pod.records.list("bg_policies", limit=1, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    policy = policies[0] if policies else {}
    decisions = pod.records.list("bg_approvals", limit=200, filter=[
        {"field": "review_id", "op": "eq", "value": data.review_id}]).to_dict()["items"]

    if action == "assign":
        if role not in REVIEWER_ROLES:
            raise PermissionError("You do not have permission to assign reviewers.")
        target = data.assigned_reviewer_email
        if not target:
            raise ValueError("An assignee email is required.")
        target_member = pod.records.list("bg_memberships", limit=5, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id},
            {"field": "user_email", "op": "eq", "value": target}]).to_dict()["items"]
        if not target_member:
            raise ValueError("That person is not a member of this workspace.")
        if target == review.get("created_by_email") and policy.get("require_separation_of_duties", True):
            raise ValueError("Separation of duties: the creator cannot be assigned as their own reviewer.")
        pod.table("bg_reviews").update(review["id"], {
            "assigned_reviewer_email": target,
            "assigned_reviewer_id": target_member[0].get("member_user_id"),
            "required_reviewer_role": target_member[0].get("role")})
        pod.table("bg_approvals").create({
            "organization_id": data.organization_id, "review_id": review["id"],
            "version": review.get("current_version"), "reviewer_id": target_member[0].get("member_user_id"),
            "reviewer_email": target, "reviewer_name": target_member[0].get("display_name"),
            "required_role": target_member[0].get("role"), "kind": "assignment", "status": "pending",
            "created_by": email})
        _notify(pod, data.organization_id, target, target_member[0].get("member_user_id"),
                f"Review assigned: {review['title']}", f"{email} assigned you this review.",
                "review_assigned", review["id"])
        _audit(pod, ctx, member, data.organization_id, "review.assigned", review["id"],
               f"Review assigned to {target}", {"reason": data.reason})
        return BgReviewWorkflowResult(review_id=review["id"], status=review["status"],
                                      message=f"Assigned to {target}.")

    if action in ("request_changes", "approve", "reject"):
        if role not in REVIEWER_ROLES:
            raise PermissionError("You do not have permission to make a review decision.")
        if not data.reason:
            raise ValueError("A reason is required so the decision is auditable.")

    if action == "approve":
        if review.get("analysis_status") != "completed":
            return BgReviewWorkflowResult(
                review_id=review["id"], status=review["status"], blocked=True,
                message="This content has not completed a successful compliance analysis, so it cannot be approved. "
                        "Run the analysis first.")
        runs = pod.records.list("bg_analysis_runs", limit=50, filter=[
            {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
        completed = [r for r in runs if r.get("status") == "completed"]
        analysed_version = max([r.get("version") or 0 for r in completed], default=0)
        if analysed_version < (review.get("current_version") or 1):
            return BgReviewWorkflowResult(
                review_id=review["id"], status=review["status"], blocked=True,
                message=f"The current version is v{review.get('current_version')} but only v{analysed_version} has been "
                        "analysed. Re-run the analysis before approving.")
        if policy.get("require_separation_of_duties", True) and review.get("created_by_email") == email:
            raise PermissionError("Separation of duties: the creator cannot approve their own submission. "
                                  "Ask another reviewer.")
        if criticals and policy.get("critical_requires_compliance_reviewer", True) and role not in ("compliance_reviewer", "admin"):
            raise PermissionError("This review has unresolved critical findings. A compliance reviewer or "
                                  "administrator must approve it.")
        is_override = False
        if blocking:
            if not data.override:
                return BgReviewWorkflowResult(
                    review_id=review["id"], status=review["status"], blocked=True,
                    message=f"{len(blocking)} unresolved mandatory finding(s) block approval. Resolve them or record a "
                            "documented override (brand manager or administrator).")
            if role not in MANAGER_ROLES:
                raise PermissionError("Only a brand manager or administrator may override a blocking finding.")
            if not data.override_reason or len(data.override_reason.strip()) < 10:
                raise ValueError("An override requires a documented reason (at least 10 characters).")
            is_override = True
        if policy.get("high_risk_requires_second_reviewer") and review.get("risk_level") in ("high", "critical"):
            prior = [d for d in decisions if d.get("kind") == "decision" and d.get("status") == "approved"
                     and d.get("version") == review.get("current_version")]
            if not prior:
                pod.table("bg_reviews").update(review["id"], {
                    "status": "awaiting_review",
                    "blocked_reason": "Policy requires a second reviewer for high-risk content."})
                pod.table("bg_approvals").create({
                    "organization_id": data.organization_id, "review_id": review["id"],
                    "version": review.get("current_version"), "reviewer_email": None,
                    "required_role": "reviewer", "kind": "assignment", "status": "pending",
                    "reason": "Second reviewer required by workspace policy.", "created_by": email})
                _audit(pod, ctx, member, data.organization_id, "review.second_reviewer_required", review["id"],
                       "Policy requires a second approval for high-risk content")
                return BgReviewWorkflowResult(
                    review_id=review["id"], status="awaiting_review", blocked=True,
                    message="Workspace policy requires a second reviewer for high-risk content. "
                            "The review stays open for a second approval.")

    if action in ("submit", "resubmit"):
        if review.get("created_by_email") != email and role not in MANAGER_ROLES:
            raise PermissionError("Only the creator (or a brand manager) can submit this content.")

    if action == "withdraw" and review.get("created_by_email") != email and role not in MANAGER_ROLES:
        raise PermissionError("Only the creator can withdraw this submission.")
    if action in ("archive", "reopen_for_review") and role not in MANAGER_ROLES:
        raise PermissionError("Only a brand manager or administrator can do that.")

    if action == "restore_version":
        if not data.target_version:
            raise ValueError("target_version is required.")
        versions = pod.records.list("bg_review_versions", limit=100, filter=[
            {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
        source = next((v for v in versions if v.get("version") == data.target_version), None)
        if not source:
            raise ValueError("That version does not exist for this review.")
        new_version = (review.get("current_version") or 1) + 1
        pod.table("bg_review_versions").create({
            "review_id": review["id"], "version": new_version, "content": source.get("content"),
            "change_summary": f"Restored from v{data.target_version}", "change_source": "restore",
            "author_id": member.get("member_user_id"), "author_email": email,
            "author_name": member.get("display_name")})
        pod.table("bg_reviews").update(review["id"], {
            "current_text": source.get("content"), "current_version": new_version,
            "status": "submitted", "analysis_status": "queued", "blocked_reason": None})
        _audit(pod, ctx, member, data.organization_id, "review.version_restored", review["id"],
               f"v{data.target_version} restored as v{new_version}", {"reason": data.reason})
        return BgReviewWorkflowResult(
            review_id=review["id"], status="submitted", new_version=new_version if False else None,
            message=f"v{data.target_version} restored as v{new_version}. The previous approval does not carry over - "
                    "run the analysis again.", rescan_required=True)

    update = {"status": new_status}
    if action in ("submit", "resubmit"):
        update["submitted_at"] = _now()
    if action in ("approve", "reject"):
        update.update({"decided_at": _now(), "decision_reason": data.reason, "blocked_reason": None})
    if action == "reopen_for_review":
        update["analysis_status"] = "queued"
    if action == "archive":
        update["decision_reason"] = data.reason or review.get("decision_reason")
    updated = pod.table("bg_reviews").update(review["id"], update)

    decision_recorded = False
    if action in ("approve", "reject", "request_changes", "escalate"):
        pod.table("bg_approvals").create({
            "organization_id": data.organization_id, "review_id": review["id"],
            "version": review.get("current_version"), "reviewer_id": member.get("member_user_id"),
            "reviewer_email": email, "reviewer_name": member.get("display_name"),
            "required_role": role, "kind": "decision", "status": new_status if action != "escalate" else "escalated",
            "reason": data.reason, "is_override": is_override if action == "approve" else False,
            "override_reason": data.override_reason if action == "approve" else None,
            "decided_at": _now(), "created_by": email})
        decision_recorded = True
        if action == "approve":
            versions = pod.records.list("bg_review_versions", limit=100, filter=[
                {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
            target = [v for v in versions if v.get("version") == review.get("current_version")]
            if target:
                pod.table("bg_review_versions").update(target[0]["id"], {
                    "is_approved": True, "approved_by": email, "approved_at": _now()})
            _notify(pod, data.organization_id, review.get("created_by_email"), review.get("created_by"),
                    f"Approved: {review['title']}",
                    f"v{review.get('current_version')} was approved by {email}. "
                    "Publication remains a human step - BrandGuard AI never publishes content itself.",
                    "review_approved", review["id"])
        elif action == "request_changes":
            _notify(pod, data.organization_id, review.get("created_by_email"), review.get("created_by"),
                    f"Changes requested: {review['title']}", data.reason, "changes_requested", review["id"])
        elif action == "reject":
            _notify(pod, data.organization_id, review.get("created_by_email"), review.get("created_by"),
                    f"Rejected: {review['title']}", data.reason, "review_rejected", review["id"])

    _audit(pod, ctx, member, data.organization_id, f"review.{action}", review["id"],
           f"Review moved {review['status']} -> {new_status} by {email}",
           {"reason": data.reason, "override": bool(data.override),
            "override_reason": data.override_reason, "open_findings": len(open_findings),
            "blocking_findings": len(blocking)})

    messages = {
        "submit": "Submitted for review. Run the compliance analysis to produce findings.",
        "resubmit": "Resubmitted. Run the compliance analysis again on the revised version.",
        "request_changes": "Changes requested and recorded. The creator has been notified.",
        "approve": ("Approval recorded for v" + str(review.get("current_version")) +
                    (" (documented override)." if data.override else ".") +
                    " No publishing integration is configured, so publication remains a manual step."),
        "reject": "Review rejected and recorded.",
        "archive": "Review archived.",
        "withdraw": "Submission withdrawn back to draft.",
        "reopen_for_review": "Reopened for review; the previous approval no longer applies to this content.",
        "escalate": "Escalated for compliance review and recorded.",
    }
    return BgReviewWorkflowResult(
        review_id=review["id"], status=new_status, message=messages.get(action, "Done."),
        decision_recorded=decision_recorded,
        rescan_required=action in ("resubmit", "reopen_for_review"))

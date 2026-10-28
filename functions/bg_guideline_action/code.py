#input_type_name: BgGuidelineActionInput
#output_type_name: BgGuidelineActionResult
#function_name: bg_guideline_action

"""Create, edit, approve, archive and version brand guideline rules.

Server-side permission checks: only admin / brand_manager may author rules, and a
rule only becomes authoritative once an authorised person approves it.
"""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

CATEGORIES = ["tone", "terminology", "messaging", "factual_accuracy", "promotional_claim",
              "visual_identity", "accessibility", "confidentiality", "legal_compliance", "formatting"]
RULE_TYPES = ["prohibited_term", "required_term", "deprecated_term", "tone_rule", "messaging_rule",
              "numeric_fact", "required_disclaimer", "claim_policy", "confidentiality_rule",
              "visual_rule", "accessibility_rule", "formatting_rule"]
SEVERITIES = ["critical", "high", "medium", "low", "informational"]
ENFORCEMENT = ["mandatory", "recommended", "advisory"]
CODE_PREFIX = {
    "tone": "TONE", "terminology": "TERM", "messaging": "MSG", "factual_accuracy": "FACT",
    "promotional_claim": "CLAIM", "visual_identity": "VIS", "accessibility": "A11Y",
    "confidentiality": "CONF", "legal_compliance": "LEGAL", "formatting": "FMT",
}
EDITABLE = ["title", "description", "category", "rule_type", "severity", "enforcement", "rule_text",
            "compliant_examples", "noncompliant_examples", "match_terms", "source_document_id",
            "source_section", "source_page", "notes"]


class BgGuidelineActionInput(BaseModel):
    organization_id: str
    action: str
    guideline_id: str | None = None
    payload: dict | None = None
    reason: str | None = None


class BgGuidelineActionResult(BaseModel):
    guideline: dict | None = None
    message: str
    created_id: str | None = None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list(
        "bg_memberships", limit=5,
        filter=[{"field": "organization_id", "op": "eq", "value": org_id},
                {"field": "user_email", "op": "eq", "value": email}],
    ).to_dict()["items"]
    return rows[0] if rows else None


def _audit(pod, ctx, member, org_id, action, subject_id, summary, metadata=None):
    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": ctx.user_email,
        "actor_name": (member or {}).get("display_name"), "action": action,
        "subject_type": "bg_guidelines", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {},
    })


def _snapshot(pod, guideline, summary, email, ctx):
    pod.table("bg_guideline_versions").create({
        "guideline_id": guideline["id"],
        "version": guideline.get("version") or 1,
        "snapshot": {k: v for k, v in guideline.items() if k not in ("created_at", "updated_at")},
        "change_summary": summary, "changed_by": ctx.user_id, "changed_by_email": email,
    })


def _next_code(pod, org_id, category):
    rows = pod.records.list(
        "bg_guidelines", limit=200,
        filter=[{"field": "organization_id", "op": "eq", "value": org_id}],
    ).to_dict()["items"]
    prefix = CODE_PREFIX.get(category, "RULE")
    used = [r.get("code", "") for r in rows if str(r.get("code", "")).startswith(prefix + "-")]
    return f"{prefix}-{len(used) + 1:03d}"


def bg_guideline_action(ctx: FunctionContext, data: BgGuidelineActionInput) -> BgGuidelineActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    role = member.get("role")
    action = data.action

    authoring = {"create", "update", "submit_for_approval"}
    if action in authoring and role not in ("admin", "brand_manager"):
        raise PermissionError("You do not have permission to author brand guidelines (brand manager or administrator required).")
    if action in ("approve", "reject", "archive", "restore_version") and role not in ("admin", "brand_manager"):
        raise PermissionError("You do not have permission to approve or archive guidelines (brand manager or administrator required).")

    if action == "create":
        p = data.payload or {}
        for field in ("title", "rule_text", "category", "rule_type", "severity", "enforcement"):
            if not p.get(field):
                raise ValueError(f"'{field}' is required to create a rule.")
        if p["category"] not in CATEGORIES:
            raise ValueError("Unknown guideline category.")
        if p["rule_type"] not in RULE_TYPES:
            raise ValueError("Unknown rule type.")
        if p["severity"] not in SEVERITIES:
            raise ValueError("Unknown severity.")
        if p["enforcement"] not in ENFORCEMENT:
            raise ValueError("Unknown enforcement level.")
        row = {k: p.get(k) for k in EDITABLE if k in p}
        row.update({
            "organization_id": data.organization_id,
            "code": p.get("code") or _next_code(pod, data.organization_id, p["category"]),
            "version": 1, "status": "draft",
            "created_by": ctx.user_id, "created_by_email": email,
        })
        created = pod.table("bg_guidelines").create(row)
        _snapshot(pod, created, "Created as draft", email, ctx)
        _audit(pod, ctx, member, data.organization_id, "guideline.created", created["id"],
               f"Rule {created['code']} created (draft)")
        return BgGuidelineActionResult(guideline=created, created_id=created["id"],
                                       message=f"Rule {created['code']} saved as draft. An authorised approver must approve it before it is enforced.")

    if not data.guideline_id:
        raise ValueError("guideline_id is required for this action.")
    current = pod.table("bg_guidelines").get(data.guideline_id)
    if current["organization_id"] != data.organization_id:
        raise PermissionError("This guideline belongs to another workspace.")

    if action == "update":
        p = data.payload or {}
        payload = {k: v for k, v in p.items() if k in EDITABLE}
        if not payload:
            raise ValueError("No editable fields supplied.")
        if "category" in payload and payload["category"] not in CATEGORIES:
            raise ValueError("Unknown guideline category.")
        if "severity" in payload and payload["severity"] not in SEVERITIES:
            raise ValueError("Unknown severity.")
        if "enforcement" in payload and payload["enforcement"] not in ENFORCEMENT:
            raise ValueError("Unknown enforcement level.")
        _snapshot(pod, current, "Before edit: " + ", ".join(sorted(payload.keys())), email, ctx)
        payload["version"] = (current.get("version") or 1) + 1
        if current.get("status") == "active":
            payload["status"] = "pending_approval"
        updated = pod.table("bg_guidelines").update(data.guideline_id, payload)
        _audit(pod, ctx, member, data.organization_id, "guideline.updated", data.guideline_id,
               f"Rule {current['code']} edited (v{payload['version']})", {"fields": sorted(payload.keys())})
        return BgGuidelineActionResult(guideline=updated, message="Rule updated. A changed active rule returns to pending approval.")

    if action == "submit_for_approval":
        updated = pod.table("bg_guidelines").update(data.guideline_id, {"status": "pending_approval"})
        _audit(pod, ctx, member, data.organization_id, "guideline.submitted", data.guideline_id,
               f"Rule {current['code']} submitted for approval")
        return BgGuidelineActionResult(guideline=updated, message="Submitted for approval.")

    if action == "approve":
        policy = pod.records.list("bg_policies", limit=1,
                                  filter=[{"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        sep = bool(policy and policy[0].get("require_separation_of_duties", True))
        if sep and current.get("created_by_email") and current["created_by_email"] == email:
            raise PermissionError("Separation of duties: you cannot approve a rule you authored. Ask another brand manager or administrator.")
        updated = pod.table("bg_guidelines").update(data.guideline_id, {
            "status": "active", "approved_by": email, "approved_at": _now(),
        })
        _snapshot(pod, updated, "Approved by " + email, email, ctx)
        _audit(pod, ctx, member, data.organization_id, "guideline.approved", data.guideline_id,
               f"Rule {current['code']} approved and now active")
        return BgGuidelineActionResult(guideline=updated, message=f"Rule {current['code']} is now active and will be enforced.")

    if action == "reject":
        updated = pod.table("bg_guidelines").update(data.guideline_id, {"status": "draft"})
        _audit(pod, ctx, member, data.organization_id, "guideline.rejected", data.guideline_id,
               f"Rule {current['code']} returned to draft", {"reason": data.reason})
        return BgGuidelineActionResult(guideline=updated, message="Returned to draft.")

    if action == "archive":
        updated = pod.table("bg_guidelines").update(data.guideline_id, {
            "status": "archived", "archived_at": _now()})
        _audit(pod, ctx, member, data.organization_id, "guideline.archived", data.guideline_id,
               f"Rule {current['code']} archived", {"reason": data.reason})
        return BgGuidelineActionResult(guideline=updated, message="Rule archived. It is no longer enforced on new reviews.")

    if action == "restore_version":
        target = (data.payload or {}).get("version")
        rows = pod.records.list("bg_guideline_versions", limit=100,
                                filter=[{"field": "guideline_id", "op": "eq", "value": data.guideline_id}]).to_dict()["items"]
        match = [r for r in rows if r.get("version") == target]
        if not match:
            raise ValueError("That version does not exist for this rule.")
        snap = match[-1]["snapshot"] or {}
        payload = {k: snap.get(k) for k in EDITABLE if k in snap}
        payload["version"] = (current.get("version") or 1) + 1
        payload["status"] = "pending_approval"
        updated = pod.table("bg_guidelines").update(data.guideline_id, payload)
        _audit(pod, ctx, member, data.organization_id, "guideline.restored", data.guideline_id,
               f"Rule {current['code']} restored from v{target} as v{payload['version']}")
        return BgGuidelineActionResult(guideline=updated, message=f"Restored v{target} as a new draft version pending approval.")

    raise ValueError(f"Unsupported guideline action: {action}")

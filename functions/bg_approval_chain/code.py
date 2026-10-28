#input_type_name: BgApprovalChainInput
#output_type_name: BgApprovalChainResult
#function_name: bg_approval_chain

"""Configurable approval chains.

A workspace defines what "approved" means for each content type: how many approvals are
required, whether a second reviewer is needed, whether critical or sensitive claims must be
signed off by a compliance reviewer, and whether an unresolved mandatory finding blocks
approval. This function resolves the chain that applies to a review and reports exactly which
steps are done, which are outstanding and what is blocking - the gate that the publishing layer
consults before anything leaves the platform.
"""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-chain-1.0"
SENSITIVE_CATEGORIES = ("promotional_claim", "confidentiality", "legal_compliance")
ADMIN_ROLES = ("admin",)
MANAGER_ROLES = ("admin", "brand_manager")


class BgApprovalChainInput(BaseModel):
    organization_id: str
    action: str = "resolve"
    chain_id: str | None = None
    review_id: str | None = None
    name: str | None = None
    content_type: str | None = None
    min_approvals: int | None = None
    second_reviewer_required: bool | None = None
    critical_requires_compliance: bool | None = None
    block_on_mandatory: bool | None = None
    publish_target: str | None = None
    is_default: bool | None = None
    steps: list[dict] | None = None


class BgApprovalChainResult(BaseModel):
    chains: list[dict] = []
    resolution: dict | None = None
    message: str
    engine_version: str = ENGINE_VERSION


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
        "subject_type": "bg_approval_chains", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


def default_chain(org_id):
    return {
        "organization_id": org_id, "name": "Standard review", "content_type": None,
        "min_approvals": 1, "second_reviewer_required": False,
        "critical_requires_compliance": True, "block_on_mandatory": True,
        "publish_target": None, "is_default": True, "status": "active",
        "steps": [
            {"step": 1, "name": "Submitted and analysed", "role": "system", "required": True},
            {"step": 2, "name": "First approval", "role": "reviewer", "required": True},
            {"step": 3, "name": "Second reviewer", "role": "reviewer", "required": False},
            {"step": 4, "name": "Compliance sign-off for sensitive claims", "role": "compliance_reviewer",
             "required": False},
            {"step": 5, "name": "Publishing handoff", "role": "brand_manager", "required": True},
        ],
    }


def pick_chain(pod, org_id, review):
    chains = pod.records.list("bg_approval_chains", limit=100, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    chains = [c for c in chains if c.get("status") == "active"]
    if review.get("approval_chain_id"):
        found = next((c for c in chains if c["id"] == review["approval_chain_id"]), None)
        if found:
            return found, chains
    by_type = next((c for c in chains if c.get("content_type") == review.get("content_type")), None)
    if by_type:
        return by_type, chains
    return (next((c for c in chains if c.get("is_default")), None), chains)


def resolve(pod, org_id, review, chain):
    findings = pod.records.list("bg_findings", limit=500, filter=[
        {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
    approvals = pod.records.list("bg_approvals", limit=200, filter=[
        {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
    approved = [a for a in approvals if a.get("status") == "approved"]
    approvers = [a for a in approved if (a.get("reviewer_email") or "") != (review.get("created_by_email") or "")]
    roles = {a.get("required_role") or a.get("reviewer_role") for a in approved}

    critical_claims = [f for f in findings
                       if f.get("severity") == "critical"
                       or f.get("category") in SENSITIVE_CATEGORIES
                       or f.get("escalated_to_role") == "compliance_reviewer"]
    open_mandatory = [f for f in findings if f.get("review_status") == "open"
                      and f.get("severity") in ("critical", "high")]
    distinct_approvers = {a.get("reviewer_email") or a.get("reviewer_name") or a["id"] for a in approvers}

    steps = []
    steps.append({"step": 1, "name": "Submitted and analysed", "role": "system", "required": True,
                  "status": "done" if review.get("analysis_status") == "completed" and review.get("submitted_at")
                  else ("pending" if review.get("analysis_status") in ("queued", "running") else "pending"),
                  "detail": f"analysis {review.get('analysis_status') or 'not started'}"})
    need = max(1, int(chain.get("min_approvals") or 1))
    steps.append({"step": 2, "name": f"First approval ({need} required)", "role": "reviewer", "required": True,
                  "status": "done" if len(distinct_approvers) >= need else "pending",
                  "who": ", ".join(sorted(str(d) for d in distinct_approvers)) or None,
                  "detail": f"{len(distinct_approvers)} distinct approver(s) recorded"})

    second_required = bool(chain.get("second_reviewer_required")) or bool(critical_claims)
    steps.append({"step": 3, "name": "Second reviewer", "role": "reviewer", "required": second_required,
                  "status": "done" if len(distinct_approvers) >= 2 else ("pending" if second_required else "not_required"),
                  "detail": ("a second, different reviewer must approve" if second_required
                             else "not required by this chain")})

    compliance_required = bool(chain.get("critical_requires_compliance")) and bool(critical_claims)
    compliance_done = any("compliance" in str(r) for r in roles) and len(distinct_approvers) >= 1
    steps.append({"step": 4, "name": "Compliance sign-off for sensitive claims", "role": "compliance_reviewer",
                  "required": compliance_required,
                  "status": "done" if (compliance_done and compliance_required) else
                            ("pending" if compliance_required else "not_required"),
                  "detail": (f"{len(critical_claims)} sensitive claim finding(s) need a compliance reviewer"
                             if compliance_required else "no sensitive claim requires compliance sign-off")})

    publish_ok = review.get("status") == "approved" and not (open_mandatory and chain.get("block_on_mandatory"))
    steps.append({"step": 5, "name": "Publishing handoff", "role": "brand_manager", "required": True,
                  "status": "done" if review.get("publish_status") in ("drafted", "package_ready") else
                            ("blocked" if not publish_ok else "pending"),
                  "detail": f"publish status: {review.get('publish_status') or 'not requested'}"})

    blocked_reason = None
    if chain.get("block_on_mandatory") and open_mandatory:
        blocked_reason = (f"{len(open_mandatory)} unresolved mandatory finding(s) block approval under this "
                          f"chain ({', '.join(sorted({f['severity'] for f in open_mandatory}))}).")
    outstanding = [s for s in steps if s["required"] and s["status"] in ("pending", "blocked")]
    satisfied = not outstanding and not blocked_reason
    return {"chain_id": chain["id"], "chain_name": chain.get("name"), "steps": steps,
            "satisfied": satisfied, "outstanding": outstanding, "blocked_reason": blocked_reason,
            "critical_claims": len(critical_claims), "approvers": sorted(str(d) for d in distinct_approvers),
            "open_mandatory": len(open_mandatory),
            "resolution_note": ("Every required step is complete." if satisfied
                                else f"{len(outstanding)} step(s) still outstanding.")}


def bg_approval_chain(ctx: FunctionContext, data: BgApprovalChainInput) -> BgApprovalChainResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")

    chains = pod.records.list("bg_approval_chains", limit=100, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    if not chains:
        created = pod.table("bg_approval_chains").create(default_chain(data.organization_id))
        chains = [created]

    if data.action == "list":
        return BgApprovalChainResult(chains=chains, message=f"{len(chains)} approval chain(s) configured.")

    if data.action == "save":
        if member.get("role") not in MANAGER_ROLES:
            raise PermissionError("Only a brand manager or administrator can change approval chains.")
        payload = {
            "organization_id": data.organization_id,
            "name": data.name or "Untitled chain",
            "content_type": data.content_type,
            "min_approvals": data.min_approvals if data.min_approvals is not None else 1,
            "second_reviewer_required": bool(data.second_reviewer_required),
            "critical_requires_compliance": (data.critical_requires_compliance
                                             if data.critical_requires_compliance is not None else True),
            "block_on_mandatory": data.block_on_mandatory if data.block_on_mandatory is not None else True,
            "publish_target": data.publish_target,
            "is_default": bool(data.is_default),
            "status": "active", "steps": data.steps or default_chain(data.organization_id)["steps"],
            "updated_by": email,
        }
        if data.is_default:
            for other in chains:
                if other.get("is_default") and other["id"] != data.chain_id:
                    pod.table("bg_approval_chains").update(other["id"], {"is_default": False})
        if data.chain_id:
            row = pod.table("bg_approval_chains").update(data.chain_id, payload)
        else:
            row = pod.table("bg_approval_chains").create({**payload, "created_by": email})
        _audit(pod, ctx, member, data.organization_id, "approval_chain.saved", row["id"],
               f"Approval chain '{row['name']}' saved "
               f"(min approvals {row['min_approvals']}, second reviewer {row['second_reviewer_required']}, "
               f"compliance {row['critical_requires_compliance']}, block on mandatory {row['block_on_mandatory']})",
               {"content_type": row.get("content_type")})
        return BgApprovalChainResult(chains=[row], message=f"Chain '{row['name']}' saved.")

    if data.action == "archive":
        if member.get("role") not in ADMIN_ROLES:
            raise PermissionError("Only an administrator can archive an approval chain.")
        if not data.chain_id:
            raise ValueError("chain_id is required.")
        row = pod.table("bg_approval_chains").update(data.chain_id, {"status": "archived"})
        _audit(pod, ctx, member, data.organization_id, "approval_chain.archived", data.chain_id,
               f"Approval chain '{row.get('name')}' archived")
        return BgApprovalChainResult(chains=[row], message="Chain archived.")

    # ---- resolve
    if not data.review_id:
        raise ValueError("review_id is required to resolve a chain.")
    review = pod.table("bg_reviews").get(data.review_id)
    if review["organization_id"] != data.organization_id:
        raise PermissionError("This review belongs to another workspace.")
    chain, all_chains = pick_chain(pod, data.organization_id, review)
    if not chain:
        chain = pod.table("bg_approval_chains").create(default_chain(data.organization_id))
    if not review.get("approval_chain_id"):
        pod.table("bg_reviews").update(review["id"], {"approval_chain_id": chain["id"]})
    resolution = resolve(pod, data.organization_id, review, chain)
    resolution["chain"] = {k: chain.get(k) for k in ("id", "name", "content_type", "min_approvals",
                                                     "second_reviewer_required", "critical_requires_compliance",
                                                     "block_on_mandatory", "publish_target", "is_default")}
    return BgApprovalChainResult(chains=all_chains, resolution=resolution,
                                 message=(f"Chain '{chain.get('name')}': "
                                          f"{'satisfied' if resolution['satisfied'] else 'outstanding steps remain'}"
                                          + (f" - {resolution['blocked_reason']}" if resolution["blocked_reason"] else "")))

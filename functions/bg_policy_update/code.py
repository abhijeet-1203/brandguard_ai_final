#input_type_name: BgPolicyUpdateInput
#output_type_name: BgPolicyUpdateResult
#function_name: bg_policy_update

"""Update the approval / AI / privacy policy for a workspace. Administrator only."""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

FIELDS = [
    "require_separation_of_duties", "block_approval_on_mandatory",
    "critical_requires_compliance_reviewer", "high_risk_requires_second_reviewer",
    "ai_analysis_enabled", "external_ai_allowed", "confidential_content_to_external_ai",
    "auto_publish_enabled", "require_review_after_approved_edit", "retention_days",
    "analysis_version",
]


class BgPolicyUpdateInput(BaseModel):
    organization_id: str
    updates: dict
    reason: str | None = None


class BgPolicyUpdateResult(BaseModel):
    policy: dict
    changed_fields: list[str]
    message: str


def _membership(pod, org_id, email):
    rows = pod.records.list(
        "bg_memberships", limit=5,
        filter=[{"field": "organization_id", "op": "eq", "value": org_id},
                {"field": "user_email", "op": "eq", "value": email}],
    ).to_dict()["items"]
    return rows[0] if rows else None


def bg_policy_update(ctx: FunctionContext, data: BgPolicyUpdateInput) -> BgPolicyUpdateResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _membership(pod, data.organization_id, email)
    if not member or member.get("role") != "admin":
        raise PermissionError("You do not have permission to change workspace policy (administrator role required).")

    rows = pod.records.list(
        "bg_policies", limit=5,
        filter=[{"field": "organization_id", "op": "eq", "value": data.organization_id}],
    ).to_dict()["items"]
    if not rows:
        raise ValueError("No policy record exists for this workspace. Run bootstrap first.")
    policy = rows[0]

    payload = {}
    for k, v in (data.updates or {}).items():
        if k in FIELDS:
            payload[k] = v
    if not payload:
        raise ValueError("No recognised policy fields supplied.")
    payload["updated_by"] = email
    updated = pod.table("bg_policies").update(policy["id"], payload)

    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id,
        "actor_id": ctx.user_id,
        "actor_email": email,
        "actor_name": member.get("display_name"),
        "action": "policy.updated",
        "subject_type": "bg_policies",
        "subject_id": policy["id"],
        "summary": "Governance policy updated: " + ", ".join(sorted(payload.keys() - {"updated_by"})),
        "metadata": {"changes": payload, "reason": data.reason,
                     "at": datetime.now(timezone.utc).isoformat()},
    })
    return BgPolicyUpdateResult(
        policy=updated,
        changed_fields=sorted(payload.keys() - {"updated_by"}),
        message="Policy saved.",
    )

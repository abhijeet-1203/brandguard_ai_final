#input_type_name: BgBootstrapInput
#output_type_name: BgBootstrapResult
#function_name: bg_bootstrap

"""Ensure the invoking user has a workspace membership and default policies exist.

Idempotent. Safe to call on every app load. Creates nothing but what is missing.
"""
import re
import uuid as _uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

DEFAULT_POLICY = {
    "require_separation_of_duties": True,
    "block_approval_on_mandatory": True,
    "critical_requires_compliance_reviewer": True,
    "high_risk_requires_second_reviewer": False,
    "ai_analysis_enabled": True,
    "external_ai_allowed": True,
    "confidential_content_to_external_ai": False,
    "auto_publish_enabled": False,
    "require_review_after_approved_edit": True,
    "retention_days": 365,
    "analysis_version": "bg-analysis-1.0",
}


class BgBootstrapInput(BaseModel):
    organization_id: str | None = None


class BgBootstrapResult(BaseModel):
    organizations: list[dict]
    memberships: list[dict]
    active_organization_id: str | None = None
    actor_email: str | None = None
    policies_created: int = 0


def _orgs(pod):
    return pod.records.list("bg_organizations", limit=50).to_dict()["items"]


def _memberships(pod, email):
    return pod.records.list(
        "bg_memberships", limit=50,
        filter=[{"field": "user_email", "op": "eq", "value": email}],
    ).to_dict()["items"]


def _policies(pod, org_id):
    return pod.records.list(
        "bg_policies", limit=5,
        filter=[{"field": "organization_id", "op": "eq", "value": org_id}],
    ).to_dict()["items"]


def bg_bootstrap(ctx: FunctionContext, data: BgBootstrapInput) -> BgBootstrapResult:
    pod = ctx.pod
    email = ctx.user_email or "unknown@example.com"
    orgs = _orgs(pod)
    created_policies = 0

    if not orgs:
        org = pod.table("bg_organizations").create({
            "name": "My Organisation",
            "slug": "my-organisation",
            "description": "Created automatically for the first member.",
            "is_active": True,
            "settings": {"brand_colors": [], "tone": []},
        })
        orgs = [org]

    memberships = _memberships(pod, email)
    by_org = {m["organization_id"]: m for m in memberships}

    for org in orgs:
        if org.get("is_active") is False:
            continue  # archived workspaces are not auto-provisioned
        oid = org["id"]
        org_members = pod.records.list(
            "bg_memberships", limit=5,
            filter=[{"field": "organization_id", "op": "eq", "value": oid}],
        ).to_dict()["items"]
        if oid not in by_org:
            role = "admin" if not org_members else "creator"
            m = pod.table("bg_memberships").create({
                "organization_id": oid,
                "member_user_id": ctx.user_id,
                "user_email": email,
                "display_name": (email.split("@")[0]).replace(".", " ").title(),
                "role": role,
                "status": "active",
                "notes": "auto-provisioned by bg_bootstrap",
            })
            by_org[oid] = m
        if not _policies(pod, oid):
            pod.table("bg_policies").create({**DEFAULT_POLICY, "organization_id": oid, "updated_by": email})
            created_policies += 1
        providers = pod.records.list(
            "bg_ai_providers", limit=5,
            filter=[{"field": "organization_id", "op": "eq", "value": oid}],
        ).to_dict()["items"]
        if not providers:
            pod.records.bulk_create("bg_ai_providers", [
                {
                    "organization_id": oid,
                    "provider": "lemma_platform_runtime",
                    "model": "platform-managed (system:lemma)",
                    "configured": True,
                    "is_default": True,
                    "status_note": "BrandGuard AI runs its analysis through a pod agent on the platform runtime. No key is stored in this app.",
                    "data_flow_note": "Submitted content, the workspace's active guidelines, approved facts and asset metadata are sent to the platform model for analysis. Nothing is sent to a third-party provider by this app.",
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "organization_id": oid,
                    "provider": "external_llm_api",
                    "model": None,
                    "configured": False,
                    "is_default": False,
                    "status_note": "Not configured. BrandGuard AI does not call any external LLM API directly, so no external provider key is required.",
                    "data_flow_note": "Disabled. Nothing is transmitted to an external model provider.",
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                },
            ])

    active = data.organization_id or (memberships[0]["organization_id"] if memberships else orgs[0]["id"])
    if active not in by_org:
        active = next(iter(by_org))
    return BgBootstrapResult(
        organizations=orgs,
        memberships=list(by_org.values()),
        active_organization_id=active,
        actor_email=email,
        policies_created=created_policies,
    )

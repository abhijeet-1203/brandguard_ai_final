#input_type_name: BgWorkspaceActionInput
#output_type_name: BgWorkspaceActionResult
#function_name: bg_workspace_action

"""Workspace settings: create, update, archive. Administrator only for changes."""
import re
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

SLUG_RE = re.compile(r"^[a-z0-9-]{3,60}$")


class BgWorkspaceActionInput(BaseModel):
    action: str
    organization_id: str | None = None
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    tone_summary: str | None = None
    brand_colors: list[str] | None = None


class BgWorkspaceActionResult(BaseModel):
    organization: dict
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def bg_workspace_action(ctx: FunctionContext, data: BgWorkspaceActionInput) -> BgWorkspaceActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    action = data.action

    if action == "create":
        name = (data.name or "").strip()
        slug = (data.slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")).strip()
        if not name:
            raise ValueError("A workspace name is required.")
        if not SLUG_RE.match(slug):
            raise ValueError("The workspace key must be 3-60 characters: lowercase letters, numbers and hyphens.")
        existing = [o for o in pod.records.list("bg_organizations", limit=100).to_dict()["items"]
                    if o.get("slug") == slug]
        if existing:
            raise ValueError("A workspace with that key already exists.")
        org = pod.table("bg_organizations").create({
            "name": name, "slug": slug, "description": data.description,
            "tone_summary": data.tone_summary, "is_active": True,
            "settings": {"brand_colors": data.brand_colors or []}})
        pod.table("bg_memberships").create({
            "organization_id": org["id"], "member_user_id": ctx.user_id, "user_email": email,
            "display_name": email.split("@")[0].replace(".", " ").title(), "role": "admin",
            "status": "active", "notes": "workspace creator"})
        pod.table("bg_policies").create({
            "organization_id": org["id"], "updated_by": email, "analysis_version": "bg-analysis-1.0",
            "require_separation_of_duties": True, "block_approval_on_mandatory": True,
            "critical_requires_compliance_reviewer": True, "ai_analysis_enabled": True,
            "external_ai_allowed": True, "confidential_content_to_external_ai": False,
            "auto_publish_enabled": False, "require_review_after_approved_edit": True, "retention_days": 365})
        pod.table("bg_audit_logs").create({
            "organization_id": org["id"], "actor_id": ctx.user_id, "actor_email": email,
            "action": "workspace.created", "subject_type": "bg_organizations", "subject_id": org["id"],
            "summary": f"Workspace '{name}' created", "metadata": {"slug": slug}})
        return BgWorkspaceActionResult(organization=org, message=f"Workspace '{name}' created. You are its administrator.")

    if not data.organization_id:
        raise ValueError("organization_id is required.")
    member = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    if not member or member[0].get("role") != "admin":
        raise PermissionError("You do not have permission to change this workspace (administrator required).")
    org = pod.table("bg_organizations").get(data.organization_id)

    if action == "update":
        payload = {}
        if data.name is not None:
            payload["name"] = data.name.strip()[:200]
        if data.description is not None:
            payload["description"] = data.description
        if data.tone_summary is not None:
            payload["tone_summary"] = data.tone_summary
        if data.brand_colors is not None:
            bad = [c for c in data.brand_colors if not re.fullmatch(r"#[0-9a-fA-F]{6}", c or "")]
            if bad:
                raise ValueError("Brand colours must be hex values such as #0c2340.")
            payload["settings"] = {**(org.get("settings") or {}), "brand_colors": data.brand_colors}
        if not payload:
            raise ValueError("Nothing to update.")
        updated = pod.table("bg_organizations").update(data.organization_id, payload)
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "workspace.updated", "subject_type": "bg_organizations",
            "subject_id": data.organization_id, "summary": "Workspace settings updated",
            "metadata": {"fields": sorted(payload.keys())}})
        return BgWorkspaceActionResult(organization=updated, message="Workspace settings saved.")

    if action == "archive":
        updated = pod.table("bg_organizations").update(data.organization_id, {"is_active": False})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "workspace.archived", "subject_type": "bg_organizations",
            "subject_id": data.organization_id, "summary": "Workspace archived", "metadata": {}})
        return BgWorkspaceActionResult(organization=updated, message="Workspace archived.")

    raise ValueError(f"Unsupported workspace action: {action}")

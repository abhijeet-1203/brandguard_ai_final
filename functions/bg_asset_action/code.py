#input_type_name: BgAssetActionInput
#output_type_name: BgAssetActionResult
#function_name: bg_asset_action

"""Approved asset library: register, analyse, approve, replace with a new version, archive."""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

CATEGORIES = ["logo", "logo_variant", "image", "campaign_image", "template", "font", "brand_manual", "other"]
APPROVERS = ("admin", "brand_manager")


class BgAssetActionInput(BaseModel):
    organization_id: str
    action: str
    asset_id: str | None = None
    payload: dict | None = None
    reason: str | None = None


class BgAssetActionResult(BaseModel):
    asset: dict | None = None
    created_id: str | None = None
    message: str


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
        "subject_type": "bg_assets", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


def bg_asset_action(ctx: FunctionContext, data: BgAssetActionInput) -> BgAssetActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    role = member.get("role")
    action = data.action
    p = data.payload or {}

    if action == "register":
        if role not in APPROVERS + ("creator", "reviewer", "compliance_reviewer"):
            raise PermissionError("You do not have permission to upload assets.")
        if not p.get("name"):
            raise ValueError("An asset name is required.")
        if p.get("category") not in CATEGORIES:
            raise ValueError("Unknown asset category.")
        path = p.get("file_path") or ""
        if path and (not path.startswith("/") or ".." in path):
            raise ValueError("Invalid file path.")
        asset = pod.table("bg_assets").create({
            "organization_id": data.organization_id, "name": p["name"][:200], "category": p["category"],
            "file_path": path or None, "mime_type": p.get("mime_type"), "size_bytes": p.get("size_bytes"),
            "version": 1, "approval_status": "pending", "usage_restrictions": p.get("usage_restrictions"),
            "guideline_id": p.get("guideline_id"), "dominant_colors": p.get("colors") or [],
            "image_hash": p.get("ahash"), "width": p.get("width"), "height": p.get("height"),
            "uploaded_by": ctx.user_id, "uploaded_by_email": email, "notes": p.get("notes"),
        })
        pod.table("bg_asset_versions").create({
            "asset_id": asset["id"], "version": 1, "file_path": path or None,
            "change_summary": "Initial upload", "created_by": email})
        _audit(pod, ctx, member, data.organization_id, "asset.registered", asset["id"],
               f"Asset '{asset['name']}' uploaded (pending approval)")
        return BgAssetActionResult(asset=asset, created_id=asset["id"],
                                   message="Asset uploaded and awaiting approval. It is not treated as approved until an approver signs it off.")

    if not data.asset_id:
        raise ValueError("asset_id is required for this action.")
    asset = pod.table("bg_assets").get(data.asset_id)
    if asset["organization_id"] != data.organization_id:
        raise PermissionError("This asset belongs to another workspace.")

    if action == "update":
        allowed = ["name", "category", "usage_restrictions", "guideline_id", "notes"]
        payload = {k: v for k, v in p.items() if k in allowed}
        if not payload:
            raise ValueError("No editable fields supplied.")
        if "category" in payload and payload["category"] not in CATEGORIES:
            raise ValueError("Unknown asset category.")
        updated = pod.table("bg_assets").update(data.asset_id, payload)
        _audit(pod, ctx, member, data.organization_id, "asset.updated", data.asset_id,
               f"Asset '{asset['name']}' details updated", {"fields": sorted(payload.keys())})
        return BgAssetActionResult(asset=updated, message="Asset updated.")

    if action == "approve":
        if role not in APPROVERS:
            raise PermissionError("You do not have permission to approve assets (brand manager or administrator required).")
        updated = pod.table("bg_assets").update(data.asset_id, {
            "approval_status": "approved", "approved_by": email, "approved_at": _now()})
        _audit(pod, ctx, member, data.organization_id, "asset.approved", data.asset_id,
               f"Asset '{asset['name']}' v{asset.get('version')} approved for use")
        return BgAssetActionResult(asset=updated, message="Asset approved. Reviews will now compare against it.")

    if action == "archive":
        if role not in APPROVERS:
            raise PermissionError("You do not have permission to archive assets (brand manager or administrator required).")
        updated = pod.table("bg_assets").update(data.asset_id, {"approval_status": "archived"})
        _audit(pod, ctx, member, data.organization_id, "asset.archived", data.asset_id,
               f"Asset '{asset['name']}' archived", {"reason": data.reason})
        return BgAssetActionResult(asset=updated, message="Asset archived. Reviews will flag content that still uses it.")

    if action == "replace":
        if role not in APPROVERS + ("creator",):
            raise PermissionError("You do not have permission to replace assets.")
        path = p.get("file_path") or ""
        if not path.startswith("/") or ".." in path:
            raise ValueError("A valid new file path is required.")
        new_version = (asset.get("version") or 1) + 1
        pod.table("bg_asset_versions").create({
            "asset_id": asset["id"], "version": new_version, "file_path": path,
            "change_summary": p.get("change_summary") or f"Replaced v{asset.get('version')}",
            "replaced_by": email, "created_by": email})
        updated = pod.table("bg_assets").update(data.asset_id, {
            "file_path": path, "version": new_version, "approval_status": "pending",
            "image_hash": p.get("ahash"), "dominant_colors": p.get("colors") or [],
            "width": p.get("width"), "height": p.get("height"),
            "approved_by": None, "approved_at": None,
        })
        _audit(pod, ctx, member, data.organization_id, "asset.replaced", data.asset_id,
               f"Asset '{asset['name']}' replaced with v{new_version} (previous version preserved for historical review records)",
               {"change_summary": p.get("change_summary")})
        return BgAssetActionResult(asset=updated,
                                   message=f"New version v{new_version} uploaded and pending approval. The previous version is preserved in version history.")

    raise ValueError(f"Unsupported asset action: {action}")

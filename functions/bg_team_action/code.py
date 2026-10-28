#input_type_name: BgTeamActionInput
#output_type_name: BgTeamActionResult
#function_name: bg_team_action

"""Workspace membership management with server-side role checks.

Administrator only. Guards against removing the last administrator, and every change is
audited."""
import re
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ROLES = ["admin", "brand_manager", "reviewer", "compliance_reviewer", "creator"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BgTeamActionInput(BaseModel):
    organization_id: str
    action: str
    membership_id: str | None = None
    email: str | None = None
    role: str | None = None
    display_name: str | None = None
    status: str | None = None


class BgTeamActionResult(BaseModel):
    members: list[dict]
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def _members(pod, org_id):
    return pod.records.list("bg_memberships", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]


def _audit(pod, ctx, member, org_id, action, subject_id, summary, metadata=None):
    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": ctx.user_email,
        "actor_name": (member or {}).get("display_name"), "action": action,
        "subject_type": "bg_memberships", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


def bg_team_action(ctx: FunctionContext, data: BgTeamActionInput) -> BgTeamActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    members = _members(pod, data.organization_id)
    actor = next((m for m in members if m.get("user_email") == email), None)
    if not actor:
        raise PermissionError("You are not a member of this workspace.")
    if actor.get("role") != "admin":
        raise PermissionError("You do not have permission to manage this workspace's team (administrator required).")

    action = data.action
    if action == "invite":
        target = (data.email or "").strip().lower()
        if not EMAIL_RE.match(target):
            raise ValueError("A valid email address is required.")
        if data.role not in ROLES:
            raise ValueError("Unknown role.")
        existing = next((m for m in members if (m.get("user_email") or "").lower() == target), None)
        if existing:
            updated = pod.table("bg_memberships").update(existing["id"], {
                "role": data.role, "status": "active",
                "display_name": data.display_name or existing.get("display_name")})
            _audit(pod, ctx, actor, data.organization_id, "member.role_changed", existing["id"],
                   f"{target} role set to {data.role} (was {existing.get('role')})")
            return BgTeamActionResult(members=_members(pod, data.organization_id),
                                      message=f"{target} is already a member; role updated to {data.role}.")
        created = pod.table("bg_memberships").create({
            "organization_id": data.organization_id, "user_email": target,
            "display_name": data.display_name or target.split("@")[0].replace(".", " ").title(),
            "role": data.role, "status": "invited", "invited_by": email,
            "notes": "invited from the app"})
        _audit(pod, ctx, actor, data.organization_id, "member.invited", created["id"],
               f"{target} invited as {data.role}")
        return BgTeamActionResult(members=_members(pod, data.organization_id),
                                  message=f"{target} was added as {data.role}. They gain access the next time they open "
                                          "the app with that email.")

    if not data.membership_id:
        raise ValueError("membership_id is required for this action.")
    target = next((m for m in members if m["id"] == data.membership_id), None)
    if not target:
        raise ValueError("That member does not belong to this workspace.")

    admins = [m for m in members if m.get("role") == "admin" and m.get("status") == "active"]
    if target.get("role") == "admin" and len(admins) <= 1 and action in ("change_role", "suspend", "remove"):
        raise PermissionError("This is the only active administrator. Promote another administrator first.")

    if action == "change_role":
        if data.role not in ROLES:
            raise ValueError("Unknown role.")
        updated = pod.table("bg_memberships").update(data.membership_id, {"role": data.role})
        _audit(pod, ctx, actor, data.organization_id, "member.role_changed", data.membership_id,
               f"{target.get('user_email')} role changed from {target.get('role')} to {data.role}")
        return BgTeamActionResult(members=_members(pod, data.organization_id),
                                  message=f"Role updated to {data.role}.")

    if action in ("suspend", "activate"):
        status = "suspended" if action == "suspend" else "active"
        updated = pod.table("bg_memberships").update(data.membership_id, {"status": status})
        _audit(pod, ctx, actor, data.organization_id, f"member.{action}d", data.membership_id,
               f"{target.get('user_email')} set to {status}")
        return BgTeamActionResult(members=_members(pod, data.organization_id), message=f"Member {status}.")

    if action == "remove":
        pod.table("bg_memberships").delete(data.membership_id)
        _audit(pod, ctx, actor, data.organization_id, "member.removed", data.membership_id,
               f"{target.get('user_email')} removed from the workspace", {"role": target.get("role")})
        return BgTeamActionResult(members=_members(pod, data.organization_id),
                                  message="Member removed. Their historical audit entries are preserved.")

    raise ValueError(f"Unsupported team action: {action}")

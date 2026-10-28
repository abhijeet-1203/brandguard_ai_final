#input_type_name: BgCommentActionInput
#output_type_name: BgCommentActionResult
#function_name: bg_comment_action

"""Review discussion: comment, reply, mention, resolve. Workspace-scoped."""
import re
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod


class BgCommentActionInput(BaseModel):
    organization_id: str
    review_id: str
    action: str
    body: str | None = None
    comment_id: str | None = None
    parent_id: str | None = None
    finding_id: str | None = None
    range_start: int | None = None
    range_end: int | None = None


class BgCommentActionResult(BaseModel):
    comment: dict | None = None
    message: str
    mentioned: list[str] = []


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def bg_comment_action(ctx: FunctionContext, data: BgCommentActionInput) -> BgCommentActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    review = pod.table("bg_reviews").get(data.review_id)
    if review["organization_id"] != data.organization_id:
        raise PermissionError("This review belongs to another workspace.")

    if data.action == "add":
        if not data.body or not data.body.strip():
            raise ValueError("A comment cannot be empty.")
        if data.parent_id:
            parent = pod.table("bg_comments").get(data.parent_id)
            if parent["review_id"] != data.review_id:
                raise ValueError("You can only reply within the same review.")
        mentioned = [m for m in re.findall(r"@([\w.\-+]+@[\w.\-]+\.\w+)", data.body)]
        comment = pod.table("bg_comments").create({
            "organization_id": data.organization_id, "review_id": data.review_id,
            "finding_id": data.finding_id, "parent_id": data.parent_id,
            "author_id": member.get("member_user_id"), "author_email": email,
            "author_name": member.get("display_name"), "body": data.body.strip()[:4000],
            "range_start": data.range_start, "range_end": data.range_end, "is_resolved": False})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "actor_name": member.get("display_name"), "action": "comment.added",
            "subject_type": "bg_reviews", "subject_id": data.review_id,
            "summary": ("Reply added" if data.parent_id else "Comment added") +
                       (f" on a finding" if data.finding_id else ""),
            "metadata": {"comment_id": comment["id"], "mentioned": mentioned}})
        for m in mentioned:
            if m == email:
                continue
            target = pod.records.list("bg_memberships", limit=5, filter=[
                {"field": "organization_id", "op": "eq", "value": data.organization_id},
                {"field": "user_email", "op": "eq", "value": m}]).to_dict()["items"]
            if target:
                pod.records.bulk_create("bg_notifications", [{
                    "organization_id": data.organization_id,
                    "recipient_user_id": target[0].get("member_user_id"), "user_email": m,
                    "title": f"You were mentioned on '{review['title']}'",
                    "body": data.body.strip()[:300], "kind": "mention", "link_type": "review",
                    "link_id": data.review_id, "is_read": False}])
        return BgCommentActionResult(comment=comment, message="Comment posted.", mentioned=mentioned)

    if data.action in ("resolve", "reopen"):
        if not data.comment_id:
            raise ValueError("comment_id is required.")
        comment = pod.table("bg_comments").get(data.comment_id)
        if comment["review_id"] != data.review_id:
            raise PermissionError("This comment belongs to another review.")
        updated = pod.table("bg_comments").update(data.comment_id, {
            "is_resolved": data.action == "resolve",
            "resolved_by": email if data.action == "resolve" else None,
            "resolved_at": _now() if data.action == "resolve" else None})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": f"comment.{data.action}d", "subject_type": "bg_reviews",
            "subject_id": data.review_id, "summary": f"Thread {data.action}d", "metadata": {}})
        return BgCommentActionResult(comment=updated,
                                     message="Thread resolved." if data.action == "resolve" else "Thread reopened.")

    raise ValueError(f"Unsupported comment action: {data.action}")

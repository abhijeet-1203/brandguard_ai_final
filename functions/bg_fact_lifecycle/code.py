#input_type_name: BgFactLifecycleInput
#output_type_name: BgFactLifecycleResult
#function_name: bg_fact_lifecycle

"""Automated fact lifecycle.

Runs on a schedule (or on demand) and does three things:

1. **Expiry watch** - approved facts approaching expiry are notified to their owner before
   they lapse, and a re-verification task is created. Already-expired facts get an urgent task.
2. **Dependency tracking** - every review whose findings rest on a fact that is expiring or
   expired is flagged, because approving that content would publish a claim whose support is
   about to disappear.
3. **Task bookkeeping** - open tasks are refreshed (deduplicated, re-dated) and can be closed
   by a person with a note.

Notifications are sent at most once per fact per window so a schedule can run daily without
spamming owners.
"""
import re
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-lifecycle-1.0"
DEFAULT_DAYS_AHEAD = 45
NOTIFY_COOLDOWN_DAYS = 7
MANAGER_ROLES = ("admin", "brand_manager")


class BgFactLifecycleInput(BaseModel):
    organization_id: str
    action: str = "scan"
    days_ahead: int = DEFAULT_DAYS_AHEAD
    task_id: str | None = None
    note: str | None = None
    notify: bool = True


class BgFactLifecycleResult(BaseModel):
    tasks_created: int
    tasks_refreshed: int
    tasks_closed: int
    notified: int
    dependencies_flagged: int
    tasks: list[dict]
    summary: dict
    message: str
    engine_version: str = ENGINE_VERSION


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _days_until(value):
    if not value:
        return None
    try:
        return (date.fromisoformat(str(value)[:10]) - date.today()).days
    except Exception:  # noqa: BLE001
        return None


def _recently_notified(notifications, fact_id, cooldown=NOTIFY_COOLDOWN_DAYS):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown)).isoformat()
    for n in notifications:
        if n.get("kind") in ("fact_expiring", "fact_expired") and str(n.get("link_id")) == str(fact_id):
            if (n.get("created_at") or "") >= cutoff:
                return True
    return False


def bg_fact_lifecycle(ctx: FunctionContext, data: BgFactLifecycleInput) -> BgFactLifecycleResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")

    if data.action == "close_task":
        if not data.task_id:
            raise ValueError("task_id is required.")
        task = pod.table("bg_fact_tasks").get(data.task_id)
        if task["organization_id"] != data.organization_id:
            raise PermissionError("This task belongs to another workspace.")
        updated = pod.table("bg_fact_tasks").update(data.task_id, {
            "status": "done", "closed_by": email, "closed_at": _now(), "notes": data.note or task.get("notes")})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "actor_name": member.get("display_name"), "action": "fact_task.closed",
            "subject_type": "bg_fact_tasks", "subject_id": data.task_id,
            "summary": f"Re-verification task closed: {str(task.get('claim'))[:80]}",
            "metadata": {"note": data.note}})
        return BgFactLifecycleResult(tasks_created=0, tasks_refreshed=0, tasks_closed=1, notified=0,
                                     dependencies_flagged=0, tasks=[updated], summary={},
                                     message="Task closed and recorded in the audit log.")

    if data.action == "dismiss_task":
        if member.get("role") not in MANAGER_ROLES:
            raise PermissionError("Only a brand manager or administrator can dismiss a lifecycle task.")
        if not data.task_id:
            raise ValueError("task_id is required.")
        updated = pod.table("bg_fact_tasks").update(data.task_id, {
            "status": "dismissed", "closed_by": email, "closed_at": _now(), "notes": data.note})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "fact_task.dismissed", "subject_type": "bg_fact_tasks", "subject_id": data.task_id,
            "summary": "Lifecycle task dismissed", "metadata": {"note": data.note}})
        return BgFactLifecycleResult(tasks_created=0, tasks_refreshed=0, tasks_closed=1, notified=0,
                                     dependencies_flagged=0, tasks=[updated], summary={},
                                     message="Task dismissed.")

    if data.action == "list":
        tasks = pod.records.list("bg_fact_tasks", limit=300, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        open_tasks = [t for t in tasks if t.get("status") in ("open", "in_progress")]
        return BgFactLifecycleResult(tasks_created=0, tasks_refreshed=0, tasks_closed=0, notified=0,
                                     dependencies_flagged=0, tasks=open_tasks,
                                     summary={"open": len(open_tasks), "total": len(tasks)},
                                     message=f"{len(open_tasks)} open lifecycle task(s).")

    # ---- scan
    facts = pod.records.list("bg_facts", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    review_by_id = {r["id"]: r for r in reviews}
    findings = [f for f in pod.records.list("bg_findings", limit=1000).to_dict()["items"]
                if f.get("review_id") in review_by_id]
    existing = pod.records.list("bg_fact_tasks", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    notifications = pod.records.list("bg_notifications", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    members = pod.records.list("bg_memberships", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    managers = [m for m in members if m.get("role") in MANAGER_ROLES and m.get("status") == "active"]

    open_by_fact = {t["fact_id"]: t for t in existing if t.get("status") in ("open", "in_progress")}
    created = refreshed = notified = deps = 0
    touched = []

    for fact in facts:
        days = _days_until(fact.get("expiration_date"))
        if fact.get("approval_status") != "approved" or days is None:
            continue
        if days > data.days_ahead:
            continue
        task_type = "expired" if days < 0 else "reverify"
        # which pending reviews depend on this fact?
        dependent = sorted({
            f["review_id"] for f in findings
            if (f.get("source_reference") or {}).get("fact_id") == fact["id"]
            and review_by_id.get(f["review_id"], {}).get("status") in
            ("submitted", "analysing", "awaiting_review", "resubmitted", "changes_requested")})
        payload = {
            "organization_id": data.organization_id, "fact_id": fact["id"],
            "claim": fact.get("statement"), "owner": fact.get("owner") or email,
            "task_type": task_type, "status": "open",
            "due_date": (date.today() + timedelta(days=0 if days < 0 else min(days, 14))).isoformat(),
            "expires_at": fact.get("expiration_date"), "days_to_expiry": days,
            "dependent_reviews": [{"review_id": r, "title": review_by_id.get(r, {}).get("title"),
                                   "status": review_by_id.get(r, {}).get("status")} for r in dependent],
            "notes": (f"The approved source expired {abs(days)} day(s) ago. Content that relies on it is no longer "
                      "supported until it is re-verified."
                      if days < 0 else
                      f"The approved source expires in {days} day(s). Re-verify before it lapses."),
        }
        prior = open_by_fact.get(fact["id"])
        if prior:
            if prior.get("task_type") != task_type or prior.get("days_to_expiry") != days:
                pod.table("bg_fact_tasks").update(prior["id"], payload)
                refreshed += 1
            touched.append({**prior, **payload, "id": prior["id"]})
        else:
            row = pod.table("bg_fact_tasks").create(payload)
            created += 1
            touched.append(row)

        if dependent:
            deps += len(dependent)

        # notify the owner and the managers, at most once per cooldown window
        if data.notify and not _recently_notified(notifications, fact["id"]):
            rows = []
            owner_email = payload["owner"]
            owner = next((m for m in members if m.get("user_email") == owner_email), None)
            title = (f"Approved fact expired: {str(fact.get('statement'))[:70]}" if days < 0
                     else f"Approved fact expires in {days} days: {str(fact.get('statement'))[:60]}")
            body = payload["notes"] + (f" {len(dependent)} pending review(s) depend on it." if dependent else "")
            if owner:
                rows.append({"organization_id": data.organization_id,
                             "recipient_user_id": owner.get("member_user_id"), "user_email": owner_email,
                             "title": title, "body": body, "kind": "fact_expired" if days < 0 else "fact_expiring",
                             "link_type": "fact", "link_id": fact["id"], "is_read": False})
            for manager in managers[:5]:
                if owner and manager.get("user_email") == owner_email:
                    continue
                rows.append({"organization_id": data.organization_id,
                             "recipient_user_id": manager.get("member_user_id"),
                             "user_email": manager.get("user_email"), "title": title, "body": body,
                             "kind": "fact_expired" if days < 0 else "fact_expiring",
                             "link_type": "fact", "link_id": fact["id"], "is_read": False})
            if rows:
                pod.records.bulk_create("bg_notifications", rows)
                notified += len(rows)

    open_tasks = pod.records.list("bg_fact_tasks", limit=300, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    open_tasks = [t for t in open_tasks if t.get("status") in ("open", "in_progress")]
    summary = {
        "open_tasks": len(open_tasks),
        "expired": len([t for t in open_tasks if t.get("task_type") == "expired"]),
        "expiring": len([t for t in open_tasks if t.get("task_type") == "reverify"]),
        "with_dependencies": len([t for t in open_tasks if (t.get("dependent_reviews") or [])]),
        "dependent_reviews": deps,
        "days_ahead": data.days_ahead,
    }
    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
        "actor_name": member.get("display_name"), "action": "fact_lifecycle.scanned",
        "subject_type": "bg_fact_tasks", "subject_id": None,
        "summary": (f"Fact lifecycle scan: {created} task(s) created, {refreshed} refreshed, "
                    f"{notified} notification(s), {deps} dependent review(s) flagged"),
        "metadata": {"engine": ENGINE_VERSION, "summary": summary}})
    return BgFactLifecycleResult(
        tasks_created=created, tasks_refreshed=refreshed, tasks_closed=0, notified=notified,
        dependencies_flagged=deps, tasks=open_tasks, summary=summary,
        message=(f"{created} task(s) created and {refreshed} refreshed; {notified} notification(s) sent. "
                 f"{deps} pending review(s) depend on a fact that is expiring or expired."))

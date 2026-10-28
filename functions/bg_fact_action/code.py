#input_type_name: BgFactActionInput
#output_type_name: BgFactActionResult
#function_name: bg_fact_action

"""Manage the approved facts and claims library (verification is a human decision)."""
from datetime import date, datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

CATEGORIES = ["product_specification", "price", "warranty", "discount", "availability",
              "company_description", "marketing_statement", "certification", "promotional_claim",
              "disclaimer", "other"]
VERIFICATION = ["verified", "pending", "rejected", "superseded", "no_evidence"]
APPROVAL = ["approved", "pending", "rejected", "superseded"]
EDITABLE = ["statement", "category", "scope", "value_text", "numeric_value", "unit", "match_keywords",
            "source_document_id", "source_location", "effective_date", "expiration_date", "owner", "notes"]


class BgFactActionInput(BaseModel):
    organization_id: str
    action: str
    fact_id: str | None = None
    payload: dict | None = None
    evidence: dict | None = None
    reason: str | None = None


class BgFactActionResult(BaseModel):
    fact: dict | None = None
    message: str
    created_id: str | None = None


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
        "subject_type": "bg_facts", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


def bg_fact_action(ctx: FunctionContext, data: BgFactActionInput) -> BgFactActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    role = member.get("role")
    if role not in ("admin", "brand_manager", "compliance_reviewer"):
        raise PermissionError("You do not have permission to maintain the approved facts library (brand manager, compliance reviewer or administrator required).")

    action = data.action

    if action == "create":
        p = data.payload or {}
        if not p.get("statement"):
            raise ValueError("'statement' is required.")
        if p.get("category") not in CATEGORIES:
            raise ValueError("Unknown fact category.")
        row = {k: p.get(k) for k in EDITABLE if k in p}
        row.update({
            "organization_id": data.organization_id,
            "verification_status": p.get("verification_status") or "pending",
            "approval_status": p.get("approval_status") or "pending",
            "version": 1, "owner": p.get("owner") or email,
        })
        created = pod.table("bg_facts").create(row)
        _audit(pod, ctx, member, data.organization_id, "fact.created", created["id"],
               "Approved fact created: " + p["statement"][:120])
        return BgFactActionResult(fact=created, created_id=created["id"], message="Fact saved.")

    if not data.fact_id:
        raise ValueError("fact_id is required for this action.")
    fact = pod.table("bg_facts").get(data.fact_id)
    if fact["organization_id"] != data.organization_id:
        raise PermissionError("This record belongs to another workspace.")

    if action == "update":
        p = data.payload or {}
        payload = {k: v for k, v in p.items() if k in EDITABLE}
        if not payload:
            raise ValueError("No editable fields supplied.")
        if "category" in payload and payload["category"] not in CATEGORIES:
            raise ValueError("Unknown fact category.")
        payload["version"] = (fact.get("version") or 1) + 1
        updated = pod.table("bg_facts").update(data.fact_id, payload)
        _audit(pod, ctx, member, data.organization_id, "fact.updated", data.fact_id,
               "Fact updated (v" + str(payload["version"]) + ")", {"fields": sorted(payload.keys())})
        return BgFactActionResult(fact=updated, message="Fact updated.")

    if action == "verify":
        status = (data.payload or {}).get("verification_status", "verified")
        if status not in VERIFICATION:
            raise ValueError("Unknown verification status.")
        updated = pod.table("bg_facts").update(data.fact_id, {
            "verification_status": status,
            "approval_status": "approved" if status == "verified" else ("superseded" if status == "superseded" else "pending"),
            "last_verified_at": _now(), "owner": (data.payload or {}).get("owner") or fact.get("owner") or email,
            "notes": (data.payload or {}).get("note") or fact.get("notes"),
        })
        _audit(pod, ctx, member, data.organization_id, "fact.verified", data.fact_id,
               f"Fact verification set to '{status}'", {"reason": data.reason})
        return BgFactActionResult(fact=updated, message=f"Verification status set to {status}.")

    if action == "add_evidence":
        e = data.evidence or {}
        if not e.get("title"):
            raise ValueError("Evidence needs a title.")
        created = pod.table("bg_fact_evidence").create({
            "fact_id": data.fact_id,
            "evidence_type": e.get("evidence_type") or "document",
            "title": e["title"],
            "description": e.get("description"),
            "source_document_id": e.get("source_document_id"),
            "source_location": e.get("source_location"),
            "url": e.get("url"),
            "strength": e.get("strength") or "unverified",
            "added_by": email,
        })
        _audit(pod, ctx, member, data.organization_id, "fact.evidence_added", data.fact_id,
               "Evidence added: " + e["title"][:120])
        return BgFactActionResult(fact=fact, created_id=created["id"],
                                  message="Evidence recorded. The stated scope is what the evidence supports — not more.")

    if action == "expire":
        updated = pod.table("bg_facts").update(data.fact_id, {
            "verification_status": "pending", "expiration_date": str(date.today())})
        _audit(pod, ctx, member, data.organization_id, "fact.expired", data.fact_id,
               "Fact marked as expired — requires re-verification")
        return BgFactActionResult(fact=updated, message="Fact expired. It will no longer be treated as current approved truth.")

    raise ValueError(f"Unsupported fact action: {action}")

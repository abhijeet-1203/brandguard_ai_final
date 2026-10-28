#input_type_name: BgProvenanceScanInput
#output_type_name: BgProvenanceScanResult
#function_name: bg_provenance_scan

"""Claim provenance and evidence graph.

For every approved claim and every claim that a review relied on, this records where it came
from - the source document, the passage, the owner, the evidence strength and the expiry date -
and re-evaluates whether that support is still valid. Claims whose source has expired, whose
document is missing, or which never had evidence are surfaced with a reason and an alert,
instead of being treated as approved truth.
"""
import re
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-provenance-1.0"
EXPIRING_DAYS = 60


class BgProvenanceScanInput(BaseModel):
    organization_id: str
    notify: bool = True
    days_ahead: int = EXPIRING_DAYS


class BgProvenanceScanResult(BaseModel):
    links: list[dict]
    graph: dict
    summary: dict
    alerts: list[dict]
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _days(value):
    if not value:
        return None
    try:
        return (date.fromisoformat(str(value)[:10]) - date.today()).days
    except Exception:  # noqa: BLE001
        return None


def _key(*parts):
    raw = "|".join(str(p or "") for p in parts)
    return re.sub(r"[^a-zA-Z0-9|.-]", "", raw)[:190]


def evaluate(fact, evidence_rows, document_ids, days_ahead):
    """Decide whether this claim still has valid support, and say why."""
    expires = fact.get("expiration_date")
    days = _days(expires)
    if fact.get("verification_status") == "no_evidence" or fact.get("approval_status") == "rejected":
        return "unsupported", "No approved evidence substantiates this claim; it may not be used in content."
    if fact.get("verification_status") == "superseded" or fact.get("approval_status") == "superseded":
        return "superseded", "A newer approved record replaces this claim."
    if days is not None and days < 0:
        return "expired", (f"The source expired on {str(expires)[:10]} ({abs(days)} days ago), so the claim is no "
                           "longer current approved truth.")
    if fact.get("source_document_id") and str(fact["source_document_id"]) not in document_ids:
        return "orphaned", "The referenced source document is no longer available in this workspace."
    if days is not None and days <= days_ahead:
        return "expiring", f"The source expires in {days} days - re-verify before it lapses."
    if not evidence_rows and not fact.get("source_location"):
        return "orphaned", "No evidence record or source location is attached to this claim."
    return "valid", "Supporting source is present and current."


def bg_provenance_scan(ctx: FunctionContext, data: BgProvenanceScanInput) -> BgProvenanceScanResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")

    facts = pod.records.list("bg_facts", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    evidence = pod.records.list("bg_fact_evidence", limit=500).to_dict()["items"]
    documents = pod.records.list("bg_documents", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    document_ids = {d["id"] for d in documents}
    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    review_by_id = {r["id"]: r for r in reviews}
    findings = [f for f in pod.records.list("bg_findings", limit=1000).to_dict()["items"]
                if f.get("review_id") in review_by_id]
    existing = pod.records.list("bg_claim_provenance", limit=1000, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    by_key = {row.get("claim_key"): row for row in existing}

    links, alerts = [], []
    for fact in facts:
        ev = [e for e in evidence if e.get("fact_id") == fact["id"]]
        strength = "unverified"
        for candidate in ("strong", "moderate", "weak", "unverified"):
            if any(e.get("strength") == candidate for e in ev):
                strength = candidate
                break
        status, reason = evaluate(fact, ev, document_ids, data.days_ahead)
        doc = next((d for d in documents if d["id"] == fact.get("source_document_id")), None)
        payload = {
            "organization_id": data.organization_id,
            "claim_text": fact.get("statement"),
            "claim_key": _key("fact", fact["id"]),
            "source_type": "fact", "fact_id": fact["id"], "document_id": fact.get("source_document_id"),
            "source_passage": (ev[0].get("description") if ev else None),
            "source_location": fact.get("source_location"),
            "owner": fact.get("owner"), "evidence_strength": strength,
            "expires_at": fact.get("expiration_date"), "status": status, "status_reason": reason,
            "last_checked_at": _now(),
            "notes": f"{len(ev)} evidence record(s); verification status '{fact.get('verification_status')}'.",
        }
        prior = by_key.get(payload["claim_key"])
        if prior:
            pod.table("bg_claim_provenance").update(prior["id"], payload)
            link_id = prior["id"]
        else:
            created = pod.table("bg_claim_provenance").create(payload)
            link_id = created["id"]
        payload["id"] = link_id
        payload["document_title"] = doc.get("title") if doc else None
        payload["evidence_count"] = len(ev)
        links.append(payload)
        if status in ("expired", "expiring", "unsupported", "orphaned", "superseded"):
            alerts.append({"link_id": link_id, "claim": payload["claim_text"], "status": status,
                           "reason": reason, "owner": payload["owner"], "expires_at": payload["expires_at"]})

    # claims a review actually relied on
    for finding in findings:
        ref = finding.get("source_reference") or {}
        fact_id = ref.get("fact_id")
        if not fact_id:
            continue
        fact = next((f for f in facts if f["id"] == fact_id), None)
        if not fact:
            continue
        payload = {
            "organization_id": data.organization_id,
            "claim_text": finding.get("original_text") or finding.get("title"),
            "claim_key": _key("finding", finding["id"]),
            "source_type": "finding", "finding_id": finding["id"], "fact_id": fact_id,
            "review_id": finding.get("review_id"), "document_id": fact.get("source_document_id"),
            "source_passage": fact.get("statement"), "source_location": fact.get("source_location"),
            "owner": fact.get("owner"), "evidence_strength": "strong" if fact.get("verification_status") == "verified" else "unverified",
            "expires_at": fact.get("expiration_date"),
            "status": next((l["status"] for l in links if l["fact_id"] == fact_id), "valid"),
            "status_reason": next((l["status_reason"] for l in links if l["fact_id"] == fact_id), None),
            "last_checked_at": _now(),
            "notes": f"Relied on by review '{review_by_id.get(finding.get('review_id'), {}).get('title')}'.",
        }
        prior = by_key.get(payload["claim_key"])
        if prior:
            pod.table("bg_claim_provenance").update(prior["id"], payload)
            link_id = prior["id"]
        else:
            created = pod.table("bg_claim_provenance").create(payload)
            link_id = created["id"]
        payload["id"] = link_id
        payload["document_title"] = next((d.get("title") for d in documents
                                          if d["id"] == payload.get("document_id")), None)
        payload["review_title"] = review_by_id.get(finding.get("review_id"), {}).get("title")
        links.append(payload)

    if data.notify and alerts:
        members = pod.records.list("bg_memberships", limit=200, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        recipients = [m for m in members if m.get("role") in ("admin", "brand_manager") and m.get("status") == "active"]
        recent = pod.records.list("bg_notifications", limit=200, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        fresh = [a for a in alerts
                 if not any(n.get("link_id") == a["link_id"] and n.get("kind") == "provenance_alert"
                            and (n.get("created_at") or "") > (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                            for n in recent)]
        rows = []
        for alert in fresh[:20]:
            for person in recipients[:5]:
                rows.append({
                    "organization_id": data.organization_id,
                    "recipient_user_id": person.get("member_user_id"), "user_email": person.get("user_email"),
                    "title": f"Claim evidence {alert['status']}: {str(alert['claim'])[:80]}",
                    "body": alert["reason"], "kind": "provenance_alert", "link_type": "provenance",
                    "link_id": alert["link_id"], "is_read": False})
        if rows:
            pod.records.bulk_create("bg_notifications", rows)

    statuses = {}
    for link in links:
        statuses[link["status"]] = statuses.get(link["status"], 0) + 1
    nodes, edges = [], []
    for link in links:
        claim_node = f"claim:{link['id']}"
        nodes.append({"id": claim_node, "label": str(link["claim_text"])[:70], "type": "claim",
                      "status": link["status"]})
        if link.get("fact_id"):
            fact_node = f"fact:{link['fact_id']}"
            nodes.append({"id": fact_node, "label": "Approved fact", "type": "fact"})
            edges.append({"from": claim_node, "to": fact_node, "label": "substantiated by"})
        if link.get("document_id"):
            doc_node = f"doc:{link['document_id']}"
            nodes.append({"id": doc_node, "label": link.get("document_title") or "Source document",
                          "type": "document"})
            edges.append({"from": claim_node, "to": doc_node, "label": "sourced from"})
        if link.get("owner"):
            owner_node = f"owner:{link['owner']}"
            nodes.append({"id": owner_node, "label": link["owner"], "type": "owner"})
            edges.append({"from": claim_node, "to": owner_node, "label": "owned by"})
        if link.get("review_id"):
            review_node = f"review:{link['review_id']}"
            nodes.append({"id": review_node, "label": str(link.get("review_title"))[:60], "type": "review"})
            edges.append({"from": review_node, "to": claim_node, "label": "relied on"})
    unique_nodes = {n["id"]: n for n in nodes}
    unique_edges = {f"{e['from']}->{e['to']}": e for e in edges}

    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
        "actor_name": (member or {}).get("display_name"), "action": "provenance.checked",
        "subject_type": "bg_claim_provenance", "subject_id": None,
        "summary": f"Provenance re-evaluated for {len(links)} claim link(s); {len(alerts)} need attention",
        "metadata": {"engine": ENGINE_VERSION, "statuses": statuses}})

    return BgProvenanceScanResult(
        links=links,
        graph={"nodes": list(unique_nodes.values()), "edges": list(unique_edges.values())},
        summary={"total": len(links), "by_status": statuses, "alerts": len(alerts)},
        alerts=alerts,
        message=(f"{len(links)} claim(s) traced to their sources; {len(alerts)} need attention "
                 f"({', '.join(f'{k}: {v}' for k, v in statuses.items() if k != 'valid') or 'all valid'})."))

#input_type_name: BgPublishActionInput
#output_type_name: BgPublishActionResult
#function_name: bg_publish_action

"""Governed publishing handoff.

Approval is not publication. This function is the only path by which approved content leaves
BrandGuard AI, and it enforces four things:

1. **The approval chain must be satisfied.** The chain that applies to the review (by content
   type) is resolved, and any outstanding step or unresolved mandatory finding blocks the
   handoff - a compliance sign-off for sensitive claims included.
2. **The content hash is pinned at approval.** The SHA-256 of the approved version is recorded
   on the review. If the content changes afterwards, the pinned hash no longer matches and the
   handoff is blocked until the new version is analysed and approved again.
3. **The handoff is a draft push, never a publish.** When an authorised connector is available
   the approved version is pushed as a draft to the configured target. No connector in this
   organization is installed today, so the connector path reports that honestly.
4. **A signed publishing package is always available as the fallback.** The package carries
   the pinned content hash, the approved version, the approval trail and a tamper-evident
   integrity seal, so the handoff can be completed by a person without any integration.

Every attempt - allowed, blocked or failed - is written to the publishing audit trail.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-publish-1.0"
SENSITIVE_CATEGORIES = ("promotional_claim", "confidentiality", "legal_compliance")
MANAGER_ROLES = ("admin", "brand_manager")


class BgPublishActionInput(BaseModel):
    organization_id: str
    review_id: str
    action: str = "prepare"
    target: str | None = None
    reason: str | None = None


class BgPublishActionResult(BaseModel):
    event: dict | None = None
    gate: dict | None = None
    package: dict | None = None
    content_hash: str | None = None
    message: str
    engine_version: str = ENGINE_VERSION


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(text):
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _gate(pod, org_id, review):
    """Resolve the approval chain and decide whether the handoff may proceed."""
    findings = pod.records.list("bg_findings", limit=500, filter=[
        {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
    approvals = pod.records.list("bg_approvals", limit=200, filter=[
        {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
    chains = pod.records.list("bg_approval_chains", limit=100, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    chains = [c for c in chains if c.get("status") == "active"]
    chain = next((c for c in chains if c["id"] == review.get("approval_chain_id")), None) \
        or next((c for c in chains if c.get("content_type") == review.get("content_type")), None) \
        or next((c for c in chains if c.get("is_default")), None) \
        or {"name": "Standard review", "min_approvals": 1, "second_reviewer_required": False,
            "critical_requires_compliance": True, "block_on_mandatory": True}

    approved = [a for a in approvals if a.get("status") == "approved"]
    approvers = {a.get("reviewer_email") or a.get("reviewer_name") or a["id"] for a in approved
                 if (a.get("reviewer_email") or "") != (review.get("created_by_email") or "")}
    roles = {a.get("required_role") for a in approved}
    critical_claims = [f for f in findings if f.get("severity") == "critical"
                       or f.get("category") in SENSITIVE_CATEGORIES]
    open_mandatory = [f for f in findings if f.get("review_status") == "open"
                      and f.get("severity") in ("critical", "high")]

    checks = []
    checks.append({"check": "Review approved", "ok": review.get("status") == "approved",
                   "detail": f"status is '{review.get('status')}'"})
    need = max(1, int(chain.get("min_approvals") or 1))
    checks.append({"check": f"Approvals recorded ({need} required)", "ok": len(approvers) >= need,
                   "detail": f"{len(approvers)} distinct approver(s): {', '.join(sorted(str(a) for a in approvers)) or 'none'}"})
    second_needed = bool(chain.get("second_reviewer_required")) or bool(critical_claims)
    checks.append({"check": "Second reviewer", "ok": (len(approvers) >= 2) if second_needed else True,
                   "detail": ("required for this content" if second_needed else "not required")})
    compliance_needed = bool(chain.get("critical_requires_compliance")) and bool(critical_claims)
    compliance_done = any("compliance" in str(r) for r in roles)
    checks.append({"check": "Compliance sign-off", "ok": (compliance_done if compliance_needed else True),
                   "detail": (f"{len(critical_claims)} sensitive claim finding(s); "
                              f"{'recorded' if compliance_done else 'not recorded'}")
                   if compliance_needed else "no sensitive claim requires it"})
    checks.append({"check": "No unresolved mandatory finding",
                   "ok": (not open_mandatory) if chain.get("block_on_mandatory", True) else True,
                   "detail": f"{len(open_mandatory)} open high or critical finding(s)"})
    pinned = review.get("approved_hash") or review.get("content_hash")
    current = _sha256(review.get("current_text") or "")
    checks.append({"check": "Content unchanged since approval",
                   "ok": bool(pinned) and pinned == current,
                   "detail": (f"pinned {str(pinned)[:24]}… vs current {current[:24]}…" if pinned
                              else "no approved hash was pinned, so integrity cannot be verified")})
    failed = [c for c in checks if not c["ok"]]
    return {"chain_name": chain.get("name"), "checks": checks, "allowed": not failed,
            "blocked_reason": (None if not failed else "; ".join(f"{c['check']}: {c['detail']}" for c in failed)),
            "critical_claims": len(critical_claims), "approvers": sorted(str(a) for a in approvers),
            "target": chain.get("publish_target")}


def _connector_available(pod):
    """Is any connector installed in this organization that could receive a draft?"""
    try:
        configs = pod.connectors.auth_configs.list(limit=20)
        rows = configs.to_dict().get("items") if hasattr(configs, "to_dict") else []
        return (len(rows or []), None)
    except Exception as e:  # noqa: BLE001
        return (0, f"{type(e).__name__}: {e}")


def _audit_event(pod, ctx, member, org_id, review, action, status, detail, target=None,
                 connector=None, content_hash=None, manifest=None, package_path=None):
    event = pod.table("bg_publish_events").create({
        "organization_id": org_id, "review_id": review["id"], "version": review.get("current_version"),
        "action": action, "status": status, "target": target or review.get("publish_target"),
        "connector": connector, "content_hash": content_hash, "manifest": manifest,
        "package_path": package_path, "detail": detail, "actor_email": ctx.user_email})
    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": ctx.user_email,
        "actor_name": (member or {}).get("display_name"), "action": f"publish.{action}",
        "subject_type": "bg_reviews", "subject_id": review["id"],
        "summary": f"Publishing {action} {status}: {str(detail)[:160]}",
        "metadata": {"target": target, "connector": connector, "content_hash": content_hash,
                     "package_path": package_path, "version": review.get("current_version")}})
    return event


def bg_publish_action(ctx: FunctionContext, data: BgPublishActionInput) -> BgPublishActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    review = pod.table("bg_reviews").get(data.review_id)
    if review["organization_id"] != data.organization_id:
        raise PermissionError("This review belongs to another workspace.")

    if data.action == "list":
        events = pod.records.list("bg_publish_events", limit=200, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        packages = pod.records.list("bg_publish_packages", limit=100, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        return BgPublishActionResult(events=events[:50], packages=packages[:20],
                                     message=f"{len(events)} publishing event(s), {len(packages)} package(s).")

    gate = _gate(pod, data.organization_id, review)
    current_hash = _sha256(review.get("current_text") or "")

    # ---- prepare: pin the hash and report whether the handoff is allowed
    if data.action == "prepare":
        if member.get("role") not in MANAGER_ROLES:
            raise PermissionError("Only a brand manager or administrator can prepare a publishing handoff.")
        if not gate["allowed"]:
            event = _audit_event(pod, ctx, member, data.organization_id, review, "blocked", "blocked",
                                 gate["blocked_reason"], target=data.target, content_hash=current_hash)
            pod.table("bg_reviews").update(review["id"], {
                "publish_status": "blocked", "publish_blocked_reason": gate["blocked_reason"],
                "publish_target": data.target or gate.get("target") or review.get("publish_target")})
            return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash,
                                         message=f"Handoff blocked. {gate['blocked_reason']}")
        pod.table("bg_reviews").update(review["id"], {
            "approved_hash": current_hash, "content_hash": current_hash,
            "publish_status": "prepared", "publish_target": data.target or gate.get("target"),
            "publish_blocked_reason": None})
        event = _audit_event(pod, ctx, member, data.organization_id, review, "prepare", "ok",
                             f"Approved version v{review.get('current_version')} pinned as {current_hash[:24]}…",
                             target=data.target or gate.get("target"), content_hash=current_hash)
        return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash,
                                     message=(f"Approved version pinned ({current_hash[:24]}…). The approval chain is "
                                              f"satisfied, so the handoff may proceed."))

    # ---- every remaining action re-checks the gate, including content integrity
    if not gate["allowed"]:
        event = _audit_event(pod, ctx, member, data.organization_id, review, data.action, "blocked",
                             gate["blocked_reason"], target=data.target, content_hash=current_hash)
        pod.table("bg_reviews").update(review["id"], {
            "publish_status": "blocked", "publish_blocked_reason": gate["blocked_reason"]})
        return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash,
                                     message=f"Blocked. {gate['blocked_reason']}")

    approvals = pod.records.list("bg_approvals", limit=200, filter=[
        {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
    versions = pod.records.list("bg_review_versions", limit=100, filter=[
        {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
    approved_version = next((v for v in versions if v.get("version") == review.get("current_version")), None)

    # ---- push: only as a draft, only through an authorised connector
    if data.action == "push":
        if member.get("role") not in MANAGER_ROLES:
            raise PermissionError("Only a brand manager or administrator can authorise a draft push.")
        installed, connector_error = _connector_available(pod)
        target = data.target or review.get("publish_target") or gate.get("target") or "not configured"
        if not installed:
            detail = ("No connector is installed in this organization, so no external tool could be authorised. "
                      f"Use the signed publishing package instead. ({connector_error or 'no auth configs found'})")
            event = _audit_event(pod, ctx, member, data.organization_id, review, "draft_push", "failed", detail,
                                 target=target, content_hash=current_hash)
            pod.table("bg_reviews").update(review["id"], {
                "publish_status": "package_ready", "publish_blocked_reason": None})
            return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash,
                                         message=detail)
        try:
            result = pod.connectors.execute(
                installed[0], "create_draft",
                {"title": review.get("title"), "content": review.get("current_text"),
                 "status": "draft", "content_hash": current_hash, "source": "BrandGuard AI"})
            payload = result.to_dict() if hasattr(result, "to_dict") else result
            detail = f"Draft pushed to {target} as a draft (not published). Response: {json.dumps(payload)[:300]}"
            event = _audit_event(pod, ctx, member, data.organization_id, review, "draft_push", "ok", detail,
                                 target=target, connector=installed[0], content_hash=current_hash)
            pod.table("bg_reviews").update(review["id"], {
                "publish_status": "drafted", "published_at": _now(), "publish_blocked_reason": None})
            return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash,
                                         message=f"Draft pushed to {target}. It is a draft there, not a publication.")
        except Exception as e:  # noqa: BLE001
            detail = f"The connector call failed ({type(e).__name__}: {e}). Nothing was published."
            event = _audit_event(pod, ctx, member, data.organization_id, review, "draft_push", "failed", detail,
                                 target=target, connector=installed[0], content_hash=current_hash)
            return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash, message=detail)

    # ---- package: the fallback handoff, always available
    if data.action == "package":
        if member.get("role") not in MANAGER_ROLES:
            raise PermissionError("Only a brand manager or administrator can generate a publishing package.")
        manifest = {
            "brandguard_publishing_package": "1.0",
            "generated_at": _now(),
            "engine": ENGINE_VERSION,
            "workspace": {"id": data.organization_id, "name": (pod.table("bg_organizations").get(data.organization_id) or {}).get("name")},
            "content": {
                "review_id": review["id"], "title": review.get("title"),
                "content_type": review.get("content_type"), "campaign": review.get("campaign"),
                "channel": review.get("channel"), "audience": review.get("audience"),
                "language": review.get("language"), "version": review.get("current_version"),
                "content_hash": current_hash, "characters": len(review.get("current_text") or ""),
                "word_count": len((review.get("current_text") or "").split()),
            },
            "approval": {
                "chain": gate.get("chain_name"),
                "status": review.get("status"),
                "decided_at": review.get("decided_at"),
                "decision_reason": review.get("decision_reason"),
                "approvers": gate.get("approvers"),
                "checks": gate.get("checks"),
            },
            "open_findings": [],
            "target": data.target or review.get("publish_target") or gate.get("target") or "not configured",
            "instructions": ("Import the content as a DRAFT in the target tool, compare the content hash shown here "
                             "with the hash of what you import, and only publish after that comparison matches. "
                             "This package is a handoff, not a publication."),
        }
        findings = pod.records.list("bg_findings", limit=500, filter=[
            {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
        manifest["open_findings"] = [
            {"title": f.get("title"), "severity": f.get("severity"), "category": f.get("category"),
             "evidence_status": f.get("evidence_status"), "review_status": f.get("review_status"),
             "rule_code": f.get("rule_code")}
            for f in findings if f.get("review_status") in ("open", "escalated")]
        manifest_json = json.dumps(manifest, indent=2, sort_keys=True, default=str)
        manifest_hash = _sha256(manifest_json)
        # Tamper-evident seal over the pinned content, the manifest and the approval trail.
        # No external signing key is configured in this pod, so this is a deterministic
        # integrity seal that a recipient can recompute - it is not a third-party signature.
        seal = _sha256(f"{current_hash}|{manifest_hash}|{data.organization_id}|{review['id']}|"
                       f"{review.get('current_version')}")
        slug = (pod.table("bg_organizations").get(data.organization_id) or {}).get("slug") or "workspace"
        safe_title = re.sub(r"[^a-zA-Z0-9]+", "-", str(review.get("title"))).strip("-")[:50] or "review"
        package_dir = f"/brandguard/{slug}/publishing"
        manifest_path = f"{package_dir}/{safe_title}-v{review.get('current_version')}-manifest.json"
        content_path = f"{package_dir}/{safe_title}-v{review.get('current_version')}-content.md"
        try:
            pod.files.write_text(manifest_path, manifest_json, description="BrandGuard AI publishing manifest")
            pod.files.write_text(content_path, review.get("current_text") or "",
                                 description="BrandGuard AI approved content for handoff")
            written = [manifest_path, content_path]
        except Exception as e:  # noqa: BLE001
            written = []
            manifest["write_error"] = f"{type(e).__name__}: {e}"
        contents = {
            "manifest_path": manifest_path if written else None,
            "content_path": content_path if written else None,
            "content_hash": current_hash, "manifest_hash": manifest_hash,
            "approved_version": review.get("current_version"),
            "approvals": len(approvals), "approved_by": gate.get("approvers"),
            "target": manifest["target"], "generated_at": manifest["generated_at"],
        }
        package = pod.table("bg_publish_packages").create({
            "organization_id": data.organization_id, "review_id": review["id"],
            "version": review.get("current_version"), "content_hash": current_hash,
            "manifest_hash": manifest_hash, "package_path": manifest_path if written else None,
            "signed": True, "signature": seal,
            "signature_method": "sha256 integrity seal over content hash, manifest hash, workspace and version "
                                "(tamper-evident; no external signing key is configured)",
            "contents": contents, "status": "ready", "created_by": email})
        event = _audit_event(pod, ctx, member, data.organization_id, review, "package", "ok",
                             f"Signed publishing package generated with pinned hash {current_hash[:24]}… "
                             f"(seal {seal[:24]}…)", target=manifest["target"],
                             content_hash=current_hash, manifest=contents,
                             package_path=manifest_path if written else None)
        pod.table("bg_reviews").update(review["id"], {
            "publish_status": "package_ready", "publish_blocked_reason": None})
        return BgPublishActionResult(event=event, gate=gate, package=package, content_hash=current_hash,
                                     message=(f"Publishing package ready with the pinned hash {current_hash[:24]}… and "
                                              f"integrity seal {seal[:24]}…. No connector is required to complete the "
                                              "handoff, and the target tool receives it as a draft."))

    if data.action == "withdraw":
        if member.get("role") not in MANAGER_ROLES:
            raise PermissionError("Only a brand manager or administrator can withdraw a handoff.")
        pod.table("bg_reviews").update(review["id"], {
            "publish_status": "withdrawn", "publish_blocked_reason": data.reason})
        event = _audit_event(pod, ctx, member, data.organization_id, review, "withdrawn", "ok",
                             data.reason or "Handoff withdrawn by a person", content_hash=current_hash)
        return BgPublishActionResult(event=event, gate=gate, content_hash=current_hash,
                                     message="Handoff withdrawn and recorded.")

    raise ValueError(f"Unsupported publishing action: {data.action}")

#input_type_name: BgCorrectionActionInput
#output_type_name: BgCorrectionActionResult
#function_name: bg_correction_action

"""AI correction assistant + correction validation.

Apply / reject / edit / regenerate / restore corrections. Applying a correction never
marks a finding resolved by itself: it creates a new content version, and the finding is
only closed after a re-scan confirms the problem is gone."""
import asyncio
import time
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

CORRECTION_AGENT = "brandguard-correction-writer"


class BgCorrectionActionInput(BaseModel):
    organization_id: str
    review_id: str
    action: str
    finding_id: str | None = None
    instructions: str | None = None
    edited_text: str | None = None
    reason: str | None = None
    apply_all_accepted: bool = False


class BgCorrectionActionResult(BaseModel):
    finding: dict | None = None
    new_version: int | None = None
    rescan_required: bool = False
    applied: list[str] = []
    message: str
    warnings: list[str] = []


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


async def _ask(pod, message, title, timeout_s=240):
    conv = pod.agents.run(CORRECTION_AGENT, message, title=title)
    cid = str(conv.id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for m in reversed(pod.conversations.messages(cid, limit=100).to_dict()["items"]):
            md = m.get("metadata") or {}
            if m.get("role") == "assistant" and md.get("is_final_answer"):
                if isinstance(md.get("structured_output"), dict):
                    return md["structured_output"]
                return {"corrected_text": (m.get("text") or "").strip(), "rationale": "model reply (unstructured)",
                        "preserves_meaning": True, "warnings": ["The correction was not returned in structured form."]}
        await asyncio.sleep(2)
    return None


def _locate(text, finding):
    """Return (start, end) for the finding's span in the current text."""
    start, end = finding.get("location_start"), finding.get("location_end")
    original = finding.get("original_text") or ""
    if start is not None and end is not None and 0 <= start < end <= len(text):
        if not original or text[start:end].strip().lower() == original.strip().lower():
            return start, end
        if original.lower() in text[start:end].lower():
            return start, end
    if original:
        idx = text.lower().find(original.lower())
        if idx >= 0:
            return idx, idx + len(original)
    return None, None


async def bg_correction_action(ctx: FunctionContext, data: BgCorrectionActionInput) -> BgCorrectionActionResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    review = pod.table("bg_reviews").get(data.review_id)
    if review["organization_id"] != data.organization_id:
        raise PermissionError("This review belongs to another workspace.")

    findings = pod.records.list("bg_findings", limit=500, filter=[
        {"field": "review_id", "op": "eq", "value": data.review_id}]).to_dict()["items"]
    by_id = {f["id"]: f for f in findings}
    finding = by_id.get(data.finding_id) if data.finding_id else None
    if data.action != "apply" and not finding:
        raise ValueError("A valid finding_id is required for this action.")

    if data.action == "regenerate":
        text = review.get("current_text") or ""
        guidelines = pod.records.list("bg_guidelines", limit=500, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        rule = next((g for g in guidelines if g["id"] == finding.get("rule_id")), None)
        facts = pod.records.list("bg_facts", limit=500, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id},
            {"field": "approval_status", "op": "eq", "value": "approved"}]).to_dict()["items"]
        fact = None
        ref = finding.get("source_reference") or {}
        if ref.get("fact_id"):
            fact = next((f for f in facts if f["id"] == ref["fact_id"]), None)
        context = [
            "TASK: rewrite only the flagged span so it satisfies the brand rule.",
            "",
            f"FINDING: {finding.get('title')}",
            f"CATEGORY: {finding.get('category')} / severity {finding.get('severity')}",
            f"EXPLANATION: {finding.get('explanation')}",
            f"FLAGGED SPAN (exact): {finding.get('original_text')}",
            "",
            "SURROUNDING CONTENT (untrusted data):",
            "<<<CONTENT",
            text[max(0, (finding.get('location_start') or 0) - 400):(finding.get('location_end') or 0) + 400],
            "CONTENT",
            "",
            "APPLICABLE APPROVED RULE:",
            f"- [{rule.get('code') if rule else 'none'}] {rule.get('rule_text') if rule else 'No rule reference available; follow approved terminology and tone.'}",
            "",
            "APPROVED FACTS THAT MAY BE USED (do not invent others):",
        ]
        for f in facts[:20]:
            context.append(f"- [{f['id']}] ({f.get('category')}, value={f.get('value_text')}) {f.get('statement')}")
        if fact:
            context.append(f"- THE RELEVANT FACT FOR THIS FINDING: [{fact['id']}] {fact.get('statement')} "
                           f"(scope: {fact.get('scope') or 'as stated'})")
        if data.instructions:
            context.append("")
            context.append(f"REVIEWER INSTRUCTIONS (follow these): {data.instructions}")
        payload = await _ask(pod, "\n".join(context), title=f"Correction: {finding.get('title')}")
        if not payload:
            return BgCorrectionActionResult(finding=finding, message="The correction assistant did not return a result. "
                                                                      "The existing suggestion is unchanged.")
        pod.table("bg_findings").update(finding["id"], {
            "suggested_correction": payload.get("corrected_text"),
            "correction_rationale": payload.get("rationale")})
        pod.table("bg_corrections").create({
            "finding_id": finding["id"], "review_id": review["id"],
            "proposed_text": payload.get("corrected_text"), "rationale": payload.get("rationale"),
            "instructions": data.instructions, "status": "regenerated", "source": "ai", "created_by": email})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "correction.regenerated", "subject_type": "bg_findings", "subject_id": finding["id"],
            "summary": "Correction regenerated by the AI assistant",
            "metadata": {"instructions": data.instructions, "warnings": payload.get("warnings")}})
        return BgCorrectionActionResult(
            finding=pod.table("bg_findings").get(finding["id"]),
            message="New suggestion generated. Review it before accepting - nothing was applied to the content.",
            warnings=payload.get("warnings") or [])

    if data.action == "edit":
        if not data.edited_text or not data.edited_text.strip():
            raise ValueError("The edited correction text cannot be empty.")
        pod.table("bg_findings").update(finding["id"], {"suggested_correction": data.edited_text.strip()})
        pod.table("bg_corrections").create({
            "finding_id": finding["id"], "review_id": review["id"], "proposed_text": data.edited_text.strip(),
            "rationale": data.reason, "status": "edited", "source": "human", "created_by": email})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "correction.edited", "subject_type": "bg_findings", "subject_id": finding["id"],
            "summary": "Suggested correction edited by a person", "metadata": {"reason": data.reason}})
        return BgCorrectionActionResult(finding=pod.table("bg_findings").get(finding["id"]),
                                        message="Suggestion updated with your wording.")

    if data.action == "accept":
        updated = pod.table("bg_findings").update(finding["id"], {"review_status": "accepted"})
        pod.table("bg_corrections").create({
            "finding_id": finding["id"], "review_id": review["id"],
            "proposed_text": finding.get("suggested_correction"), "status": "accepted",
            "source": "human", "created_by": email})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "correction.accepted", "subject_type": "bg_findings", "subject_id": finding["id"],
            "summary": "Suggestion accepted (not yet applied to the content)", "metadata": {}})
        return BgCorrectionActionResult(finding=updated, message="Suggestion accepted. Apply corrections to create a "
                                                                "revised version, then re-scan.")

    if data.action == "reject":
        updated = pod.table("bg_findings").update(finding["id"], {
            "review_status": "rejected", "resolution_note": data.reason, "resolved_by": email,
            "resolved_at": _now()})
        pod.table("bg_corrections").create({
            "finding_id": finding["id"], "review_id": review["id"],
            "proposed_text": finding.get("suggested_correction"), "rationale": data.reason,
            "status": "rejected", "source": "human", "created_by": email})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "correction.rejected", "subject_type": "bg_findings", "subject_id": finding["id"],
            "summary": "Suggested correction rejected", "metadata": {"reason": data.reason}})
        return BgCorrectionActionResult(finding=updated,
                                        message="Suggestion rejected and recorded. The finding stays visible to reviewers.")

    if data.action in ("resolve_manual", "escalate"):
        if not data.reason:
            raise ValueError("A reason is required.")
        status = "resolved" if data.action == "resolve_manual" else "escalated"
        updated = pod.table("bg_findings").update(finding["id"], {
            "review_status": status, "resolution_note": data.reason, "resolved_by": email,
            "resolved_at": _now()})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": f"finding.{status}", "subject_type": "bg_findings", "subject_id": finding["id"],
            "summary": f"Finding marked {status} by a person", "metadata": {"reason": data.reason}})
        return BgCorrectionActionResult(finding=updated,
                                        message="Finding marked resolved by a person." if status == "resolved"
                                        else "Finding escalated for compliance review.")

    if data.action == "restore":
        text = review.get("current_text") or ""
        versions = pod.records.list("bg_review_versions", limit=100, filter=[
            {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
        first = min(versions, key=lambda v: v.get("version") or 0)
        original_text = finding.get("original_text")
        if not original_text:
            raise ValueError("This finding has no original wording to restore.")
        start, end = _locate(text, finding)
        suggestion = finding.get("suggested_correction") or ""
        if start is None and suggestion:
            start = text.lower().find(suggestion.lower())
            end = start + len(suggestion) if start >= 0 else None
        if start is None or start < 0:
            raise ValueError("Could not locate the current wording to restore.")
        new_text = text[:start] + original_text + text[end:]
        version = (review.get("current_version") or 1) + 1
        pod.table("bg_review_versions").create({
            "review_id": review["id"], "version": version, "content": new_text,
            "change_summary": f"Restored the original wording flagged by '{finding['title']}'",
            "change_source": "restore", "author_id": member.get("member_user_id"), "author_email": email,
            "author_name": member.get("display_name")})
        pod.table("bg_reviews").update(review["id"], {
            "current_text": new_text, "current_version": version, "analysis_status": "queued",
            "status": "submitted" if review["status"] in ("approved", "awaiting_review") else review["status"]})
        pod.table("bg_findings").update(finding["id"], {"review_status": "reopened" if False else "open",
                                                        "resolution_note": None})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "correction.restored", "subject_type": "bg_reviews", "subject_id": review["id"],
            "summary": f"Original wording restored (v{version})", "metadata": {"finding_id": finding["id"]}})
        return BgCorrectionActionResult(finding=pod.table("bg_findings").get(finding["id"]), new_version=version,
                                        rescan_required=True,
                                        message=f"Original wording restored as v{version}. Re-scan to confirm the current state.")

    if data.action == "apply":
        text = review.get("current_text") or ""
        if data.apply_all_accepted or not finding:
            targets = [f for f in findings
                       if f.get("review_status") == "accepted" and (f.get("suggested_correction") or "").strip()]
        else:
            targets = [finding] if (finding.get("suggested_correction") or "").strip() else []
        if not targets:
            raise ValueError("There is nothing to apply: no accepted suggestion with replacement text.")
        edits = []
        for f in targets:
            start, end = _locate(text, f)
            if start is None:
                continue
            edits.append((start, end, f))
        if not edits:
            raise ValueError("None of the accepted suggestions could be located in the current content. "
                             "The content may have changed since the finding was created.")
        edits.sort(key=lambda e: -e[0])
        new_text = text
        applied = []
        for start, end, f in edits:
            new_text = new_text[:start] + f["suggested_correction"].strip() + new_text[end:]
            applied.append(f["id"])
        version = (review.get("current_version") or 1) + 1
        pod.table("bg_review_versions").create({
            "review_id": review["id"], "version": version, "content": new_text,
            "change_summary": f"Applied {len(applied)} suggested correction(s)",
            "change_source": "ai_correction", "author_id": member.get("member_user_id"),
            "author_email": email, "author_name": member.get("display_name"),
            "findings_snapshot": [{"finding_id": f["id"], "title": f["title"], "applied": f["suggested_correction"]}
                                  for _, _, f in edits]})
        pod.table("bg_reviews").update(review["id"], {
            "current_text": new_text, "current_version": version, "analysis_status": "queued",
            "status": "submitted" if review["status"] in ("approved", "rejected", "awaiting_review",
                                                          "changes_requested", "resubmitted") else review["status"]})
        for _, _, f in edits:
            pod.table("bg_findings").update(f["id"], {"review_status": "accepted"})
            pod.table("bg_corrections").create({
                "finding_id": f["id"], "review_id": review["id"],
                "proposed_text": f.get("suggested_correction"), "status": "applied",
                "source": "human", "created_by": email, "applied_version": version, "applied_at": _now()})
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "actor_name": member.get("display_name"), "action": "correction.applied",
            "subject_type": "bg_reviews", "subject_id": review["id"],
            "summary": f"{len(applied)} correction(s) applied as v{version}. Findings stay open until a re-scan confirms them.",
            "metadata": {"finding_ids": applied, "version": version}})
        return BgCorrectionActionResult(new_version=version, rescan_required=True, applied=applied,
                                        message=f"Applied {len(applied)} correction(s) as v{version}. "
                                                "Re-scan the revised content to confirm which findings are resolved.")

    raise ValueError(f"Unsupported correction action: {data.action}")

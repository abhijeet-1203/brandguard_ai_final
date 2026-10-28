#input_type_name: BgRootCauseInput
#output_type_name: BgRootCauseResult
#function_name: bg_root_cause

"""Root-cause analysis and auto-generated policy suggestions.

Turns repeated violations into an explanation and a proposal: which rule, campaign, content
type or team keeps producing the same problem, what the likely cause is (an unclear rule, an
outdated source document, a training gap, or a one-off campaign spike), and what a revised
rule could say. Every proposal is a draft. Nothing is enforced until a brand manager approves
it through the normal guideline workflow.
"""
import asyncio
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

AGENT = "brandguard-guideline-extractor"
ENGINE_VERSION = "bg-rootcause-1.0"
MIN_CASES = 3
CODE_PREFIX = {"tone": "TONE", "terminology": "TERM", "messaging": "MSG", "factual_accuracy": "FACT",
               "promotional_claim": "CLAIM", "visual_identity": "VIS", "accessibility": "A11Y",
               "confidentiality": "CONF", "legal_compliance": "LEGAL", "formatting": "FMT"}


class BgRootCauseInput(BaseModel):
    organization_id: str
    action: str = "generate"
    days: int = 365
    min_cases: int = MIN_CASES
    suggestion_id: str | None = None
    reason: str | None = None


class BgRootCauseResult(BaseModel):
    suggestions: list[dict]
    message: str
    data_note: str | None = None


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
        "subject_type": "bg_policy_suggestions", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


async def _ask(pod, message, title, timeout_s=300):
    conv = pod.agents.run(AGENT, message, title=title)
    cid = str(conv.id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for m in reversed(pod.conversations.messages(cid, limit=100).to_dict()["items"]):
            md = m.get("metadata") or {}
            if m.get("role") == "assistant" and md.get("is_final_answer"):
                if isinstance(md.get("structured_output"), dict):
                    return md["structured_output"]
        await asyncio.sleep(2)
    return None


def classify(group, baseline_rate, totals):
    """Why does this keep happening? Return (cause, detail, note)."""
    ambiguous = len([f for f in group["findings"] if f.get("evidence_status") in ("ambiguous", "unverified")])
    expired = [f for f in group["findings"]
               if (f.get("source_reference") or {}).get("expiration_date")
               and str((f.get("source_reference") or {}).get("expiration_date"))[:10] < str(date.today())]
    if expired:
        return ("outdated_source",
                f"{len(expired)} of {len(group['findings'])} finding(s) rest on an approved source that has expired, "
                "so reviewers are working from a stale reference.",
                "Refresh the source document and the approved fact before changing the rule.")
    if ambiguous and ambiguous / max(1, len(group["findings"])) >= 0.4:
        return ("rule_clarity",
                f"{ambiguous} of {len(group['findings'])} finding(s) needed human judgement (ambiguous or "
                "unverified), which points at rule wording rather than at the authors.",
                "Reword the rule so it states when it applies, not only what it forbids.")
    campaigns = Counter(f.get("campaign") or "(none)" for f in group["findings"])
    if campaigns and len(campaigns) == 1 and group["reviews"] >= 2:
        return ("campaign_specific",
                f"Every case comes from the “{campaigns.most_common(1)[0][0]}” campaign, so this looks like a spike "
                "from one campaign rather than a standing rule problem.",
                "Check the campaign brief first; only change the rule if it recurs outside this campaign.")
    content_types = Counter(f.get("content_type") or "unknown" for f in group["findings"])
    creators = Counter(f.get("created_by_email") or "unknown" for f in group["findings"])
    if content_types and content_types.most_common(1)[0][1] / len(group["findings"]) >= 0.7:
        return ("training_gap",
                f"{content_types.most_common(1)[0][1]} of {len(group['findings'])} cases are "
                f"{content_types.most_common(1)[0][0].replace('_', ' ')}, so the gap looks specific to how that "
                "content type is written.",
                "Add a worked example to the guideline for that content type.")
    if creators and len(creators) == 1 and group["reviews"] >= 2:
        return ("training_gap",
                f"All cases in this group came from {creators.most_common(1)[0][0]}, so this reads as a coaching "
                "matter rather than a policy gap.",
                "Share the worked examples with that author before changing the rule.")
    if group["reviews"] >= 3:
        return ("process_gap",
                f"The same rule was breached in {group['reviews']} separate reviews, which suggests the check is "
                "happening too late in the process.",
                "Add the rule to the authoring checklist so it is caught before submission.")
    return ("insufficient_data", "Not enough cases to attribute a cause.", "Collect more reviews before acting.")


async def bg_root_cause(ctx: FunctionContext, data: BgRootCauseInput) -> BgRootCauseResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    role = member.get("role")

    if data.action in ("create_draft", "dismiss"):
        if role not in ("admin", "brand_manager"):
            raise PermissionError("Only a brand manager or administrator can act on a policy suggestion.")
        if not data.suggestion_id:
            raise ValueError("suggestion_id is required.")
        suggestion = pod.table("bg_policy_suggestions").get(data.suggestion_id)
        if suggestion["organization_id"] != data.organization_id:
            raise PermissionError("This suggestion belongs to another workspace.")
        if data.action == "dismiss":
            updated = pod.table("bg_policy_suggestions").update(data.suggestion_id, {
                "status": "dismissed", "decided_by": email, "decided_at": _now()})
            _audit(pod, ctx, member, data.organization_id, "policy_suggestion.dismissed",
                   data.suggestion_id, "Policy suggestion dismissed", {"reason": data.reason})
            return BgRootCauseResult(suggestions=[updated], message="Suggestion dismissed.")
        proposed = suggestion.get("proposed_rule") or {}
        guidelines = pod.records.list("bg_guidelines", limit=500, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        category = proposed.get("category") or "messaging"
        prefix = CODE_PREFIX.get(category, "RULE")
        used = [g.get("code", "") for g in guidelines if str(g.get("code", "")).startswith(prefix + "-")]
        draft = pod.table("bg_guidelines").create({
            "organization_id": data.organization_id, "code": f"{prefix}-{len(used) + 1:03d}",
            "title": (proposed.get("title") or suggestion["title"])[:300],
            "description": proposed.get("rule_text") or suggestion.get("rationale"),
            "category": category, "rule_type": proposed.get("rule_type") or "messaging_rule",
            "severity": proposed.get("severity") or "medium",
            "enforcement": proposed.get("enforcement") or "recommended",
            "rule_text": proposed.get("rule_text") or suggestion.get("rationale") or suggestion["title"],
            "match_terms": proposed.get("match_terms"),
            "source_section": proposed.get("source_quote"),
            "version": 1, "status": "draft", "created_by": ctx.user_id, "created_by_email": email,
            "notes": (f"Drafted from root-cause analysis of {suggestion.get('root_cause_type')} "
                      f"({len(suggestion.get('evidence') or [])} supporting case(s)). Requires approval "
                      "before it is enforced.")})
        pod.table("bg_guideline_versions").create({
            "guideline_id": draft["id"], "version": 1, "snapshot": draft,
            "change_summary": "Draft created from root-cause analysis",
            "changed_by": ctx.user_id, "changed_by_email": email})
        updated = pod.table("bg_policy_suggestions").update(data.suggestion_id, {
            "status": "draft_created", "draft_guideline_id": draft["id"], "decided_by": email,
            "decided_at": _now()})
        _audit(pod, ctx, member, data.organization_id, "policy_suggestion.draft_created",
               data.suggestion_id, f"Draft rule {draft['code']} created from root-cause analysis",
               {"draft_guideline_id": draft["id"]})
        return BgRootCauseResult(suggestions=[updated],
                                 message=f"Draft rule {draft['code']} created. It stays a draft until an authorised "
                                         "person approves it - the platform never rewrites official guidelines itself.")

    # ---- generate
    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    review_by_id = {r["id"]: r for r in reviews}
    findings = [f for f in pod.records.list("bg_findings", limit=1000).to_dict()["items"]
                if f.get("review_id") in review_by_id]
    guidelines = pod.records.list("bg_guidelines", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    by_id = {g["id"]: g for g in guidelines}

    old = pod.records.list("bg_policy_suggestions", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    stale = [s["id"] for s in old if s.get("status") == "proposed"]
    if stale:
        pod.records.bulk_delete("bg_policy_suggestions", stale)

    groups = defaultdict(list)
    for finding in findings:
        key = finding.get("rule_code") or f"unruled:{finding.get('category')}"
        groups[key].append({**finding,
                            "campaign": review_by_id.get(finding.get("review_id"), {}).get("campaign"),
                            "content_type": review_by_id.get(finding.get("review_id"), {}).get("content_type"),
                            "created_by_email": review_by_id.get(finding.get("review_id"), {}).get("created_by_email")})

    suggestions = []
    if len(reviews) < 4:
        note = f"Only {len(reviews)} review(s) exist, so root causes cannot be attributed reliably."
    else:
        note = f"{len(reviews)} reviews and {len(findings)} findings analysed."
        for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            review_ids = sorted({f["review_id"] for f in items})
            if len(items) < data.min_cases or len(review_ids) < 2:
                continue
            rule = by_id.get(items[0].get("rule_id")) or next(
                (g for g in guidelines if g.get("code") == items[0].get("rule_code")), None)
            group = {"findings": items, "reviews": len(review_ids)}
            cause, detail, action = classify(group, len(items) / max(1, len(findings)), len(findings))
            examples = [{"review_id": f["review_id"], "title": review_by_id.get(f["review_id"], {}).get("title"),
                         "campaign": f.get("campaign"), "content_type": f.get("content_type"),
                         "severity": f["severity"], "evidence_status": f.get("evidence_status"),
                         "quoted": f.get("original_text"), "explanation": f.get("explanation"),
                         "rule_code": f.get("rule_code")}
                        for f in items[:6]]
            proposed_rule = None
            if cause in ("rule_clarity", "process_gap", "training_gap") and rule:
                prompt = "\n".join([
                    "A brand rule keeps being breached in review. Propose ONE revised rule that would remove the",
                    "ambiguity, keeping the brand's intent. Base it only on the evidence below.",
                    "",
                    f"EXISTING RULE {rule.get('code')}: {rule.get('title')}",
                    f"rule text: {rule.get('rule_text')}",
                    f"category: {rule.get('category')} | rule_type: {rule.get('rule_type')} | "
                    f"enforcement: {rule.get('enforcement')} | severity: {rule.get('severity')}",
                    "",
                    f"OBSERVED FAILURES ({len(items)} findings across {len(review_ids)} reviews), "
                    f"likely cause: {cause} — {detail}",
                ])
                for example in examples:
                    prompt += (f"- [{example['severity']}, {example['evidence_status']}] "
                               f"quoted: {str(example['quoted'])[:120]!r} | why: {str(example['explanation'])[:200]}\n")
                prompt += ("\nReturn one rule in the schema. Set source_quote to the most representative quoted "
                           "failure above. Do not invent policy the evidence does not support.")
                payload = await _ask(pod, prompt, title=f"Policy proposal: {rule.get('code')}")
                if payload and payload.get("rules"):
                    first = payload["rules"][0]
                    proposed_rule = {
                        "title": first.get("title"), "rule_text": first.get("rule_text"),
                        "category": first.get("category") or rule.get("category"),
                        "rule_type": first.get("rule_type") or rule.get("rule_type"),
                        "severity": first.get("severity") or rule.get("severity"),
                        "enforcement": first.get("enforcement") or rule.get("enforcement"),
                        "match_terms": {"terms": first.get("terms") or []} if first.get("terms") else None,
                        "source_quote": first.get("source_quote"),
                        "based_on_rule": rule.get("code"),
                    }
            title = (f"{key} breaches {len(items)} times across {len(review_ids)} reviews"
                     if not proposed_rule else f"Clarify {rule.get('code')}: {proposed_rule.get('title')}")
            row = pod.table("bg_policy_suggestions").create({
                "organization_id": data.organization_id, "title": title[:300],
                "rationale": f"{detail} Recommended action: {action}",
                "root_cause_type": cause, "root_cause_detail": detail,
                "evidence": examples,
                "affected_reviews": review_ids[:25],
                "affected_campaigns": sorted({f.get("campaign") for f in items if f.get("campaign")})[:10],
                "affected_content_types": sorted({f.get("content_type") for f in items if f.get("content_type")})[:10],
                "rule_code": items[0].get("rule_code"), "guideline_id": rule["id"] if rule else None,
                "proposed_rule": proposed_rule, "status": "proposed",
                "confidence_note": ("Counts come from stored findings. A cause is an interpretation of the evidence, "
                                    "not proof: review the supporting cases before changing a rule."),
                "created_by": email, "generated_at": _now()})
            suggestions.append(row)

    if not suggestions and len(reviews) >= 4:
        row = pod.table("bg_policy_suggestions").create({
            "organization_id": data.organization_id,
            "title": "No recurring problem met the reporting threshold",
            "rationale": f"{len(findings)} findings across {len(reviews)} reviews were analysed and no rule reached "
                         f"{data.min_cases} cases in two or more reviews.",
            "root_cause_type": "insufficient_data",
            "root_cause_detail": "Nothing to attribute yet.",
            "evidence": [], "affected_reviews": [], "status": "proposed",
            "confidence_note": "Reported honestly rather than filled with a guess.",
            "created_by": email, "generated_at": _now()})
        suggestions.append(row)

    _audit(pod, ctx, member, data.organization_id, "root_cause.generated", None,
           f"{len(suggestions)} root-cause suggestion(s) computed from {len(reviews)} reviews",
           {"engine": ENGINE_VERSION})
    return BgRootCauseResult(suggestions=suggestions, data_note=note,
                             message=f"{len(suggestions)} suggestion(s) generated. Each one is a proposal for a "
                                     "brand manager to approve, edit or dismiss.")

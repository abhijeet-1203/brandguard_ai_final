#input_type_name: BgInsightsGenerateInput
#output_type_name: BgInsightsGenerateResult
#function_name: bg_insights_generate

"""Governance memory: recurring problems and guideline gaps computed from real review
history. Every number is counted from stored records; where the history is too thin the
platform says so instead of inventing a pattern."""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

MIN_REVIEWS = 4
MIN_CASES = 3
CODE_PREFIX = {
    "tone": "TONE", "terminology": "TERM", "messaging": "MSG", "factual_accuracy": "FACT",
    "promotional_claim": "CLAIM", "visual_identity": "VIS", "accessibility": "A11Y",
    "confidentiality": "CONF", "legal_compliance": "LEGAL", "formatting": "FMT",
}


class BgInsightsGenerateInput(BaseModel):
    organization_id: str
    action: str = "generate"
    days: int = 180
    insight_id: str | None = None
    status: str | None = None
    reason: str | None = None


class BgInsightsGenerateResult(BaseModel):
    insights: list[dict]
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
        "subject_type": "bg_insights", "subject_id": subject_id, "summary": summary,
        "metadata": metadata or {}})


def bg_insights_generate(ctx: FunctionContext, data: BgInsightsGenerateInput) -> BgInsightsGenerateResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    role = member.get("role")

    if data.action == "update_status":
        if role not in ("admin", "brand_manager"):
            raise PermissionError("Only a brand manager or administrator can change an insight's status.")
        if not data.insight_id:
            raise ValueError("insight_id is required.")
        insight = pod.table("bg_insights").get(data.insight_id)
        if insight["organization_id"] != data.organization_id:
            raise PermissionError("This insight belongs to another workspace.")
        updated = pod.table("bg_insights").update(data.insight_id, {"status": data.status or "investigating"})
        _audit(pod, ctx, member, data.organization_id, "insight.status_changed", data.insight_id,
               f"Insight marked '{updated.get('status')}'", {"reason": data.reason})
        return BgInsightsGenerateResult(insights=[updated], message="Insight updated.")

    if data.action == "create_draft_guideline":
        if role not in ("admin", "brand_manager"):
            raise PermissionError("Only a brand manager or administrator can draft a guideline update.")
        if not data.insight_id:
            raise ValueError("insight_id is required.")
        insight = pod.table("bg_insights").get(data.insight_id)
        if insight["organization_id"] != data.organization_id:
            raise PermissionError("This insight belongs to another workspace.")
        existing = pod.records.list("bg_guidelines", limit=500, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
        category = "terminology" if insight["insight_type"] == "recurring_terminology" else "messaging"
        if insight["insight_type"] == "recurring_claim":
            category = "promotional_claim"
        prefix = CODE_PREFIX.get(category, "RULE")
        used = [g.get("code", "") for g in existing if str(g.get("code", "")).startswith(prefix + "-")]
        draft = pod.table("bg_guidelines").create({
            "organization_id": data.organization_id,
            "code": f"{prefix}-{len(used) + 1:03d}",
            "title": ("Clarify: " + insight["title"])[:300],
            "description": insight.get("recommended_action"),
            "category": category, "rule_type": "messaging_rule" if category == "messaging" else "prohibited_term",
            "severity": "medium", "enforcement": "recommended",
            "rule_text": insight.get("recommended_action") or insight.get("description") or insight["title"],
            "version": 1, "status": "draft", "created_by": ctx.user_id, "created_by_email": email,
            "notes": f"Drafted from governance insight {insight['id']} ({insight.get('cases_count')}/"
                     f"{insight.get('total_count')} cases). Requires approval before it is enforced.",
            "match_terms": {"terms": [insight.get("guideline_code")] if insight.get("guideline_code") else []},
        })
        pod.table("bg_guideline_versions").create({
            "guideline_id": draft["id"], "version": 1, "snapshot": draft,
            "change_summary": "Draft created from a governance insight",
            "changed_by": ctx.user_id, "changed_by_email": email})
        updated = pod.table("bg_insights").update(data.insight_id, {
            "status": "draft_created", "draft_guideline_id": draft["id"]})
        _audit(pod, ctx, member, data.organization_id, "insight.draft_created", data.insight_id,
               f"Draft guideline {draft['code']} created from insight", {"draft_guideline_id": draft["id"]})
        return BgInsightsGenerateResult(insights=[updated],
                                        message=f"Draft rule {draft['code']} created from this insight. It stays a "
                                                "draft until an authorised person approves it - the platform never "
                                                "rewrites official guidelines on its own.")

    # ---- generate
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, data.days))).isoformat()
    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    reviews = [r for r in reviews if (r.get("created_at") or "") >= since]
    review_ids = {r["id"] for r in reviews}
    findings = [f for f in pod.records.list("bg_findings", limit=1000).to_dict()["items"]
                if f.get("review_id") in review_ids]
    guidelines = pod.records.list("bg_guidelines", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    g_by_id = {g["id"]: g for g in guidelines}
    corrections = pod.records.list("bg_corrections", limit=500).to_dict()["items"]
    decisions = [d for d in pod.records.list("bg_approvals", limit=500).to_dict()["items"]
                 if d.get("review_id") in review_ids]
    period_start = min([(r.get("created_at") or "")[:10] for r in reviews], default=None)
    period_end = datetime.now(timezone.utc).date().isoformat()

    # refresh only the auto-generated, still-new insights
    old = pod.records.list("bg_insights", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    stale = [i["id"] for i in old if i.get("status") == "new"]
    if stale:
        pod.records.bulk_delete("bg_insights", stale)

    generated = []

    def add(itype, title, description, cases, total, action, guideline=None, note=None, review_ids_list=None):
        row = pod.table("bg_insights").create({
            "organization_id": data.organization_id, "insight_type": itype, "title": title[:300],
            "description": description, "cases_count": cases, "total_count": total,
            "period_start": period_start, "period_end": period_end,
            "supporting_review_ids": (review_ids_list or [])[:25],
            "guideline_id": guideline["id"] if guideline else None,
            "guideline_code": guideline.get("code") if guideline else None,
            "recommended_action": action, "status": "new", "confidence_note": note,
            "generated_at": _now()})
        generated.append(row)

    if len(reviews) < MIN_REVIEWS:
        add("insufficient_data", "Not enough review data to identify reliable recurring patterns",
            f"This workspace has {len(reviews)} review(s) in the selected period. Patterns are only reported once "
            f"there are at least {MIN_REVIEWS} reviews and {MIN_CASES} cases of the same issue.",
            0, len(reviews),
            "Run more reviews so the platform can distinguish a recurring problem from a one-off mistake.",
            note="No pattern is claimed from this sample.")
        return BgInsightsGenerateResult(insights=generated,
                                        data_note=f"Only {len(reviews)} review(s) in the last {data.days} days.",
                                        message="Insufficient history for pattern detection.")

    # recurring issues by rule
    by_rule = defaultdict(list)
    for f in findings:
        if f.get("rule_id") and f.get("review_status") in ("open", "accepted", "resolved", "escalated"):
            by_rule[f["rule_id"]].append(f)
    for rule_id, items in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        rule = g_by_id.get(rule_id)
        distinct_reviews = {i["review_id"] for i in items}
        if len(items) < MIN_CASES or len(distinct_reviews) < 2:
            continue
        itype = {"terminology": "recurring_terminology", "promotional_claim": "recurring_claim",
                 "visual_identity": "asset_misuse", "visual": "asset_misuse",
                 "accessibility": "high_revision_rate", "confidentiality": "recurring_claim"}.get(
            (rule or {}).get("category"), "recurring_terminology")
        if (rule or {}).get("category") == "terminology":
            itype = "recurring_terminology"
        add(itype,
            f"Recurring breach of rule {(rule or {}).get('code', 'unknown')}: {(rule or {}).get('title', '')}",
            f"Rule {(rule or {}).get('code')} was breached in {len(distinct_reviews)} of {len(reviews)} reviewed items "
            f"in this period ({len(items)} findings).",
            len(distinct_reviews), len(reviews),
            f"Investigate why '{(rule or {}).get('title')}' is repeatedly missed. If the rule is ambiguous or easy to "
            "overlook, clarify the rule text or add it to the authoring checklist.",
            guideline=rule,
            note="Association only: the rule's presence in findings does not prove the rule caused the problem.")

    # guideline ambiguity: same rule producing ambiguous/unverified findings repeatedly
    ambiguous = defaultdict(int)
    for f in findings:
        if f.get("evidence_status") in ("ambiguous", "unverified") and f.get("rule_id"):
            ambiguous[f["rule_id"]] += 1
    for rule_id, count in sorted(ambiguous.items(), key=lambda kv: -kv[1]):
        if count < 2:
            continue
        rule = g_by_id.get(rule_id)
        items = [f for f in findings if f.get("rule_id") == rule_id]
        add("guideline_ambiguity",
            f"Rule {(rule or {}).get('code')} repeatedly produces findings that need human judgement",
            f"{count} finding(s) from this rule were ambiguous or unverified - reviewers have to interpret the rule "
            "case by case, which risks inconsistent decisions.",
            count, len(items),
            f"Clarify when '{(rule or {}).get('title')}' applies and when it does not. A precise rule removes the "
            "guesswork without changing the brand's intent.",
            guideline=rule,
            note="This is an interpretation signal, not proof that the rule itself is wrong.",
            review_ids_list=[f["review_id"] for f in items])

    # rejected corrections
    rejected = defaultdict(int)
    for c in corrections:
        if c.get("status") == "rejected" and c.get("finding_id"):
            rejected[c["finding_id"]] += 1
    rej_findings = [f for f in findings if rejected.get(f["id"])]
    if len(rej_findings) >= 2:
        add("rejected_corrections",
            f"{len(rej_findings)} suggested corrections were rejected by reviewers",
            "Reviewers judged the AI suggestions unsuitable in these cases. That is usually a sign that the "
            "suggestion did not respect context, or that the rule behind it is too rigid.",
            len(rej_findings), len(findings),
            "Review the rejected suggestions and consider whether the affected rules need context-specific wording.",
            note="Small samples of rejected suggestions can reflect individual reviewer preference.",
            review_ids_list=[f["review_id"] for f in rej_findings])

    # high revision rate by content type
    by_type = defaultdict(list)
    for r in reviews:
        by_type[r.get("content_type")].append(r)
    for ctype, items in by_type.items():
        if len(items) < MIN_CASES:
            continue
        revised = [r for r in items if (r.get("current_version") or 1) > 1]
        if len(revised) >= max(2, int(len(items) * 0.5)):
            add("high_revision_rate",
                f"{len(revised)} of {len(items)} {str(ctype).replace('_', ' ')} reviews needed revision",
                f"Content of this type is revised after its first review more often than others "
                f"({len(revised)}/{len(items)} in this period).",
                len(revised), len(items),
                "Check whether the brief or the authoring template for this content type is missing the guidance "
                "the reviewers keep asking for.",
                note="Revision rate can also reflect a strict reviewer rather than a weak brief.",
                review_ids_list=[r["id"] for r in revised])

    # reviewer disagreement
    by_review = defaultdict(set)
    for d in decisions:
        if d.get("kind") == "decision":
            by_review[d["review_id"]].add(d.get("status"))
    disputed = [rid for rid, statuses in by_review.items()
                if ("approved" in statuses and ("changes_requested" in statuses or "rejected" in statuses))]
    if len(disputed) >= 2:
        add("reviewer_disagreement",
            f"Reviewers reached different decisions on {len(disputed)} reviews",
            "The same submission received both an approval and a change request or rejection. This usually points at "
            "unclear criteria rather than at the reviewers.",
            len(disputed), len(by_review),
            "Agree the acceptance criteria for the affected content type, or add worked examples to the guideline.",
            note="Counts recorded decisions only; it does not evaluate any individual reviewer's performance.",
            review_ids_list=disputed)

    if not generated:
        add("insufficient_data", "No reliable recurring pattern found in this period",
            f"{len(reviews)} reviews and {len(findings)} findings were analysed and no issue met the reporting "
            f"threshold (at least {MIN_CASES} cases across two or more reviews).",
            0, len(findings),
            "Keep reviewing. Patterns are reported only when the evidence supports them.",
            note="Absence of a pattern is reported honestly rather than filled with a guess.")

    _audit(pod, ctx, member, data.organization_id, "insights.generated", None,
           f"{len(generated)} governance insight(s) computed from {len(reviews)} reviews",
           {"period_days": data.days, "findings": len(findings)})
    return BgInsightsGenerateResult(
        insights=generated, data_note=f"{len(reviews)} reviews and {len(findings)} findings analysed.",
        message=f"{len(generated)} insight(s) computed from actual review history.")

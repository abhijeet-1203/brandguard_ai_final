#input_type_name: BgRiskScoreInput
#output_type_name: BgRiskScoreResult
#function_name: bg_risk_score

"""Explainable brand-risk scoring.

Produces a 0-100 score from transparent factors (finding severity, claim uncertainty,
evidence expiry, claim sensitivity, audience exposure, content type, creator history,
revision history and blocked policy findings) and records every factor with its points and
reason, so a reviewer can see exactly why a submission is ranked where it is.

The score is a triage aid built from weighted signals. It is NOT a calibrated probability of
error, and the app labels it that way.
"""
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-risk-1.0"
SENSITIVE_CATEGORIES = ("promotional_claim", "confidentiality", "legal_compliance")
PUBLIC_AUDIENCE_HINTS = ("customer", "consumer", "public", "home owner", "shopper", "general",
                         "prospect", "parent", "buyer", "partner", "retail")
PUBLIC_CHANNEL_HINTS = ("instagram", "facebook", "linkedin", "tiktok", "twitter", "x", "website",
                        "web", "blog", "email", "print", "display", "ads", "banner", "landing", "facebook")
EXPOSED_TYPES = ("social_post", "advertisement", "promotional_banner", "landing_page",
                 "marketing_email", "product_description")
CLAIM_WORDS = ("guarantee", "best", "number one", "#1", "100%", "clinically", "unbeatable",
               "cures", "carbon neutral", "risk-free", "outperforms", "world's best")


class BgRiskScoreInput(BaseModel):
    organization_id: str
    review_id: str | None = None
    action: str = "compute"


class BgRiskScoreResult(BaseModel):
    scores: list[dict]
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


def compute_factors(pod, review, findings, facts, history):
    factors = []
    text = (review.get("current_text") or "") or ""
    lower = text.lower()

    open_findings = [f for f in findings if f.get("review_status") in ("open", "escalated", "accepted")]
    worst = None
    for f in open_findings:
        if worst is None or {"critical": 4, "high": 3, "medium": 2, "low": 1, "informational": 0}.get(
                f["severity"], 0) > {"critical": 4, "high": 3, "medium": 2, "low": 1, "informational": 0}.get(worst, 0):
            worst = f["severity"]
    severity_points = {"critical": 30, "high": 20, "medium": 8, "low": 3, "informational": 0}.get(worst, 0)
    if severity_points:
        factors.append({"factor": "Worst open finding", "points": severity_points, "severity": worst,
                        "reason": f"An unresolved {worst} finding is attached to this version."})
    extra = max(0, len([f for f in open_findings if f["severity"] in ("critical", "high")]) - 1)
    if extra:
        points = min(10, extra * 3)
        factors.append({"factor": "Volume of serious findings", "points": points,
                        "reason": f"{extra} further high or critical finding(s) are open."})

    uncertain = [f for f in open_findings if f.get("evidence_status") in ("not_found", "ambiguous", "unverified")]
    if uncertain:
        points = min(18, len(uncertain) * 6)
        factors.append({"factor": "Claim uncertainty", "points": points,
                        "reason": f"{len(uncertain)} finding(s) could not be settled by approved evidence "
                                  f"({', '.join(sorted({f['evidence_status'] for f in uncertain}))})."})

    expired, expiring = [], []
    for f in open_findings:
        ref = f.get("source_reference") or {}
        days = _days_until(ref.get("expiration_date"))
        if days is None:
            continue
        if days < 0:
            expired.append(f)
        elif days <= 60:
            expiring.append(f)
    if expired:
        factors.append({"factor": "Expired evidence", "points": min(18, 12 + 2 * (len(expired) - 1)),
                        "reason": f"{len(expired)} finding(s) rest on an approved source that has expired."})
    elif expiring:
        factors.append({"factor": "Evidence about to expire", "points": 6,
                        "reason": f"{len(expiring)} finding(s) reference a source expiring within 60 days."})

    sensitive = [f for f in open_findings if f["category"] in SENSITIVE_CATEGORIES]
    if sensitive:
        factors.append({"factor": "Sensitive claim categories", "points": min(16, 10 + 2 * (len(sensitive) - 1)),
                        "reason": f"Open findings touch {', '.join(sorted({f['category'].replace('_', ' ') for f in sensitive}))}."})
    if any(word in lower for word in CLAIM_WORDS):
        hit = next(w for w in CLAIM_WORDS if w in lower)
        factors.append({"factor": "Promotional claim wording", "points": 6,
                        "reason": f"The copy contains claim language (“{hit}”), which raises the cost of an error."})

    audience = (review.get("audience") or "").lower()
    channel = (review.get("channel") or "").lower()
    public_audience = any(h in audience for h in PUBLIC_AUDIENCE_HINTS)
    public_channel = any(h in channel for h in PUBLIC_CHANNEL_HINTS)
    if public_audience or public_channel:
        factors.append({"factor": "Public exposure", "points": 6,
                        "reason": f"Intended for a public audience ({review.get('audience') or 'unspecified'}) "
                                  f"on {review.get('channel') or 'an unspecified channel'}."})
    if review.get("content_type") in EXPOSED_TYPES:
        factors.append({"factor": "Content type reach", "points": 4,
                        "reason": f"{str(review.get('content_type')).replace('_', ' ')} content is widely seen "
                                  "and hard to withdraw."})

    decided = history.get("decided", 0)
    reworked = history.get("reworked", 0)
    if decided >= 3 and reworked / max(1, decided) >= 0.5:
        factors.append({"factor": "Creator's recent outcome history", "points": 8,
                        "reason": f"{reworked} of this creator's last {decided} decided submissions needed changes "
                                  "or were rejected."})

    if (review.get("current_version") or 1) > 1:
        factors.append({"factor": "Already revised", "points": 4,
                        "reason": f"This is version {review.get('current_version')}; the first pass did not clear it."})

    blocking = [f for f in open_findings if f["severity"] in ("critical", "high")]
    if blocking and any(f["category"] in SENSITIVE_CATEGORIES or f.get("rule_code") for f in blocking):
        factors.append({"factor": "Policy blocking risk", "points": 8,
                        "reason": "At least one open finding would block approval under the current policy "
                                  "without a documented override."})

    if not factors:
        factors.append({"factor": "No risk signals", "points": 0,
                        "reason": "No open findings, uncertain claims or exposure signals were recorded."})

    score = max(0.0, min(100.0, float(sum(f["points"] for f in factors))))
    band = "critical" if score >= 75 else "high" if score >= 50 else "medium" if score >= 25 else "low"
    top = sorted(factors, key=lambda f: -f["points"])[:3]
    explanation = ("Score " + str(int(score)) + "/100 (" + band + "). Main reasons: "
                   + "; ".join(f"{f['factor']} (+{f['points']})" for f in top if f["points"] > 0)
                   + ". This is a weighted triage aid, not a calibrated probability of error.")
    return factors, score, band, explanation


def bg_risk_score(ctx: FunctionContext, data: BgRiskScoreInput) -> BgRiskScoreResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    if not _member(pod, data.organization_id, email):
        raise PermissionError("You are not a member of this workspace.")

    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    if data.review_id:
        reviews = [r for r in reviews if r["id"] == data.review_id]
        if not reviews:
            raise ValueError("That review does not belong to this workspace.")
    facts = pod.records.list("bg_facts", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    all_findings = pod.records.list("bg_findings", limit=1000).to_dict()["items"]
    existing_scores = pod.records.list("bg_risk_scores", limit=1000, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]

    if data.action == "sync_outcomes":
        synced = 0
        for score in existing_scores:
            review = next((r for r in reviews if r["id"] == score["review_id"]), None)
            if not review:
                continue
            outcome = {"approved": "approved", "rejected": "rejected",
                       "changes_requested": "changes_requested", "archived": "archived"}.get(review.get("status"))
            if outcome and score.get("outcome") != outcome:
                pod.table("bg_risk_scores").update(score["id"], {
                    "outcome": outcome, "outcome_at": review.get("decided_at") or _now(),
                    "outcome_source": f"review status '{review.get('status')}'"})
                synced += 1
        return BgRiskScoreResult(scores=[], message=f"Recorded {synced} outcome(s) for scoring evaluation.")

    by_review = {}
    for f in all_findings:
        by_review.setdefault(f["review_id"], []).append(f)

    results = []
    for review in reviews:
        # creator history from recorded decisions in this workspace
        creator = review.get("created_by_email")
        decided = reworked = 0
        for other in reviews:
            if other.get("created_by_email") != creator or other["id"] == review["id"]:
                continue
            if other.get("status") in ("approved", "rejected", "changes_requested"):
                decided += 1
                if other["status"] in ("rejected", "changes_requested"):
                    reworked += 1
        factors, score, band, explanation = compute_factors(
            pod, review, by_review.get(review["id"], []), facts, {"decided": decided, "reworked": reworked})

        version = review.get("current_version") or 1
        prior = next((s for s in existing_scores
                      if s["review_id"] == review["id"] and s.get("version") == version), None)
        payload = {"organization_id": data.organization_id, "review_id": review["id"], "version": version,
                   "score": round(score, 1), "band": band, "factors": factors, "explanation": explanation,
                   "engine_version": ENGINE_VERSION, "computed_at": _now()}
        if prior:
            pod.table("bg_risk_scores").update(prior["id"], payload)
            score_id = prior["id"]
        else:
            created = pod.table("bg_risk_scores").create({**payload, "outcome": "pending"})
            score_id = created["id"]
        pod.table("bg_reviews").update(review["id"], {
            "risk_score": round(score, 1), "risk_band": band, "risk_factors": factors})
        results.append({"review_id": review["id"], "title": review["title"], "version": version,
                        "score": round(score, 1), "band": band, "factors": factors,
                        "explanation": explanation, "score_id": score_id})

    results.sort(key=lambda r: -r["score"])
    if results:
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "risk.scored", "subject_type": "bg_risk_scores", "subject_id": None,
            "summary": f"Risk scored for {len(results)} review(s); highest {results[0]['score']} "
                       f"({results[0]['band']})",
            "metadata": {"engine": ENGINE_VERSION, "bands": {b: len([r for r in results if r['band'] == b])
                                                             for b in ('critical', 'high', 'medium', 'low')}}})
    return BgRiskScoreResult(scores=results, message=f"Scored {len(results)} review(s).")

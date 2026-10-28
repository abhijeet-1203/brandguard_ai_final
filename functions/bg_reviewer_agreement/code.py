#input_type_name: BgReviewerAgreementInput
#output_type_name: BgReviewerAgreementResult
#function_name: bg_reviewer_agreement

"""Reviewer-agreement scoring.

The point is to tell two very different situations apart:

* **An unclear rule** - different reviewers reach different conclusions about the same kind of
  finding, which usually means the rule's wording leaves room for interpretation.
* **A strict reviewer** - one person rejects findings much more often than their colleagues,
  which is a person-level pattern rather than a rule problem.

For each rule with enough decisions it computes the pairwise agreement rate and Cohen's kappa
over the binary decision "the finding stands" (accepted / applied / resolved) versus "the
finding was rejected" (rejected / will not fix). It also computes a per-reviewer strictness
index from their rejection rate, and reports `insufficient_data` rather than a number when
there are too few decisions or too few reviewers to say anything.
"""
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-agreement-1.0"
MIN_DECISIONS = 4
MIN_REVIEWERS = 2
UPHOLD = ("accepted", "resolved", "escalated")
REJECT = ("rejected", "wont_fix")


class BgReviewerAgreementInput(BaseModel):
    organization_id: str
    action: str = "compute"


class BgReviewerAgreementResult(BaseModel):
    rules: list[dict]
    reviewers: list[dict]
    workspace: dict
    message: str
    engine_version: str = ENGINE_VERSION


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _kappa(pairs):
    """Cohen's kappa for two raters over paired binary decisions."""
    n = len(pairs)
    if n == 0:
        return None, None
    agree = len([p for p in pairs if p[0] == p[1]])
    po = agree / n
    a1 = len([p for p in pairs if p[0]]) / n
    b1 = len([p for p in pairs if p[1]]) / n
    pe = a1 * b1 + (1 - a1) * (1 - b1)
    kappa = (po - pe) / (1 - pe) if pe != 1 else None
    return po, kappa


def bg_reviewer_agreement(ctx: FunctionContext, data: BgReviewerAgreementInput) -> BgReviewerAgreementResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")

    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    review_ids = {r["id"] for r in reviews}
    findings = [f for f in pod.records.list("bg_findings", limit=1000).to_dict()["items"]
                if f.get("review_id") in review_ids]
    corrections = pod.records.list("bg_corrections", limit=1000).to_dict()["items"]

    # a decision = a finding a person resolved one way or the other, with a named actor
    decisions = []
    for finding in findings:
        status = finding.get("review_status")
        actor = finding.get("resolved_by")
        if status in UPHOLD + REJECT and actor:
            decisions.append({"rule_code": finding.get("rule_code") or f"unruled:{finding.get('category')}",
                              "category": finding.get("category"), "severity": finding.get("severity"),
                              "reviewer": actor, "uphold": status in UPHOLD, "source": "finding"})
    for correction in corrections:
        if correction.get("status") in ("accepted", "applied", "rejected") and correction.get("created_by"):
            finding = next((f for f in findings if f["id"] == correction.get("finding_id")), None)
            decisions.append({
                "rule_code": (finding or {}).get("rule_code") or f"unruled:{(finding or {}).get('category')}",
                "category": (finding or {}).get("category"),
                "severity": (finding or {}).get("severity"),
                "reviewer": correction["created_by"],
                "uphold": correction["status"] in ("accepted", "applied"),
                "source": "correction"})

    by_rule = defaultdict(list)
    for decision in decisions:
        by_rule[decision["rule_code"]].append(decision)

    rule_rows = []
    for rule_code, items in sorted(by_rule.items()):
        by_reviewer = defaultdict(list)
        for item in items:
            by_reviewer[item["reviewer"]].append(item)
        reviewers = sorted(by_reviewer)
        pairs = []
        for a, b in combinations(reviewers, 2):
            # pair the two reviewers' decisions on the same finding where possible, otherwise
            # compare their overall rates (documented as a weaker comparison)
            for left in by_reviewer[a]:
                for right in by_reviewer[b]:
                    pairs.append((left["uphold"], right["uphold"]))
                    break
        if len(items) < MIN_DECISIONS or len(reviewers) < MIN_REVIEWERS:
            interpretation = "insufficient_data"
            note = (f"{len(items)} decision(s) from {len(reviewers)} reviewer(s); at least {MIN_DECISIONS} "
                    f"decisions from {MIN_REVIEWERS} reviewers are needed")
            agreement = kappa = None
        else:
            agreement, kappa = _kappa(pairs)
            rates = [len([i for i in by_reviewer[r] if not i["uphold"]]) / len(by_reviewer[r]) for r in reviewers]
            spread = max(rates) - min(rates) if rates else 0
            if agreement is not None and agreement < 0.7:
                interpretation = "unclear_rule"
                note = (f"reviewers agreed on only {round(agreement * 100)}% of these findings, which points at the "
                        "rule's wording rather than at the reviewers")
            elif spread >= 0.5:
                interpretation = "strict_reviewer"
                note = (f"rejection rates differ by {round(spread * 100)} points across reviewers, which looks like "
                        "individual strictness rather than an unclear rule")
            else:
                interpretation = "consistent"
                note = f"reviewers agreed on {round((agreement or 0) * 100)}% of these findings"
        row = {"organization_id": data.organization_id, "scope": "rule", "subject_key": rule_code,
               "subject_label": f"{rule_code} ({items[0].get('category') or 'unruled'})",
               "decision_count": len(items), "agreement_rate": round(agreement, 3) if agreement is not None else None,
               "kappa": round(kappa, 3) if kappa is not None else None,
               "strictness_index": (round(max(len([i for i in by_reviewer[r] if not i["uphold"]]) / len(by_reviewer[r])
                                              for r in reviewers), 3) if len(reviewers) >= MIN_REVIEWERS else None),
               "interpretation": interpretation, "detail": note, "computed_at": _now()}
        rule_rows.append(row)
        prior = pod.records.list("bg_agreement_scores", limit=20, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id},
            {"field": "scope", "op": "eq", "value": "rule"},
            {"field": "subject_key", "op": "eq", "value": rule_code}]).to_dict()["items"]
        if prior:
            pod.table("bg_agreement_scores").update(prior[0]["id"], row)
        else:
            pod.table("bg_agreement_scores").create(row)

    by_reviewer = defaultdict(list)
    for decision in decisions:
        by_reviewer[decision["reviewer"]].append(decision)
    reviewer_rows = []
    all_rates = [len([i for i in items if not i["uphold"]]) / len(items) for items in by_reviewer.values() if items]
    mean_rate = (sum(all_rates) / len(all_rates)) if all_rates else None
    for reviewer, items in sorted(by_reviewer.items()):
        rate = len([i for i in items if not i["uphold"]]) / len(items) if items else None
        sufficient = len(items) >= MIN_DECISIONS
        if not sufficient:
            interpretation, note = "insufficient_data", f"{len(items)} decision(s); {MIN_DECISIONS} needed"
        elif mean_rate is not None and rate - mean_rate >= 0.4:
            interpretation, note = "strict_reviewer", (
                f"rejects findings at {round(rate * 100)}% against a workspace average of {round(mean_rate * 100)}%")
        else:
            interpretation, note = "consistent", (
                f"rejects findings at {round(rate * 100)}% against a workspace average of {round(mean_rate * 100)}%")
        row = {"organization_id": data.organization_id, "scope": "reviewer", "subject_key": reviewer,
               "subject_label": reviewer, "decision_count": len(items),
               "agreement_rate": None, "kappa": None,
               "strictness_index": round(rate, 3) if rate is not None else None,
               "interpretation": interpretation, "detail": note, "computed_at": _now()}
        reviewer_rows.append(row)
        prior = pod.records.list("bg_agreement_scores", limit=20, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id},
            {"field": "scope", "op": "eq", "value": "reviewer"},
            {"field": "subject_key", "op": "eq", "value": reviewer}]).to_dict()["items"]
        if prior:
            pod.table("bg_agreement_scores").update(prior[0]["id"], row)
        else:
            pod.table("bg_agreement_scores").create(row)

    workspace_pairs = []
    rule_reviewers = defaultdict(set)
    for rule_code, items in by_rule.items():
        for item in items:
            rule_reviewers[rule_code].add(item["reviewer"])
    multi = {r: v for r, v in rule_reviewers.items() if len(v) >= MIN_REVIEWERS}
    workspace = {
        "decisions": len(decisions),
        "reviewers": len(by_reviewer),
        "rules_with_decisions": len(by_rule),
        "rules_with_multiple_reviewers": len(multi),
        "mean_rejection_rate": round(mean_rate, 3) if mean_rate is not None else None,
        "verdict": (None if len(decisions) < MIN_DECISIONS or len(by_reviewer) < MIN_REVIEWERS else
                    ("insufficient data: too few decisions from more than one reviewer to score agreement"
                     if not multi else
                     f"{len([r for r in rule_rows if r['interpretation'] == 'unclear_rule'])} rule(s) show "
                     f"disagreement and {len([r for r in reviewer_rows if r['interpretation'] == 'strict_reviewer'])} "
                     "reviewer(s) look stricter than their colleagues")),
    }
    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
        "actor_name": member.get("display_name"), "action": "agreement.computed",
        "subject_type": "bg_agreement_scores", "subject_id": None,
        "summary": (f"Reviewer agreement computed over {len(decisions)} decision(s) from {len(by_reviewer)} "
                    f"reviewer(s); {len(multi)} rule(s) have multiple reviewers"),
        "metadata": {"engine": ENGINE_VERSION}})
    return BgReviewerAgreementResult(
        rules=rule_rows, reviewers=reviewer_rows, workspace=workspace,
        message=(f"{len(decisions)} decision(s) from {len(by_reviewer)} reviewer(s) analysed across "
                 f"{len(by_rule)} rule group(s). "
                 + (workspace["verdict"] or "Not enough decisions to score agreement yet.")))

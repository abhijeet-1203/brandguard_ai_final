#input_type_name: BgRiskCalibrationInput
#output_type_name: BgRiskCalibrationResult
#function_name: bg_risk_calibration

"""Calibrate the risk bands against what actually happened.

Takes every scored review that has reached a decision, groups it by predicted band, and
measures the observed rework rate (changes requested or rejected). It then compares the band's
predicted midpoint with the observed rate, tests whether the bands are monotonic, and computes
a Brier score for the binary "needed rework" outcome using score/100 as the forecast.

A band with fewer than MIN_DECIDED decided reviews is reported as `sufficient: false` with an
explicit insufficient-data note - the platform does not present a calibration figure it cannot
support. Nothing here is a claim of statistical calibration; it is the measurement that would
let someone judge it.
"""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-calibration-1.0"
MIN_DECIDED = 5
BANDS = ("low", "medium", "high", "critical")
BAND_MIDPOINT = {"low": 0.125, "medium": 0.375, "high": 0.625, "critical": 0.875}
REWORK_OUTCOMES = ("changes_requested", "rejected")


class BgRiskCalibrationInput(BaseModel):
    organization_id: str
    action: str = "calibrate"


class BgRiskCalibrationResult(BaseModel):
    bands: list[dict]
    reliability: dict
    monotonic: bool | None
    message: str
    engine_version: str = ENGINE_VERSION


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def bg_risk_calibration(ctx: FunctionContext, data: BgRiskCalibrationInput) -> BgRiskCalibrationResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")

    scores = pod.records.list("bg_risk_scores", limit=1000, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    by_id = {r["id"]: r for r in reviews}

    # keep the newest score per review, then sync the outcome from the review's real status
    newest = {}
    for score in scores:
        prior = newest.get(score["review_id"])
        if not prior or (score.get("computed_at") or "") > (prior.get("computed_at") or ""):
            newest[score["review_id"]] = score

    rows, synced = [], 0
    for review_id, score in newest.items():
        review = by_id.get(review_id)
        if not review:
            continue
        outcome = {"approved": "approved", "rejected": "rejected",
                   "changes_requested": "changes_requested"}.get(review.get("status"))
        if outcome and score.get("outcome") != outcome:
            pod.table("bg_risk_scores").update(score["id"], {
                "outcome": outcome, "outcome_at": review.get("decided_at") or _now(),
                "outcome_source": f"review status '{review.get('status')}'"})
            synced += 1
            score = {**score, "outcome": outcome}
        if score.get("outcome") in ("approved", "changes_requested", "rejected"):
            rows.append({"review_id": review_id, "title": review.get("title"), "band": score.get("band"),
                         "score": score.get("score") or 0.0, "outcome": score.get("outcome")})

    bands = []
    for band in BANDS:
        group = [r for r in rows if r["band"] == band]
        decided = len(group)
        rework = len([r for r in group if r["outcome"] in REWORK_OUTCOMES])
        observed = (rework / decided) if decided else None
        sufficient = decided >= MIN_DECIDED
        midpoint = BAND_MIDPOINT[band]
        gap = (observed - midpoint) if observed is not None else None
        note = (None if sufficient else
                f"insufficient data: {decided} decided review(s) in this band; at least {MIN_DECIDED} are needed "
                "before a calibration figure is reported as reliable")
        row = {"organization_id": data.organization_id, "band": band, "predicted_midpoint": midpoint,
               "scored_count": len([r for r in rows if r["band"] == band]) + len(
                   [s for s in newest.values() if s.get("band") == band]) - decided,
               "decided_count": decided, "rework_count": rework,
               "observed_rate": round(observed, 3) if observed is not None else None,
               "sufficient": sufficient, "note": note, "computed_at": _now()}
        bands.append(row)

    scored_with_outcome = [r for r in rows]
    brier = None
    if scored_with_outcome:
        brier = sum((min(1.0, max(0.0, r["score"] / 100.0)) - (1.0 if r["outcome"] in REWORK_OUTCOMES else 0.0)) ** 2
                    for r in scored_with_outcome) / len(scored_with_outcome)
    observed_pairs = [(b["predicted_midpoint"], b["observed_rate"]) for b in bands
                      if b["sufficient"] and b["observed_rate"] is not None]
    monotonic = None
    if len(observed_pairs) >= 2:
        monotonic = all(observed_pairs[i][1] <= observed_pairs[i + 1][1] + 0.05
                        for i in range(len(observed_pairs) - 1))
    reliability = {
        "decided_reviews": len(scored_with_outcome),
        "bands_with_enough_data": len([b for b in bands if b["sufficient"]]),
        "bands_total": len(BANDS),
        "brier_score": round(brier, 4) if brier is not None else None,
        "brier_note": ("mean squared error of score/100 against the binary 'needed rework' outcome; "
                       "lower is better, 0.25 is the score of always predicting 50%"),
        "min_decided_for_reliability": MIN_DECIDED,
        "verdict": (None if not scored_with_outcome else
                    ("insufficient data: no band has enough decided reviews to judge calibration yet"
                     if not any(b["sufficient"] for b in bands) else
                     ("the observed rework rate rises with the predicted band, which is the direction the score "
                      "claims" if monotonic else
                      "the observed rework rate does not rise consistently with the band, so the score's ordering "
                      "is not supported by this sample"))),
        "excluded": (f"{len(newest) - len(scored_with_outcome)} scored review(s) have no decision yet and are "
                     "excluded from calibration"),
    }

    for row in bands:
        prior = pod.records.list("bg_calibration_runs", limit=20, filter=[
            {"field": "organization_id", "op": "eq", "value": data.organization_id},
            {"field": "band", "op": "eq", "value": row["band"]}]).to_dict()["items"]
        if prior:
            pod.table("bg_calibration_runs").update(prior[0]["id"], row)
        else:
            pod.table("bg_calibration_runs").create(row)

    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
        "actor_name": member.get("display_name"), "action": "risk.calibrated",
        "subject_type": "bg_calibration_runs", "subject_id": None,
        "summary": (f"Risk calibration over {len(scored_with_outcome)} decided review(s): "
                    f"{len([b for b in bands if b['sufficient']])}/{len(BANDS)} bands have enough data"),
        "metadata": {"engine": ENGINE_VERSION, "brier": reliability["brier_score"], "monotonic": monotonic}})
    return BgRiskCalibrationResult(
        bands=bands, reliability=reliability, monotonic=monotonic,
        message=(f"{len(scored_with_outcome)} decided review(s) analysed across {len(BANDS)} bands"
                 + (f"; {synced} outcome(s) refreshed." if synced else ".")
                 + (f" {reliability['verdict']}" if reliability["verdict"] else "")))

#input_type_name: BgEvalAccuracyInput
#output_type_name: BgEvalAccuracyResult
#function_name: bg_eval_accuracy

"""Measured accuracy of the compliance engine.

Runs the labelled cases through the **real** engine and compares what it found against what a
person labelled. Nothing is simulated: each case becomes a review in the evaluation workspace,
`bg_review_analyse` analyses it, and the stored findings are scored.

Reported per category and per rule: true positives, false positives, false negatives,
precision, recall and the false-positive rate, each with its sample size. A metric is marked
`insufficient_data` unless it has at least MIN_SUPPORT relevant cases - the platform does not
publish a number it cannot stand behind.

Two modes:
* `deterministic` (default) - the workspace policy disables the AI pass, so this measures the
  rule engine only. It is reproducible and cheap.
* `ai` - runs the full engine including the AI pass for a bounded sample of cases.
"""
import time
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-eval-runner-1.0"
EVAL_SLUG = "brandguard-eval"
MIN_SUPPORT = 5          # relevant cases needed before a metric is reported as reliable
ADVISORY_SEVERITIES = ("low", "informational")
COUNTED_SEVERITIES = ("critical", "high", "medium")


class BgEvalAccuracyInput(BaseModel):
    action: str = "run"
    mode: str = "deterministic"
    max_cases: int = 200
    ai_sample: int = 8
    # A full sweep of the labelled set exceeds one function invocation, so a run is
    # resumable: each call scores the next batch and recomputes the metrics from every
    # result recorded for that run so far.
    run_id: str | None = None
    batch_size: int = 18
    # Scoring the stored analyses of the case reviews is fast and needs no job waiting.
    # "run" analyses anything not yet analysed; "score_stored" only reads what is stored.



class BgEvalAccuracyResult(BaseModel):
    run_id: str | None = None
    mode: str
    case_count: int
    metrics: dict
    per_category: list[dict]
    per_rule: list[dict]
    insufficient: list[dict]
    message: str
    engine_version: str = ENGINE_VERSION


def _now():
    return datetime.now(timezone.utc).isoformat()


def _eval_org(pod):
    orgs = [o for o in pod.records.list("bg_organizations", limit=100).to_dict()["items"]
            if o.get("slug") == EVAL_SLUG]
    if not orgs:
        raise ValueError("The evaluation workspace does not exist yet. Run bg_eval_seed first.")
    return orgs[0]


def _run_job(pod, name, payload, timeout_s=240):
    """Run a function and, when it is asynchronous, wait for it to finish.

    `bg_review_analyse` is a JOB, so calling it returns while the work is still queued -
    reading findings straight away would score an empty result set and report recall 0.
    """
    run = pod.functions.run(name, payload)
    data = run.to_dict() if hasattr(run, "to_dict") else run
    status = str(data.get("status") or "")
    deadline = time.time() + timeout_s
    while status in ("PENDING", "QUEUED", "RUNNING", "STARTED") and time.time() < deadline:
        time.sleep(2)
        refreshed = pod.functions.run_get(name, str(data.get("id")))
        data = refreshed.to_dict() if hasattr(refreshed, "to_dict") else refreshed
        status = str(data.get("status") or "")
    output = data.get("output_data")
    if output is not None and not isinstance(output, dict):
        try:
            output = dict(output)
        except Exception:  # noqa: BLE001
            output = None
    return {"status": status, "error": data.get("error"), "output": output}


def _prf(tp, fp, fn, tn):
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else None
    fpr = fp / (fp + tn) if (fp + tn) else None
    return precision, recall, f1, fpr


def _metric_block(tp, fp, fn, tn, expected_support, detected_support):
    precision, recall, f1, fpr = _prf(tp, fp, fn, tn)
    enough = expected_support >= MIN_SUPPORT and (tp + fp) >= MIN_SUPPORT
    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn, "true_negatives": tn,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "f1": round(f1, 3) if f1 is not None else None,
        "false_positive_rate": round(fpr, 3) if fpr is not None else None,
        "expected_cases": expected_support, "detected_cases": detected_support,
        "sample_size": tp + fp + fn,
        "reliable": enough,
        "note": None if enough else (
            f"insufficient data: {expected_support} case(s) expect this and {tp + fp} finding(s) were produced; "
            f"at least {MIN_SUPPORT} of each are needed before this metric is treated as reliable"),
    }


def bg_eval_accuracy(ctx: FunctionContext, data: BgEvalAccuracyInput) -> BgEvalAccuracyResult:
    pod = ctx.pod
    org = _eval_org(pod)
    org_id = org["id"]
    started = time.time()
    mode = "ai" if data.mode == "ai" else "deterministic"

    if data.action == "latest":
        runs = pod.records.list("bg_eval_runs", limit=20, filter=[
            {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
        runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        if not runs:
            return BgEvalAccuracyResult(mode=mode, case_count=0, metrics={}, per_category=[], per_rule=[],
                                        insufficient=[], message="No evaluation run has been recorded yet.")
        run = runs[0]
        return BgEvalAccuracyResult(run_id=run["id"], mode=run.get("engine_mode") or mode,
                                    case_count=run.get("case_count") or 0, metrics=run.get("metrics") or {},
                                    per_category=run.get("per_category") or [],
                                    per_rule=run.get("per_rule") or [],
                                    insufficient=run.get("insufficient") or [],
                                    message=f"Latest run: {run.get('status')} ({run.get('created_at')}).")

    # the deterministic mode is only honest if the AI pass really is off for this workspace
    policies = pod.records.list("bg_policies", limit=1, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    if mode == "deterministic" and policies and policies[0].get("ai_analysis_enabled") is not False:
        pod.table("bg_policies").update(policies[0]["id"], {"ai_analysis_enabled": False})

    all_cases = [c for c in pod.records.list("bg_eval_cases", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"] if c.get("is_active", True)]
    all_cases.sort(key=lambda c: c.get("case_key") or "")
    if mode == "ai":
        all_cases = [c for c in all_cases if c.get("ai_relevant", True)][:max(1, data.ai_sample)]
    else:
        all_cases = all_cases[:max(1, data.max_cases)]
    by_key = {c.get("case_key"): c for c in all_cases}

    if data.run_id:
        run = pod.table("bg_eval_runs").get(data.run_id)
        if run["organization_id"] != org_id:
            raise PermissionError("That evaluation run belongs to another workspace.")
        run_id = run["id"]
    else:
        run = pod.table("bg_eval_runs").create({
            "organization_id": org_id, "engine_mode": mode, "engine_version": ENGINE_VERSION,
            "case_count": len(all_cases), "status": "running", "started_at": _now(),
            "created_by": ctx.user_email})
        run_id = run["id"]

    recorded = pod.records.list("bg_eval_results", limit=1000, filter=[
        {"field": "run_id", "op": "eq", "value": run_id}]).to_dict()["items"]
    done_keys = {r.get("case_key") for r in recorded}
    use_stored = data.action == "score_stored"
    batch = len(all_cases) if use_stored else max(1, data.batch_size)
    pending = [c for c in all_cases if c.get("case_key") not in done_keys][:batch]

    existing_reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    review_by_title = {r.get("title"): r for r in existing_reviews}

    results, errors = [], []
    all_rules = pod.records.list("bg_guidelines", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    rule_codes = [r.get("code") for r in all_rules]

    for case in pending:
        key = case.get("case_key")
        title = f"eval:{key}"
        review = review_by_title.get(title)
        try:
            if use_stored:
                if not review:
                    raise RuntimeError("no review exists for this case yet - run the evaluation first")
                if review.get("analysis_status") != "completed":
                    raise RuntimeError(f"the stored analysis is '{review.get('analysis_status')}', not completed")
            elif not review:
                created = _run_job(pod, "bg_review_create", {
                    "organization_id": org_id, "title": title, "content_type": case.get("content_type") or "other",
                    "campaign": "evaluation", "content_text": case["content"], "submit": True})
                if created["error"] or not created["output"]:
                    raise RuntimeError(str(created["error"])[:200] or "review creation returned nothing")
                review = pod.table("bg_reviews").get(created["output"]["review_id"])
            if not use_stored:
                analysis = _run_job(pod, "bg_review_analyse", {"review_id": review["id"]})
                if analysis["error"]:
                    raise RuntimeError(str(analysis["error"])[:200])
                review = pod.table("bg_reviews").get(review["id"])
            findings = pod.records.list("bg_findings", limit=500, filter=[
                {"field": "review_id", "op": "eq", "value": review["id"]}]).to_dict()["items"]
            latest_run = review.get("analysis_run_id")
            if latest_run:
                scoped = [f for f in findings if f.get("run_id") == latest_run]
                if scoped:
                    findings = scoped
            if not findings and review.get("analysis_status") != "completed":
                raise RuntimeError(f"analysis did not complete (status {review.get('analysis_status')})")
        except Exception as e:  # noqa: BLE001
            errors.append({"case_key": key, "error": f"{type(e).__name__}: {e}"[:200]})
            continue

        expected_categories = case.get("expected_categories") or []
        expected_rules = case.get("expected_rules") or []
        detected_categories = sorted({f.get("category") for f in findings if f.get("severity") in COUNTED_SEVERITIES})
        detected_rules = sorted({f.get("rule_code") for f in findings if f.get("rule_code")})
        advisory = [f.get("title") for f in findings if f.get("severity") in ADVISORY_SEVERITIES]
        tp = len(set(expected_categories) & set(detected_categories))
        fp = len(set(detected_categories) - set(expected_categories))
        fn = len(set(expected_categories) - set(detected_categories))
        results.append({
            "run_id": run_id, "case_id": case["id"], "case_key": key, "mode": mode,
            "expected_categories": expected_categories, "expected_rules": expected_rules,
            "detected_categories": detected_categories, "detected_rules": detected_rules,
            "detected_evidence": sorted({f.get("evidence_status") for f in findings}),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "outcome_correct": (tp > 0) if expected_categories else (len(detected_categories) == 0),
            "notes": ("advisories: " + "; ".join(str(a) for a in advisory[:3])) if advisory else None})

    if results:
        pod.records.bulk_create("bg_eval_results", results)

    # ---- metrics from every result recorded for this run (so a resumed run accumulates)
    scored = pod.records.list("bg_eval_results", limit=1000, filter=[
        {"field": "run_id", "op": "eq", "value": run_id}]).to_dict()["items"]
    cat, rule = {}, {}
    for row in scored:
        exp_c = set(row.get("expected_categories") or [])
        det_c = set(row.get("detected_categories") or [])
        for category in exp_c | det_c:
            slot = cat.setdefault(category, {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "expected": 0, "detected": 0})
            if category in exp_c and category in det_c:
                slot["tp"] += 1
            elif category in exp_c:
                slot["fn"] += 1
            else:
                slot["fp"] += 1
            if category in exp_c:
                slot["expected"] += 1
            if category in det_c:
                slot["detected"] += 1
        exp_r = set(row.get("expected_rules") or [])
        det_r = set(row.get("detected_rules") or [])
        for code in rule_codes:
            if code not in exp_r and code not in det_r:
                continue
            slot = rule.setdefault(code, {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "expected": 0, "detected": 0})
            if code in exp_r and code in det_r:
                slot["tp"] += 1
            elif code in exp_r:
                slot["fn"] += 1
            else:
                slot["fp"] += 1
            if code in exp_r:
                slot["expected"] += 1
            if code in det_r:
                slot["detected"] += 1

    per_category, per_rule = [], []
    for category, slot in sorted(cat.items()):
        block = _metric_block(slot["tp"], slot["fp"], slot["fn"], slot["tn"], slot["expected"], slot["detected"])
        block["category"] = category
        per_category.append(block)
    for code, slot in sorted(rule.items()):
        block = _metric_block(slot["tp"], slot["fp"], slot["fn"], slot["tn"], slot["expected"], slot["detected"])
        block["rule_code"] = code
        per_rule.append(block)

    tp = sum(r["true_positives"] for r in scored)
    fp = sum(r["false_positives"] for r in scored)
    fn = sum(r["false_negatives"] for r in scored)
    clean_keys = {k for k, c in by_key.items() if not (c.get("expected_categories") or [])}
    flagged_keys = {k for k, c in by_key.items() if (c.get("expected_categories") or [])}
    clean_scored = [r for r in scored if r.get("case_key") in clean_keys]
    flagged_scored = [r for r in scored if r.get("case_key") in flagged_keys]
    overall = _metric_block(tp, fp, fn, len(clean_scored), len(flagged_keys), len(flagged_keys))
    outcome_accuracy = (len([r for r in scored if r["outcome_correct"]]) / len(scored)) if scored else None
    metrics = {
        **overall,
        "cases_run": len(scored), "cases_total": len(all_cases), "cases_errored": len(errors),
        "clean_cases": len(clean_keys), "clean_cases_passed": len([r for r in clean_scored if r["outcome_correct"]]),
        "flagged_cases": len(flagged_keys),
        "flagged_cases_caught": len([r for r in flagged_scored if r["outcome_correct"]]),
        "outcome_accuracy": round(outcome_accuracy, 3) if outcome_accuracy is not None else None,
        "min_support_for_reliability": MIN_SUPPORT,
        "advisory_severities_excluded": list(ADVISORY_SEVERITIES),
        "errors": errors[:10],
    }
    insufficient = [b for b in per_category + per_rule if not b.get("reliable")]

    duration = int((time.time() - started) * 1000)
    complete = len(scored) >= len(all_cases)
    pod.table("bg_eval_runs").update(run_id, {
        "status": "completed" if complete else "running",
        "completed_at": _now() if complete else None, "matched_count": len(scored),
        "metrics": metrics, "per_category": per_category, "per_rule": per_rule,
        "insufficient": [{"scope": b.get("category") or b.get("rule_code"), "note": b.get("note")}
                         for b in insufficient], "duration_ms": duration})
    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": ctx.user_email,
        "action": "eval.run", "subject_type": "bg_eval_runs", "subject_id": run_id,
        "summary": (f"Evaluation run ({mode}) over {len(results)} case(s): "
                    f"precision {metrics.get('precision')}, recall {metrics.get('recall')}, "
                    f"outcome accuracy {metrics.get('outcome_accuracy')}"),
        "metadata": {"mode": mode, "errors": len(errors), "duration_ms": duration}})
    return BgEvalAccuracyResult(
        run_id=run_id, mode=mode, case_count=len(scored), metrics=metrics,
        per_category=per_category, per_rule=per_rule,
        insufficient=[{"scope": b.get("category") or b.get("rule_code"), "note": b.get("note")} for b in insufficient],
        message=(f"{len(scored)}/{len(all_cases)} case(s) scored in {mode} mode"
                 + (f" ({len(results)} in this batch)" if results else "")
                 + (". Run complete. " if complete else ". Run still in progress - call again with "
                    f"run_id {run_id} to continue. ")
                 + f"Precision {metrics.get('precision')}, recall {metrics.get('recall')}, "
                   f"outcome accuracy {metrics.get('outcome_accuracy')}"
                 + (f"; {len(insufficient)} metric(s) marked insufficient data." if insufficient else ".")))

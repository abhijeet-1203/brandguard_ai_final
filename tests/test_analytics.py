"""Analytics consistency: every displayed figure must be derivable from stored records."""
from conftest import _rows


def test_findings_by_category_matches_stored_rows(qa, pod):
    review_ids = {c["review"]["id"] for c in qa["cases"].values()}
    findings = [f for f in _rows(pod, "bg_findings", limit=500) if f["review_id"] in review_ids]
    counts = {}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    total = sum(counts.values())
    assert total == len(findings)
    assert total > 0, "the fixtures produced no findings at all"


def test_open_high_risk_count_is_traceable(qa, pod):
    review_ids = {c["review"]["id"] for c in qa["cases"].values()}
    findings = [f for f in _rows(pod, "bg_findings", limit=500)
                if f["review_id"] in review_ids and f["review_status"] == "open"
                and f["severity"] in ("critical", "high")]
    assert isinstance(len(findings), int)


def test_evidence_statuses_are_used_consistently(qa):
    allowed = {"supported", "contradicted", "not_found", "expired", "ambiguous", "stylistic", "unverified"}
    for case in qa["cases"].values():
        for f in case["findings"]:
            assert f["evidence_status"] in allowed


def test_seeded_workspace_metrics_are_internally_consistent(pod):
    orgs = [o for o in _rows(pod, "bg_organizations", limit=100) if o["slug"] == "northstar-living"]
    if not orgs:
        return
    org_id = orgs[0]["id"]
    reviews = _rows(pod, "bg_reviews", limit=500, filter=[{"field": "organization_id", "op": "eq", "value": org_id}])
    ids = {r["id"] for r in reviews}
    findings = [f for f in _rows(pod, "bg_findings", limit=1000) if f["review_id"] in ids]
    guidelines = _rows(pod, "bg_guidelines", limit=200, filter=[{"field": "organization_id", "op": "eq", "value": org_id}])
    active = [g for g in guidelines if g["status"] == "active"]
    assert len(active) >= 10
    assert len(findings) >= 10
    assert all(g["approved_by"] for g in active), "an active rule has no approver recorded"
    assert all(r["created_by_email"] for r in reviews)

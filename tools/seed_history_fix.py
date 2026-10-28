"""Complete the seeded review history now that the workflow grants are in place."""
import json
import subprocess

from lemma_sdk import Pod

pod = Pod.from_env()
ORG = {o["slug"]: o["id"] for o in pod.records.list("bg_organizations", limit=50).to_dict()["items"]}
PEER = "abhijeet.bansode24@vit.edu"


def run(fn, payload):
    proc = subprocess.run(["lemma", "--output", "json", "functions", "run", fn, "--data", json.dumps(payload)],
                          capture_output=True, text=True)
    try:
        out = json.loads(proc.stdout)
    except Exception:
        print("  !", fn, proc.stdout[-200:], proc.stderr[-200:], flush=True)
        return None
    print(f"  {fn} -> {out.get('status')} {str(out.get('error'))[:140]}", flush=True)
    return out.get("output_data") or {}


def reviews(org):
    return pod.records.list("bg_reviews", limit=100, filter=[
        {"field": "organization_id", "op": "eq", "value": org}]).to_dict()["items"]


# 1. remove leftover harness probe reviews
for rev in pod.records.list("bg_reviews", limit=200).to_dict()["items"]:
    if rev["title"].startswith("Reliability probe"):
        for t in ("bg_segments", "bg_review_versions", "bg_analysis_runs", "bg_findings", "bg_approvals", "bg_comments"):
            rows = pod.records.list(t, limit=200, filter=[{"field": "review_id", "op": "eq", "value": rev["id"]}]).to_dict()["items"]
            if rows:
                pod.records.bulk_delete(t, [r["id"] for r in rows])
        pod.records.bulk_delete("bg_reviews", [rev["id"]])
        print("removed probe review", rev["id"])

# 2. attribute the Meridian submissions to the second member (they were created by the admin)
for rev in reviews(ORG["meridian-foods"]):
    pod.table("bg_reviews").update(rev["id"], {"created_by_email": PEER, "created_by_name": "Abhijeet Bansode",
                                               "created_by": None})
print("meridian attribution set", flush=True)

# 3. decisions and comments
for slug in ("northstar-living", "meridian-foods"):
    org = ORG[slug]
    print("===", slug, flush=True)
    for rev in reviews(org):
        title, status = rev["title"], rev["status"]
        print(f"[{title}] status={status}", flush=True)
        if slug == "northstar-living" and title.startswith("AirPure 300 product page") and status != "approved":
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "Warranty and price both match the approved facts. Clearing this for publication."})
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "approve",
                                       "reason": "Warranty, price and company description all match the approved facts; no open findings."})
        if slug == "northstar-living" and title.startswith("AirPure 300 launch") and status in ("awaiting_review", "resubmitted"):
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "request_changes",
                                       "reason": "Product naming, price and warranty claims all need fixing before this can run. Use the approved spec sheet."})
        if slug == "northstar-living" and title.startswith("Spring brand voice") and status not in ("rejected",):
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "reject",
                                       "reason": "Tone is hype-led and breaches TONE-001 and FMT-001. The voice experiment can continue internally, but this draft is not publishable."})
        if slug == "northstar-living" and title.startswith("Winter offer") and status == "awaiting_review":
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "The discount needs the approved disclaimer, and 'consumers' should be 'customers'. Please revise before the send date."})
        if slug == "northstar-living" and title.startswith("AirPure 300 health"):
            crit = [f for f in pod.records.list("bg_findings", limit=200, filter=[
                {"field": "review_id", "op": "eq", "value": rev["id"]}]).to_dict()["items"]
                if f["severity"] == "critical" and f["review_status"] == "open"]
            if crit:
                run("bg_correction_action", {"organization_id": org, "review_id": rev["id"], "action": "escalate",
                                             "finding_id": crit[0]["id"],
                                             "reason": "Health outcome claim with no approved evidence. Needs compliance sign-off before any decision."})
        if slug == "northstar-living" and title.startswith("AirPure 300 brochure"):
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "The internal-only marker must be removed before this goes to partners."})
        if slug == "meridian-foods" and title.startswith("Heritage Sourdough in-store") and status != "approved":
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "approve",
                                       "reason": "Name spelling and certification claim both match the approved records."})
        if slug == "meridian-foods" and title.startswith("Heritage Sourdough supplier"):
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "Confidential supplier terms cannot appear in a buyer-facing note. Split the document before sharing."})
    run("bg_insights_generate", {"organization_id": org, "action": "generate", "days": 365})
print("HISTORY FIX COMPLETE", flush=True)

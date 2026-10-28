"""Drive the seeded workspaces through the real pipeline so analytics, version history
and audit records are backed by actual runs (no synthetic history)."""
import json
import subprocess
import sys

from lemma_sdk import Pod

pod = Pod.from_env()
ORG = {o["slug"]: o["id"] for o in pod.records.list("bg_organizations", limit=50).to_dict()["items"]}


def run(fn, payload):
    proc = subprocess.run(["lemma", "--output", "json", "functions", "run", fn,
                           "--data", json.dumps(payload)], capture_output=True, text=True)
    try:
        out = json.loads(proc.stdout)
    except Exception:
        print("  !", fn, proc.stdout[-300:], proc.stderr[-300:], flush=True)
        return None
    status = out.get("status")
    data = out.get("output_data") or {}
    print(f"  {fn} -> {status} {str(out.get('error'))[:160]}", flush=True)
    return data


def reviews(org_id):
    return pod.records.list("bg_reviews", limit=100, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]


def findings(review_id):
    return pod.records.list("bg_findings", limit=200, filter=[
        {"field": "review_id", "op": "eq", "value": review_id}]).to_dict()["items"]


for slug in ("northstar-living", "meridian-foods"):
    org = ORG[slug]
    print(f"=== {slug}", flush=True)
    for rev in reviews(org):
        print(f"[{rev['title']}]", flush=True)
        run("bg_review_analyse", {"review_id": rev["id"]})
        fs = findings(rev["id"])
        print("   findings:", len(fs), "risk:", rev.get("risk_level"), flush=True)
        title = rev["title"]

        if slug == "northstar-living" and title.startswith("AirPure 300 launch"):
            term = [f for f in fs if f.get("rule_code") == "TERM-001"]
            if term:
                run("bg_correction_action", {"organization_id": org, "review_id": rev["id"],
                                             "action": "accept", "finding_id": term[0]["id"]})
                run("bg_correction_action", {"organization_id": org, "review_id": rev["id"],
                                             "action": "apply", "finding_id": term[0]["id"]})
                run("bg_review_analyse", {"review_id": rev["id"]})
                run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"],
                                           "action": "request_changes",
                                           "reason": "Product naming, pricing and warranty claims all need fixing "
                                                     "before this can run. Please use the approved spec sheet."})
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "Reminder: the approved product name and the current price are both in "
                                              "the approved facts library. @abhijeet.bansode24@vit.edu please rework "
                                              "the copy and resubmit."})

        if slug == "northstar-living" and title.startswith("AirPure 300 product page"):
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "Checked the warranty and price against the approved facts - both match. "
                                              "Clearing this for publication."})
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "approve",
                                       "reason": "Warranty, price and company description all match the approved "
                                                 "facts; no open findings."})

        if slug == "northstar-living" and title.startswith("Spring brand voice"):
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "reject",
                                       "reason": "Tone is hype-led and breaches TONE-001 and FMT-001. The voice "
                                                 "experiment can continue internally but this draft is not publishable."})

        if slug == "northstar-living" and title.startswith("Winter offer"):
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "The discount needs the approved disclaimer, and 'consumers' should be "
                                              "'customers'. Please revise before the send date."})

        if slug == "northstar-living" and title.startswith("AirPure 300 health"):
            crit = [f for f in fs if f["severity"] == "critical"]
            if crit:
                run("bg_correction_action", {"organization_id": org, "review_id": rev["id"],
                                             "action": "escalate", "finding_id": crit[0]["id"],
                                             "reason": "Health outcome claim with no approved evidence. Needs "
                                                       "compliance sign-off before any decision."})

        if slug == "northstar-living" and title.startswith("AirPure 300 brochure"):
            run("bg_comment_action", {"organization_id": org, "review_id": rev["id"], "action": "add",
                                      "body": "The internal-only marker must be removed before this goes to partners."})

        if slug == "meridian-foods" and title.startswith("Heritage Sourdough in-store"):
            run("bg_review_workflow", {"organization_id": org, "review_id": rev["id"], "action": "approve",
                                       "reason": "Name spelling and certification claim both match the approved "
                                                 "records."})

    run("bg_insights_generate", {"organization_id": org, "action": "generate", "days": 365})
    print("insights done", flush=True)
print("HISTORY COMPLETE", flush=True)

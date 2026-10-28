"""Re-run the compliance analysis on every seeded review after an engine change.

Findings are reconciled by fingerprint, so issues the engine no longer detects are marked
resolved and new ones are added - the history stays honest."""
import json
import subprocess

from lemma_sdk import Pod

pod = Pod.from_env()
orgs = {o["slug"]: o["id"] for o in pod.records.list("bg_organizations", limit=50).to_dict()["items"]}
for slug in ("northstar-living", "meridian-foods"):
    org = orgs.get(slug)
    if not org:
        continue
    print("===", slug, flush=True)
    for rev in pod.records.list("bg_reviews", limit=100, filter=[
            {"field": "organization_id", "op": "eq", "value": org}]).to_dict()["items"]:
        proc = subprocess.run(["lemma", "--output", "json", "functions", "run", "bg_review_analyse",
                               "--data", json.dumps({"review_id": rev["id"]})], capture_output=True, text=True)
        try:
            out = json.loads(proc.stdout)
            data = out.get("output_data") or {}
            print(f"  {rev['title'][:44]:<44} {out.get('status')} findings={data.get('findings_count')} "
                  f"risk={data.get('risk_level')}", flush=True)
        except Exception:
            print("  !", rev["title"], proc.stdout[-200:], flush=True)
    proc = subprocess.run(["lemma", "--output", "json", "functions", "run", "bg_insights_generate",
                           "--data", json.dumps({"organization_id": org, "action": "generate", "days": 365})],
                          capture_output=True, text=True)
    print("  insights:", "ok" if '"COMPLETED"' in proc.stdout else proc.stdout[-160:], flush=True)
print("REANALYSIS COMPLETE", flush=True)

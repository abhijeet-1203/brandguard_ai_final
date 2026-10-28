#!/usr/bin/env bash
# Provision BrandGuard AI workspaces and drive the seeded review history through the real
# pipeline. Safe to re-run: provisioning is idempotent unless you ask for a reset.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ tables, functions, agents"
lemma pods import ./tables
lemma pods import ./functions
lemma pods import ./agents

echo "→ workspaces"
lemma functions run bg_seed_workspace --data '{"slug":"northstar-living"}'
lemma functions run bg_seed_workspace --data '{"slug":"meridian-foods"}'

echo "→ compliance analysis, corrections, approvals and insights"
python tools/seed_history.py
python tools/seed_history_fix.py

echo "→ reliability harness"
ORG=$(python - <<'PY'
from lemma_sdk import Pod
print([o["id"] for o in Pod.from_env().records.list("bg_organizations", limit=50).to_dict()["items"]
       if o["slug"] == "northstar-living"][0])
PY
)
lemma functions run bg_eval_run --data "{\"organization_id\":\"$ORG\"}"

echo "→ app"
(cd apps/brandguard && npm install && npm run build)
lemma apps deploy brandguard --source-dir apps/brandguard --yes
echo "Done. Open the app and the dashboard should show the seeded reviews with findings."

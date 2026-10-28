#input_type_name: BgEvalSeedInput
#output_type_name: BgEvalSeedResult
#function_name: bg_eval_seed

"""Seed the labelled evaluation set.

Creates the evaluation workspace, the rule set the cases are labelled against, and the
labelled cases themselves. Every case states what the engine is expected to do: whether the
content is clean or should be flagged, which categories should fire and which rule codes
should be cited. Idempotent by case key, so it can be re-run after adding cases.

The cases are hand-labelled against the rules created here - they are not generated from
engine output, which is what makes the resulting metrics meaningful.
"""
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

EVAL_SLUG = "brandguard-eval"
ENGINE_VERSION = "bg-eval-1.0"

# --------------------------------------------------------------------------- rules
RULES = [
    dict(code="E-TERM-001", title="Approved product name", category="terminology",
         rule_type="prohibited_term", severity="high", enforcement="mandatory",
         rule_text="Use 'Northstar AirPure 300'. Never 'PureAir Pro', 'Air Pure 300' or 'AirPure300'.",
         match_terms={"terms": ["PureAir Pro", "Air Pure 300", "AirPure300"],
                      "replace_with": ["Northstar AirPure 300"]}),
    dict(code="E-TERM-002", title="Customer, not consumer", category="terminology",
         rule_type="prohibited_term", severity="medium", enforcement="mandatory",
         rule_text="Write 'customer' in customer-facing copy. Do not use 'consumer'.",
         match_terms={"terms": ["consumer", "consumers"], "replace_with": ["customer"]}),
    dict(code="E-TERM-003", title="Deprecated term: air purifier", category="terminology",
         rule_type="deprecated_term", severity="low", enforcement="recommended",
         rule_text="Say 'air purifier' rather than the deprecated 'air cleaner'.",
         match_terms={"terms": ["air cleaner"], "replace_with": ["air purifier"]}),
    dict(code="E-TERM-004", title="Required certification wording", category="terminology",
         rule_type="required_term", severity="medium", enforcement="recommended",
         rule_text="When a certification is mentioned, name it as 'AHAM VERIFIDE'.",
         match_terms={"terms": ["AHAM VERIFIDE"]}),
    dict(code="E-DISC-001", title="Discount disclaimer", category="legal_compliance",
         rule_type="required_disclaimer", severity="high", enforcement="mandatory",
         rule_text="Any promotional discount must carry the approved disclaimer.",
         match_terms={"terms": ["Offer valid while stocks last."],
                      "applies_when": ["discount", "% off", "save", "offer", "deal", "%"]}),
    dict(code="E-MSG-001", title="Positioning language", category="messaging",
         rule_type="messaging_rule", severity="high", enforcement="mandatory",
         rule_text="Do not describe products as 'revolutionary', 'game-changing' or 'world-first'.",
         match_terms={"terms": ["revolutionary", "game-changing", "world-first"]}),
    dict(code="E-CLAIM-001", title="Evidence required for superlatives", category="promotional_claim",
         rule_type="claim_policy", severity="high", enforcement="mandatory",
         rule_text="Absolute superlatives and quantified benefit claims require approved evidence.",
         match_terms={}),
    dict(code="E-CLAIM-002", title="No health outcome claims", category="promotional_claim",
         rule_type="claim_policy", severity="critical", enforcement="mandatory",
         rule_text="Do not claim a product prevents, treats or cures illness without clinical evidence.",
         match_terms={}),
    dict(code="E-CONF-001", title="No internal markers in published content", category="confidentiality",
         rule_type="confidentiality_rule", severity="critical", enforcement="mandatory",
         rule_text="Published content must not carry internal-only, embargo or unreleased markers.",
         match_terms={"terms": ["internal only", "do not distribute", "confidential", "embargoed", "unreleased"]}),
    dict(code="E-FACT-001", title="Facts must match the approved record", category="factual_accuracy",
         rule_type="numeric_fact", severity="high", enforcement="mandatory",
         rule_text="Warranty duration and price must match the current approved record exactly.",
         match_terms={}),
    dict(code="E-TONE-001", title="Brand tone", category="tone", rule_type="tone_rule",
         severity="medium", enforcement="mandatory",
         rule_text="Warm, precise and human. Confident without hype. No stacked exclamation marks.",
         match_terms={}),
    dict(code="E-A11Y-001", title="Accessible marketing content", category="accessibility",
         rule_type="accessibility_rule", severity="medium", enforcement="mandatory",
         rule_text="Every image needs meaningful alt text; link text must describe the destination.",
         match_terms={}),
    dict(code="E-FMT-001", title="Formatting restraint", category="formatting",
         rule_type="formatting_rule", severity="low", enforcement="advisory",
         rule_text="Avoid long runs of capitals and keep exclamation marks to at most one per piece.",
         match_terms={}),
]

FACTS = [
    dict(statement="Northstar AirPure 300 carries a one-year limited warranty.",
         category="warranty", value_text="one-year limited warranty", numeric_value=12, unit="months",
         match_keywords=["warranty", "guarantee", "covered"],
         verification_status="verified", approval_status="approved",
         effective_date="2026-01-01", expiration_date="2027-06-30", source_location="Spec v4 s4.2"),
    dict(statement="Northstar AirPure 300 has a recommended retail price of $499.",
         category="price", value_text="$499", numeric_value=499, unit="currency",
         match_keywords=["$", "price", "retail", "costs", "available for"],
         verification_status="verified", approval_status="approved",
         effective_date="2026-01-01", source_location="Price list 2026"),
    dict(statement="Northstar AirPure 300 filters rooms up to 60 m².",
         category="product_specification", value_text="60 m²", numeric_value=60, unit="m2",
         match_keywords=["filters", "room", "coverage", "m²"],
         verification_status="verified", approval_status="approved",
         effective_date="2026-01-01", source_location="Spec v4 s2.1"),
    dict(statement="Northstar AirPure 300 holds AHAM VERIFIDE certification, smoke CADR 250.",
         category="certification", value_text="AHAM VERIFIDE, smoke CADR 250", numeric_value=None,
         match_keywords=["AHAM", "CADR", "certified"],
         verification_status="verified", approval_status="approved",
         effective_date="2026-02-01", source_location="Certificate AHAM-2026-1188"),
    dict(statement="Northstar Living was founded in 2011.",
         category="company_description", value_text="2011", numeric_value=2011, unit="year",
         match_keywords=["founded", "since"],
         verification_status="verified", approval_status="approved",
         effective_date="2015-01-01", expiration_date="2024-12-31", source_location="Fact sheet (superseded)"),
    dict(statement="The current launch offer is 15% off the recommended retail price.",
         category="discount", value_text="15%", numeric_value=15, unit="percent",
         match_keywords=["discount", "% off", "offer", "save"],
         verification_status="verified", approval_status="approved",
         effective_date="2026-09-01", expiration_date="2027-03-31", source_location="Campaign brief"),
    dict(statement="Claim 'removes 99.9% of airborne viruses' is not substantiated. No approved "
                   "laboratory evidence exists.",
         category="promotional_claim", value_text=None, numeric_value=None,
         match_keywords=["99.9%", "viruses", "pathogens"],
         verification_status="no_evidence", approval_status="rejected", source_location="Claims register C-2026-014"),
]

CLEAN = "The Northstar AirPure 300 filters rooms up to 60 m² and carries a one-year limited warranty. Recommended retail price: $499."

# --------------------------------------------------------------------------- cases
# (key, content, outcome, expected categories, expected rule codes, note)
CASES: list[tuple] = [
    # ---- controls: clean content must not be flagged
    ("clean-01", CLEAN, "clean", [], [], "approved facts quoted verbatim"),
    ("clean-02", "Northstar Living designs and sells home essentials from Bristol in the United Kingdom.",
     "clean", [], [], "plain company description"),
    ("clean-03", "The Northstar AirPure 300 holds AHAM VERIFIDE certification with a smoke CADR of 250.",
     "clean", [], ["E-TERM-004"], "certification named as the required term"),
    ("clean-04", "Cleaner air, quieter nights. The Northstar AirPure 300 filters a 60 m² room in 20 minutes.",
     "clean", [], [], "on-tone product copy"),
    ("clean-05", "Save 15% on the Northstar AirPure 300. Offer valid while stocks last.",
     "clean", [], [], "discount with the approved disclaimer"),
    ("clean-06", "Our customers tell us the AirPure 300 is easy to live with.",
     "clean", [], [], "approved term 'customers' used"),
    ("clean-07", "Every customer receives a one-year limited warranty on the AirPure 300.",
     "clean", [], [], "warranty matches the approved record"),
    ("clean-08", "The AirPure 300 is available for $499.",
     "clean", [], [], "price matches the approved record"),

    # ---- terminology
    ("term-01", "Meet the PureAir Pro, our newest purifier.", "flagged", ["terminology"], ["E-TERM-001"], "prohibited name"),
    ("term-02", "The PureAir Pro is now available in the UK.", "flagged", ["terminology"], ["E-TERM-001"], "prohibited name"),
    ("term-03", "AirPure300 filters your room in minutes.", "flagged", ["terminology"], ["E-TERM-001"], "prohibited name"),
    ("term-04", "Buy the Air Pure 300 today.", "flagged", ["terminology"], ["E-TERM-001"], "prohibited name"),
    ("term-05", "Consumers love the AirPure 300.", "flagged", ["terminology"], ["E-TERM-002"], "prohibited term 'consumers'"),
    ("term-06", "Our consumers asked for a quieter unit.", "flagged", ["terminology"], ["E-TERM-002"], "prohibited term 'consumer'"),
    ("term-07", "This air cleaner runs all night.", "flagged", ["terminology"], ["E-TERM-003"], "deprecated term"),
    ("term-08", "The air cleaner is our best seller.", "flagged", ["terminology"], ["E-TERM-003"], "deprecated term"),
    ("term-09", "The AirPure 300 is certified for smoke removal.", "flagged", ["terminology"], ["E-TERM-004"],
     "certification mentioned without the required wording"),
    ("term-10", "Our CADR score is independently verified.", "flagged", ["terminology"], ["E-TERM-004"],
     "certification mentioned without the required wording"),
    ("term-11", "The Northstar AirPure 300 and the AirPure 300 both ship this week.",
     "flagged", ["terminology"], ["E-TERM-001"], "one prohibited variant in an otherwise correct line"),
    ("term-12", "Consumers can order the Northstar AirPure 300 from today.",
     "flagged", ["terminology"], ["E-TERM-002"], "prohibited term with the correct product name"),

    # ---- disclaimers
    ("disc-01", "Save 20% on the AirPure 300 this week.", "flagged", ["messaging"], ["E-DISC-001"],
     "discount without the disclaimer"),
    ("disc-02", "15% off the AirPure 300 for the launch.", "flagged", ["messaging"], ["E-DISC-001"],
     "discount without the disclaimer"),
    ("disc-03", "Offer of the month: $100 off the AirPure 300.", "flagged", ["messaging"], ["E-DISC-001"],
     "discount without the disclaimer"),
    ("disc-04", "The AirPure 300 is available for $499.", "clean", [], [], "no discount, so no disclaimer required"),
    ("disc-05", "Our launch offer is 15% off. Offer valid while stocks last.",
     "clean", [], [], "discount with disclaimer"),
    ("disc-06", "Northstar AirPure 300: quieter nights, cleaner air.", "clean", [], [],
     "no discount language at all"),

    # ---- factual accuracy
    ("fact-01", "Every customer receives a three-year warranty.", "flagged", ["factual_accuracy"], [], "wrong warranty"),
    ("fact-02", "The AirPure 300 comes with a five-year warranty.", "flagged", ["factual_accuracy"], [], "wrong warranty"),
    ("fact-03", "The AirPure 300 is available for $399.", "flagged", ["factual_accuracy"], [], "wrong price"),
    ("fact-04", "Buy it today for $549.", "flagged", ["factual_accuracy"], [], "wrong price"),
    ("fact-05", "The AirPure 300 carries a one-year limited warranty.", "clean", [], [], "correct warranty"),
    ("fact-06", "Recommended retail price: $499.", "clean", [], [], "correct price"),
    ("fact-07", "It filters rooms up to 60 m².", "clean", [], [], "correct specification"),
    ("fact-08", "The unit covers 90 m² of floor space.", "flagged", ["factual_accuracy"], [], "wrong specification"),
    ("fact-09", "Northstar Living was founded in 2011.", "flagged", ["factual_accuracy"], [], "source expired 2024-12-31"),
    ("fact-10", "Founded in 2011, Northstar Living has grown ever since.", "flagged", ["factual_accuracy"], [],
     "expired founding fact"),
    ("fact-11", "The warranty lasts two years.", "flagged", ["factual_accuracy"], [], "wrong warranty duration"),
    ("fact-12", "Warranty: 24 months, parts and labour.", "flagged", ["factual_accuracy"], [], "wrong warranty duration"),

    # ---- promotional claims
    ("claim-01", "It is guaranteed to be the best on the market.", "flagged", ["promotional_claim"], [], "superlative plus guarantee"),
    ("claim-02", "Unbeatable performance at any price.", "flagged", ["promotional_claim"], [], "absolute superlative"),
    ("claim-03", "Delivers 100% better results than the competition.", "flagged", ["promotional_claim"], [], "quantified comparison"),
    ("claim-04", "100% effective against dust.", "flagged", ["promotional_claim"], [], "quantified benefit"),
    ("claim-05", "Clinically proven to reduce allergens.", "flagged", ["promotional_claim"], [], "scientific evidence claim"),
    ("claim-06", "Removes 99.9% of airborne viruses.", "flagged", ["promotional_claim"], [], "claim rejected in the register"),
    ("claim-07", "Prevents asthma attacks in children.", "flagged", ["promotional_claim"], [], "health outcome claim"),
    ("claim-08", "Cures seasonal allergies.", "flagged", ["promotional_claim"], [], "health outcome claim"),
    ("claim-09", "Carbon neutral manufacturing since 2020.", "flagged", ["promotional_claim"], [], "environmental claim"),
    ("claim-10", "FDA approved air purification.", "flagged", ["promotional_claim"], [], "certification claim"),
    ("claim-11", "Risk-free purchase with our money-back promise.", "flagged", ["promotional_claim"], [], "financial risk claim"),
    ("claim-12", "Outperforms every purifier we tested.", "flagged", ["promotional_claim"], [], "comparative claim"),
    ("claim-13", "The best value purifier for family homes.", "flagged", ["promotional_claim"], [], "superlative"),
    ("claim-14", "Number one in customer satisfaction.", "flagged", ["promotional_claim"], [], "ranking claim"),
    ("claim-15", "It filters rooms up to 60 m², which is the largest coverage in our range.",
     "flagged", ["promotional_claim"], [], "comparative framing around an approved number"),

    # ---- confidentiality
    ("conf-01", "INTERNAL ONLY - do not distribute. The AirPure 300 launches in March.", "flagged",
     ["confidentiality"], ["E-CONF-001"], "internal-only marker"),
    ("conf-02", "Internal only: draft pricing for the AirPure 300.", "flagged", ["confidentiality"], ["E-CONF-001"],
     "internal-only marker"),
    ("conf-03", "Do not distribute outside the partner programme.", "flagged", ["confidentiality"], ["E-CONF-001"],
     "distribution restriction"),
    ("conf-04", "Confidential: supplier terms for the AirPure 300.", "flagged", ["confidentiality"], ["E-CONF-001"],
     "confidentiality marker"),
    ("conf-05", "Unreleased AirPure 300 colourway shown below.", "flagged", ["confidentiality"], ["E-CONF-001"],
     "unreleased product reference"),
    ("conf-06", "Embargoed until 1 March 2027.", "flagged", ["confidentiality"], ["E-CONF-001"], "embargo marker"),
    ("conf-07", "The AirPure 300 is available now.", "clean", [], [], "no confidential marker"),
    ("conf-08", "Our customer team can help you choose.", "clean", [], [], "plain customer service line"),

    # ---- formatting
    ("fmt-01", "BEST VALUE EVER!!!", "flagged", ["formatting"], ["E-FMT-001"], "capitals and exclamation stack"),
    ("fmt-02", "AMAZING DEAL ON NOW", "flagged", ["formatting"], ["E-FMT-001"], "capitals run"),
    ("fmt-03", "Get it now!!! Limited time!!!", "flagged", ["formatting"], [], "exclamation stacking"),
    ("fmt-04", "Order today! Stocks are limited.", "clean", [], [], "single exclamation mark"),
    ("fmt-05", "Cleaner air, quieter nights.", "clean", [], [], "plain sentence"),
    ("fmt-06", "AIRPURE 300 NOW AVAILABLE", "flagged", ["formatting"], ["E-FMT-001"], "capitals run"),

    # ---- accessibility
    ("a11y-01", '<p>See our range</p><img src="range.png">', "flagged", ["accessibility"], ["E-A11Y-001"],
     "image without alt text"),
    ("a11y-02", '<div><img src="hero.png"></div>', "flagged", ["accessibility"], ["E-A11Y-001"],
     "image without alt text"),
    ("a11y-03", '<p>Read the specification <a href="/spec">click here</a></p>', "flagged", ["accessibility"],
     ["E-A11Y-001"], "vague link text"),
    ("a11y-04", '<p><a href="/warranty">here</a> for warranty details</p>', "flagged", ["accessibility"],
     ["E-A11Y-001"], "vague link text"),
    ("a11y-05", '<h1>AirPure 300</h1><h3>Specifications</h3>', "flagged", ["accessibility"], ["E-A11Y-001"],
     "heading level skipped"),
    ("a11y-06", '<h2>Cleaner air</h2><h5>How it works</h5>', "flagged", ["accessibility"], ["E-A11Y-001"],
     "heading level skipped"),
    ("a11y-07", '<p><img src="range.png" alt="The AirPure 300 in a living room"></p>', "clean", [], [],
     "image has meaningful alt text"),
    ("a11y-08", '<h2>AirPure 300</h2><p>Filters up to 60 m².</p>', "clean", [], [], "heading structure is sound"),

    # ---- messaging / tone
    ("msg-01", "Our revolutionary new purifier is here.", "flagged", ["messaging"], ["E-MSG-001"], "banned positioning word"),
    ("msg-02", "A game-changing approach to clean air.", "flagged", ["messaging"], ["E-MSG-001"], "banned positioning word"),
    ("msg-03", "The world-first purifier for family homes.", "flagged", ["messaging"], ["E-MSG-001"], "banned positioning word"),
    ("msg-04", "A purifier designed for everyday rooms.", "clean", [], [], "plain positioning"),
    ("tone-01", "WOW!!! You will absolutely LOVE what we have been working on!!!", "flagged", ["tone"], [],
     "hype-led tone"),
    ("tone-02", "AMAZING!!! Totally revolutionary!!!", "flagged", ["tone", "messaging"], [], "hype plus banned wording"),
    ("tone-03", "Quiet, dependable air purification for the room you actually live in.", "clean", [], [],
     "on-tone sentence"),
    ("tone-04", "We built it because a quieter night should not cost a fortune.", "clean", [], [], "warm and precise"),
]


class BgEvalSeedInput(BaseModel):
    action: str = "seed"


class BgEvalSeedResult(BaseModel):
    organization_id: str
    cases: int
    rules: int
    facts: int
    by_category: dict
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def bg_eval_seed(ctx: FunctionContext, data: BgEvalSeedInput) -> BgEvalSeedResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    orgs = [o for o in pod.records.list("bg_organizations", limit=100).to_dict()["items"]
            if o.get("slug") == EVAL_SLUG]
    if orgs:
        org = orgs[0]
    else:
        org = pod.table("bg_organizations").create({
            "name": "BrandGuard evaluation (labelled set)", "slug": EVAL_SLUG, "is_active": False,
            "description": "Holds the labelled evaluation cases and the rules they are labelled against. "
                           "Hidden from the app's workspace switcher.",
            "settings": {"brand_colors": ["#0c2340", "#e8b33c"]}})
    org_id = org["id"]

    members = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    if not members:
        pod.table("bg_memberships").create({
            "organization_id": org_id, "member_user_id": ctx.user_id, "user_email": email,
            "display_name": "Evaluation", "role": "admin", "status": "active"})
    if not pod.records.list("bg_policies", limit=1, filter=[
            {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]:
        pod.table("bg_policies").create({
            "organization_id": org_id, "updated_by": email, "analysis_version": "bg-eval-deterministic",
            "ai_analysis_enabled": False, "require_separation_of_duties": False,
            "block_approval_on_mandatory": True, "critical_requires_compliance_reviewer": False,
            "retention_days": 365})

    existing_rules = pod.records.list("bg_guidelines", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    have = {r.get("code") for r in existing_rules}
    rules_created = 0
    for rule in RULES:
        if rule["code"] in have:
            continue
        row = pod.table("bg_guidelines").create({
            **rule, "organization_id": org_id, "version": 1, "status": "active",
            "created_by": ctx.user_id, "created_by_email": email,
            "approved_by": email, "approved_at": _now(),
            "source_section": "BrandGuard evaluation set"})
        pod.table("bg_guideline_versions").create({
            "guideline_id": row["id"], "version": 1, "snapshot": row,
            "change_summary": "Created with the evaluation set", "changed_by_email": email})
        rules_created += 1

    existing_facts = pod.records.list("bg_facts", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    fact_statements = {f.get("statement") for f in existing_facts}
    facts_created = 0
    for fact in FACTS:
        if fact["statement"] in fact_statements:
            continue
        pod.table("bg_facts").create({
            **fact, "organization_id": org_id, "owner": email, "last_verified_at": _now(),
            "scope": "As stated in the approved record.", "version": 1})
        facts_created += 1

    existing_cases = pod.records.list("bg_eval_cases", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]
    have_keys = {c.get("case_key") for c in existing_cases}
    created = 0
    by_category: dict = {}
    for key, content, outcome, categories, rules, note in CASES:
        if key in have_keys:
            continue
        pod.table("bg_eval_cases").create({
            "organization_id": org_id, "case_key": key, "title": f"Eval {key}: {note}"[:300],
            "content": content, "content_type": "social_post", "campaign": "evaluation",
            "expected_outcome": outcome, "expected_categories": categories, "expected_rules": rules,
            "expected_evidence_status": [], "ai_relevant": True,
            "label_source": "hand-labelled against the rules created by this function",
            "notes": note, "is_active": True})
        created += 1
    for _, _, _, categories, _, _ in CASES:
        for category in categories or ["(clean control)"]:
            by_category[category] = by_category.get(category, 0) + 1

    total = len(pod.records.list("bg_eval_cases", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"])
    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": email,
        "action": "eval.seeded", "subject_type": "bg_eval_cases", "subject_id": None,
        "summary": f"Evaluation set: {total} labelled case(s), {len(RULES)} rule(s), {len(FACTS)} fact(s)",
        "metadata": {"created": created, "rules_created": rules_created, "engine": ENGINE_VERSION}})
    return BgEvalSeedResult(
        organization_id=org_id, cases=total, rules=len(RULES), facts=len(FACTS), by_category=by_category,
        message=(f"{total} labelled case(s) across {len(RULES)} rules and {len(FACTS)} approved facts "
                 f"({created} new). Labelled by hand, not generated from engine output."))

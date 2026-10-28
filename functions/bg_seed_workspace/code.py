#input_type_name: BgSeedWorkspaceInput
#output_type_name: BgSeedWorkspaceResult
#function_name: bg_seed_workspace

"""Provision a workspace with brand knowledge: guidelines, approved facts with evidence,
approved assets (real PNG files with perceptual hashes), policy and starting reviews.

Idempotent: an existing workspace is left alone unless reset is true, in which case its
BrandGuard records are removed and rebuilt deterministically."""
import io
import struct
import tempfile
import zlib
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

# deletion order matters: children before parents (foreign keys)
WIPE_ORDER = ["bg_segments", "bg_findings", "bg_corrections", "bg_analysis_runs", "bg_approvals",
              "bg_comments", "bg_review_versions", "bg_reviews", "bg_asset_versions", "bg_assets",
              "bg_fact_evidence", "bg_facts", "bg_guideline_versions", "bg_guidelines", "bg_documents",
              "bg_insights", "bg_notifications", "bg_audit_logs", "bg_policies", "bg_ai_providers",
              "bg_memberships"]
ALL_TABLES = ["bg_organizations"] + WIPE_ORDER

DEFAULT_POLICY = {
    "require_separation_of_duties": True, "block_approval_on_mandatory": True,
    "critical_requires_compliance_reviewer": True, "high_risk_requires_second_reviewer": False,
    "ai_analysis_enabled": True, "external_ai_allowed": True,
    "confidential_content_to_external_ai": False, "auto_publish_enabled": False,
    "require_review_after_approved_edit": True, "retention_days": 365,
    "analysis_version": "bg-analysis-1.0",
}


class BgSeedWorkspaceInput(BaseModel):
    slug: str
    reset: bool = False
    with_reviews: bool = True
    actor_email: str | None = None


class BgSeedWorkspaceResult(BaseModel):
    organization_id: str
    slug: str
    created: bool
    guidelines: int = 0
    facts: int = 0
    assets: int = 0
    reviews: list[str] = []
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- image generation
def _logo_pixels(w, h, bg, accent, mark="square"):
    def px(x, y):
        cx, cy = w * 0.30, h * 0.5
        if mark == "square" and abs(x - cx) < w * 0.09 and abs(y - cy) < h * 0.28:
            return accent
        if mark == "circle" and (x - w * 0.62) ** 2 / (w * 0.13) ** 2 + (y - cy) ** 2 / (h * 0.30) ** 2 < 1:
            return accent
        if mark == "bar" and x > w * 0.80 and abs(y - cy) < h * 0.06:
            return accent
        return bg
    return px


def _png_bytes(w, h, px):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            r, g, b = px(x, y)
            raw += bytes((r, g, b))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _ahash(w, h, px, size=8):
    """8x8 average hash - the same convention the app uses for uploaded images."""
    vals = []
    for gy in range(size):
        for gx in range(size):
            x0, x1 = gx * w // size, max(gx * w // size + 1, (gx + 1) * w // size)
            y0, y1 = gy * h // size, max(gy * h // size + 1, (gy + 1) * h // size)
            tot = n = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    r, g, b = px(x, y)
                    tot += (r * 299 + g * 587 + b * 114) // 1000
                    n += 1
            vals.append(tot / max(1, n))
    avg = sum(vals) / len(vals)
    return f"{int(''.join('1' if v >= avg else '0' for v in vals), 2):016x}"


def _dominant(w, h, px, top=4):
    counts = {}
    step = max(1, w // 40)
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px(x, y)
            key = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
            counts[key] = counts.get(key, 0) + 1
    best = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
    return ["#%02x%02x%02x" % k for k, _ in best]


def _upload_asset_file(pod, name, w, h, px):
    data = _png_bytes(w, h, px)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(data)
        tmp = fh.name
    target = f"/brandguard/assets/{name}.png"
    try:
        pod.files.get(target)
        exists = True
    except Exception:  # noqa: BLE001
        exists = False
    if not exists:
        pod.files.upload(tmp, path=target, description="Approved brand asset (BrandGuard AI)")
    return target


# ---------------------------------------------------------------- content
NORTHSTAR_GUIDELINES = [
    dict(code="TERM-001", title="Approved product name", category="terminology", rule_type="prohibited_term",
         severity="high", enforcement="mandatory",
         rule_text="Always use the approved product name 'Northstar AirPure 300'. Never use 'PureAir Pro', "
                   "'AirPure 300' or 'Air Pure 300'.",
         match_terms={"terms": ["PureAir Pro", "Pure Air Pro", "AirPure300"], "replace_with": ["Northstar AirPure 300"]},
         noncompliant_examples=["The PureAir Pro is available now."],
         compliant_examples=["The Northstar AirPure 300 is available now."],
         source_section="Brand Book 2026, section 3.2 Product naming"),
    dict(code="TERM-002", title="Customer, not consumer", category="terminology", rule_type="prohibited_term",
         severity="medium", enforcement="mandatory",
         rule_text="In customer-facing copy, write 'customer'. Do not use 'consumer'.",
         match_terms={"terms": ["consumer", "consumers"], "replace_with": ["customer", "customers"]},
         source_section="Brand Book 2026, section 3.4 Language"),
    dict(code="TONE-001", title="Brand tone of voice", category="tone", rule_type="tone_rule",
         severity="medium", enforcement="mandatory",
         rule_text="Warm, precise and human. Confident without hype. Explain the benefit in plain English and "
                   "avoid stacking exclamation marks.",
         compliant_examples=["Cleaner air, quieter nights. The Northstar AirPure 300 filters a 60 m² room in 20 minutes."],
         noncompliant_examples=["REVOLUTIONARY!!! The most amazing purifier EVER!!!"],
         source_section="Brand Book 2026, section 2.1 Tone of voice"),
    dict(code="MSG-001", title="Positioning statement", category="messaging", rule_type="messaging_rule",
         severity="high", enforcement="mandatory",
         rule_text="Northstar Living makes everyday home essentials that last. Do not describe products as "
                   "'revolutionary', 'game-changing', 'life-changing' or 'world-first'.",
         match_terms={"terms": ["revolutionary", "game-changing", "game changing", "life-changing", "world-first"]},
         source_section="Brand Book 2026, section 1.3 Positioning"),
    dict(code="CLAIM-001", title="Evidence required for superlatives", category="promotional_claim",
         rule_type="claim_policy", severity="high", enforcement="mandatory",
         rule_text="Absolute superlatives ('best on the market', 'unbeatable', 'number one') and quantified "
                   "benefit claims require an approved evidence record before publication.",
         source_section="Marketing Compliance Policy, section 2"),
    dict(code="CLAIM-002", title="No health or medical outcome claims", category="promotional_claim",
         rule_type="claim_policy", severity="critical", enforcement="mandatory",
         rule_text="Do not claim that a product prevents, treats or cures illness, or that it removes a "
                   "percentage of pathogens, unless an approved clinical evidence record exists. No such record "
                   "exists for the AirPure range.",
         source_section="Marketing Compliance Policy, section 4.1"),
    dict(code="FACT-001", title="Warranty and price must match the approved record", category="factual_accuracy",
         rule_type="numeric_fact", severity="high", enforcement="mandatory",
         rule_text="Warranty duration and recommended retail price must match the current approved fact exactly.",
         source_section="Product specification v4, section 4.2"),
    dict(code="DISC-001", title="Discount disclaimer required", category="legal_compliance",
         rule_type="required_disclaimer", severity="high", enforcement="mandatory",
         rule_text="Any promotional discount must carry the approved disclaimer: 'Offer valid while stocks last. "
                   "Discount applies to the recommended retail price.'",
         match_terms={"terms": ["Offer valid while stocks last. Discount applies to the recommended retail price."],
                      "applies_when": ["discount", "% off", "save", "offer", "deal", "%", "reduced"]},
         source_section="Marketing Compliance Policy, section 5"),
    dict(code="LEGAL-001", title="Comparative claims need a named basis", category="legal_compliance",
         rule_type="claim_policy", severity="high", enforcement="mandatory",
         rule_text="A comparative claim must name the basis of comparison and be substantiated by an approved "
                   "evidence record. Never compare against 'the competition' in general.",
         source_section="Marketing Compliance Policy, section 6"),
    dict(code="CONF-001", title="No internal-only material in published content", category="confidentiality",
         rule_type="confidentiality_rule", severity="critical", enforcement="mandatory",
         rule_text="Content published outside Northstar Living must not contain internal-only markers, embargo "
                   "notes, unreleased product details, internal cost or margin data.",
         match_terms={"terms": ["internal only", "do not distribute", "confidential", "embargoed", "unreleased",
                                "cost price", "net margin"]},
         source_section="Information Classification Policy, section 3"),
    dict(code="CONF-002", title="Draft plans stay internal", category="confidentiality",
         rule_type="confidentiality_rule", severity="medium", enforcement="recommended",
         rule_text="Working documents, draft plans and 'work in progress' labels should be removed before "
                   "customer-facing publication. Use judgement: the word 'internal' alone is not always a breach.",
         match_terms={"terms": ["internal", "draft plan", "work in progress"]},
         source_section="Information Classification Policy, section 4"),
    dict(code="VIS-001", title="Use the approved logo, unmodified", category="visual_identity",
         rule_type="visual_rule", severity="high", enforcement="mandatory",
         rule_text="Use an approved logo file at its original proportions. Do not recolour, stretch or crop the "
                   "logo, and never use a superseded version.",
         source_section="Brand Book 2026, section 5.1 Logo"),
    dict(code="A11Y-001", title="Accessible marketing content", category="accessibility",
         rule_type="accessibility_rule", severity="medium", enforcement="mandatory",
         rule_text="Every image needs meaningful alternative text. Link text must describe the destination. "
                   "Do not skip heading levels.",
         source_section="Digital Accessibility Standard, section 2"),
    dict(code="FMT-001", title="Formatting restraint", category="formatting", rule_type="formatting_rule",
         severity="low", enforcement="advisory",
         rule_text="Avoid long runs of capitals and keep exclamation marks to at most one per piece.",
         source_section="Brand Book 2026, section 2.4 Typography in copy"),
    dict(code="TERM-004", title="Plain English vocabulary", category="terminology", rule_type="prohibited_term",
         severity="low", enforcement="recommended", status="draft",
         rule_text="Prefer plain words: use 'use' not 'utilise', 'help' not 'leverage', 'service' not 'solution'.",
         match_terms={"terms": ["utilise", "leverage", "solution"]},
         source_section="Brand Book 2026, section 3.6 Plain English"),
]

NORTHSTAR_FACTS = [
    dict(statement="Northstar AirPure 300 carries a one-year limited warranty covering manufacturing defects.",
         category="warranty", value_text="one-year limited warranty", numeric_value=12, unit="months",
         match_keywords=["warranty", "guarantee", "covered"], verification_status="verified",
         approval_status="approved", effective_date="2026-01-01", expiration_date="2027-01-01",
         source_location="Product specification v4, section 4.2"),
    dict(statement="Northstar AirPure 300 has a recommended retail price of $499.",
         category="price", value_text="$499", numeric_value=499, unit="currency",
         match_keywords=["$", "price", "retail", "costs", "available for"], verification_status="verified",
         approval_status="approved", effective_date="2026-01-01",
         source_location="Price list 2026, page 1"),
    dict(statement="The current launch offer is 15% off the recommended retail price.",
         category="discount", value_text="15%", numeric_value=15, unit="percent",
         match_keywords=["discount", "% off", "offer"], verification_status="verified",
         approval_status="approved", effective_date="2026-09-01", expiration_date="2027-03-31",
         source_location="Campaign brief, AirPure 300 launch"),
    dict(statement="Northstar AirPure 300 filters rooms up to 60 m².",
         category="product_specification", value_text="60 m²", numeric_value=60, unit="m2",
         match_keywords=["room", "coverage", "filters", "m²"], verification_status="verified",
         approval_status="approved", effective_date="2026-01-01",
         source_location="Product specification v4, section 2.1"),
    dict(statement="Northstar AirPure 300 holds AHAM VERIFIDE certification with a smoke CADR of 250.",
         category="certification", value_text="AHAM VERIFIDE, smoke CADR 250", numeric_value=None,
         match_keywords=["AHAM", "certified", "certification", "CADR"], verification_status="verified",
         approval_status="approved", effective_date="2026-02-01",
         source_location="Certificate AHAM-2026-1188"),
    dict(statement="Claim 'removes 99.9% of airborne viruses' is NOT substantiated. No approved laboratory "
                   "evidence exists for this claim.",
         category="promotional_claim", value_text=None, numeric_value=None,
         match_keywords=["99.9%", "viruses", "pathogens"], verification_status="no_evidence",
         approval_status="rejected", owner="compliance@northstarliving.example",
         source_location="Claims register, entry C-2026-014 (rejected)"),
    dict(statement="Northstar Living was founded in 2011.",
         category="company_description", value_text="2011", numeric_value=2011, unit="year",
         match_keywords=["founded", "since"], verification_status="verified", approval_status="approved",
         effective_date="2015-01-01", expiration_date="2024-12-31",
         source_location="Company fact sheet (superseded 2025)"),
    dict(statement="Northstar Living designs and sells home essentials from Bristol in the United Kingdom.",
         category="company_description", value_text=None, numeric_value=None,
         match_keywords=["Northstar Living", "Bristol", "based in"], verification_status="verified",
         approval_status="approved", effective_date="2026-01-01",
         source_location="Company fact sheet 2026"),
    dict(statement="Northstar AirPure 300 launches in Europe in March 2027.",
         category="availability", value_text="March 2027", numeric_value=None,
         match_keywords=["Europe", "launch", "available"], verification_status="pending",
         approval_status="pending", owner="product@northstarliving.example",
         source_location="Roadmap draft (not approved)"),
]

NORTHSTAR_REVIEWS = [
    dict(title="AirPure 300 launch — Instagram post", content_type="social_post", campaign="AirPure 300 launch",
         audience="Home owners", channel="Instagram",
         notes="Launch teaser, 24-hour turnaround.",
         text="Meet our revolutionary new product! It is guaranteed to be the best on the market and delivers 100% "
              "better results than the competition. The PureAir Pro is available for $399 and every customer receives "
              "a three-year warranty. BEST VALUE EVER!!!"),
    dict(title="AirPure 300 product page copy", content_type="product_description", campaign="AirPure 300 launch",
         audience="Prospective customers", channel="Website",
         notes="Approved spec sheet used as the source.",
         creator="peer",
         text="The Northstar AirPure 300 is built for everyday rooms. It filters spaces up to 60 m² and carries a "
              "one-year limited warranty covering manufacturing defects.\n\nRecommended retail price: $499. "
              "Northstar Living designs and sells home essentials from Bristol in the United Kingdom."),
    dict(title="Winter offer email to existing customers", content_type="marketing_email", campaign="Winter offer",
         audience="Existing customers", channel="Email",
         notes="Sent to the newsletter list of 42,000 customers.", creator="peer",
         text="A warmer, cleaner home for less. Save 15% on the AirPure Pro this winter — that's our biggest "
              "discount of the year for consumers who care about air quality.\n\nReply to this email if you would "
              "like help choosing."),
    dict(title="AirPure 300 promotional banner (HTML)", content_type="promotional_banner", campaign="AirPure 300 launch",
         audience="Home owners", channel="Display ads",
         notes="Banner set for the launch, four sizes.",
         text="<div class=\"banner\"><h2>Breathe easier</h2><img src=\"/brandguard/assets/airpure-banner.png\">"
              "<p>Our purifier is 100% better than the competition at removing dust.</p>"
              "<h4>Order today</h4><a href=\"https://example.com/airpure\">click here</a></div>"),
    dict(title="AirPure 300 brochure — internal draft", content_type="product_brochure",
         campaign="AirPure 300 launch", audience="Retail partners", channel="Print",
         notes="Draft brochure circulated for partner review.",
         text="Northstar Living was founded in 2011 and has grown steadily since.\n\n"
              "INTERNAL ONLY — do not distribute outside the partner programme.\n\n"
              "The AirPure 300 filters rooms up to 60 m² and carries a one-year limited warranty."),
    dict(title="AirPure 300 health benefit post", content_type="social_post", campaign="AirPure 300 launch",
         audience="Parents", channel="Facebook",
         notes="Draft written by an agency.", creator="peer",
         text="Finally a purifier that removes 99.9% of airborne viruses from your home, so your family never gets "
              "sick again. Clinically proven protection you can trust."),
    dict(title="Spring brand voice test", content_type="blog_post", campaign="Always-on",
         audience="General audience", channel="Blog",
         notes="Editorial voice experiment.",
         text="WOW! You will absolutely LOVE what we have been working on!!! It is totally AMAZING and completely "
              "revolutionary. Check it out and you will never look back!!!"),
    dict(title="AirPure 300 partner kit — logo lock-up", content_type="promotional_banner",
         campaign="AirPure 300 launch", audience="Retail partners", channel="Partner portal",
         notes="Uses the partner lock-up supplied last year.", creator="peer",
         text="Download the Northstar Living lock-up for your store. This version was replaced in 2025 — check the "
              "asset library before you publish.",
         image="logo-2021"),
]

MERIDIAN_GUIDELINES = [
    dict(code="MF-TERM-001", title="Meridian Foods brand name", category="terminology", rule_type="prohibited_term",
         severity="high", enforcement="mandatory",
         rule_text="Write 'Meridian Foods'. Never use 'MeridianFoods' or 'Meridian Food Co'.",
         match_terms={"terms": ["MeridianFoods", "Meridian Food Co"], "replace_with": ["Meridian Foods"]},
         source_section="Meridian identity guide, section 1"),
    dict(code="MF-TONE-001", title="Meridian tone", category="tone", rule_type="tone_rule", severity="medium",
         enforcement="recommended",
         rule_text="Plain, generous and unfussy. Talk about the food and the people who make it.",
         source_section="Meridian identity guide, section 2"),
    dict(code="MF-CLAIM-001", title="Provenance claims need evidence", category="promotional_claim",
         rule_type="claim_policy", severity="high", enforcement="mandatory",
         rule_text="Provenance and sustainability claims require an approved certification record.",
         source_section="Meridian claims policy, section 3"),
    dict(code="MF-CONF-001", title="Supplier terms are confidential", category="confidentiality",
         rule_type="confidentiality_rule", severity="critical", enforcement="mandatory",
         rule_text="Supplier pricing and contract terms must never appear in customer-facing material.",
         match_terms={"terms": ["supplier price", "contract terms", "confidential", "do not distribute"]},
         source_section="Meridian confidentiality policy, section 2"),
]

MERIDIAN_FACTS = [
    dict(statement="Meridian Foods' Heritage Sourdough is baked in small batches in Kent.",
         category="product_specification", value_text=None, numeric_value=None,
         match_keywords=["sourdough", "baked", "Kent"], verification_status="verified",
         approval_status="approved", effective_date="2026-01-01",
         source_location="Product sheet, Heritage Sourdough"),
    dict(statement="Meridian Foods is certified by the Soil Association.",
         category="certification", value_text="Soil Association certified", numeric_value=None,
         match_keywords=["Soil Association", "certified", "organic"], verification_status="verified",
         approval_status="approved", effective_date="2026-04-01",
         source_location="Certificate SA-2026-4471"),
]

MERIDIAN_REVIEWS = [
    dict(title="Heritage Sourdough in-store poster", content_type="advertisement", campaign="Heritage range",
         audience="Shoppers", channel="Retail", notes="Poster for the bakery aisle.",
         text="MeridianFoods Heritage Sourdough is baked in small batches in Kent and is Soil Association certified. "
              "Ask at the counter for today's bake."),
    dict(title="Heritage Sourdough supplier note", content_type="marketing_document", campaign="Heritage range",
         audience="Retail buyers", channel="Email", notes="Draft shared with buyers.",
         text="Meridian Foods Heritage Sourdough is baked in Kent.\n\nConfidential: supplier price and contract terms "
              "are set out below for internal review only."),
]


def _wipe(pod, org_id):
    """Delete one workspace's BrandGuard records, children before parents."""
    def all_rows(table):
        try:
            return pod.records.list(table, limit=1000).to_dict()["items"]
        except Exception:  # noqa: BLE001
            return []

    def org_rows(table):
        return [r for r in all_rows(table) if r.get("organization_id") == org_id]

    def drop(table, ids):
        ids = sorted({i for i in ids if i})
        if ids:
            try:
                pod.records.bulk_delete(table, ids)
            except Exception:  # noqa: BLE001
                pass

    review_ids = {r["id"] for r in org_rows("bg_reviews")}
    asset_ids = {r["id"] for r in org_rows("bg_assets")}
    fact_ids = {r["id"] for r in org_rows("bg_facts")}
    guideline_ids = {r["id"] for r in org_rows("bg_guidelines")}
    finding_ids = {r["id"] for r in all_rows("bg_findings")
                   if r.get("organization_id") == org_id or r.get("review_id") in review_ids}

    drop("bg_corrections", [r["id"] for r in all_rows("bg_corrections")
                            if r.get("finding_id") in finding_ids or r.get("review_id") in review_ids])
    drop("bg_segments", [r["id"] for r in all_rows("bg_segments") if r.get("review_id") in review_ids])
    drop("bg_analysis_runs", [r["id"] for r in all_rows("bg_analysis_runs") if r.get("review_id") in review_ids])
    drop("bg_approvals", [r["id"] for r in all_rows("bg_approvals") if r.get("review_id") in review_ids])
    drop("bg_comments", [r["id"] for r in all_rows("bg_comments") if r.get("review_id") in review_ids])
    drop("bg_review_versions", [r["id"] for r in all_rows("bg_review_versions") if r.get("review_id") in review_ids])
    drop("bg_guideline_versions", [r["id"] for r in all_rows("bg_guideline_versions")
                                   if r.get("guideline_id") in guideline_ids])
    drop("bg_asset_versions", [r["id"] for r in all_rows("bg_asset_versions") if r.get("asset_id") in asset_ids])
    drop("bg_fact_evidence", [r["id"] for r in all_rows("bg_fact_evidence") if r.get("fact_id") in fact_ids])
    drop("bg_findings", finding_ids)
    drop("bg_reviews", review_ids)
    drop("bg_assets", asset_ids)
    drop("bg_facts", fact_ids)
    drop("bg_guidelines", guideline_ids)
    for table in ("bg_documents", "bg_insights", "bg_notifications", "bg_audit_logs", "bg_policies",
                  "bg_ai_providers", "bg_memberships"):
        drop(table, [r["id"] for r in org_rows(table)])


def bg_seed_workspace(ctx: FunctionContext, data: BgSeedWorkspaceInput) -> BgSeedWorkspaceResult:
    pod = ctx.pod
    email = data.actor_email or ctx.user_email or ""
    existing = [o for o in pod.records.list("bg_organizations", limit=100).to_dict()["items"]
                if o.get("slug") == data.slug]
    if existing:
        mine = pod.records.list("bg_memberships", limit=5, filter=[
            {"field": "organization_id", "op": "eq", "value": existing[0]["id"]},
            {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
        if not mine or mine[0].get("role") != "admin":
            raise PermissionError("Only a workspace administrator can provision or reset this workspace.")
    if existing and not data.reset:
        return BgSeedWorkspaceResult(organization_id=existing[0]["id"], slug=data.slug, created=False,
                                     message=f"Workspace '{data.slug}' already exists; nothing was changed.")
    if existing and data.reset:
        org = existing[0]
        _wipe(pod, org["id"])
        created = False
    else:
        created = True
        org = None

    if data.slug == "northstar-living":
        name, description = "Northstar Living", "Home essentials brand — brand governance workspace."
        colors = ["#0c2340", "#e8b33c", "#f5f1ea", "#1f7a6c"]
        tone = ("Warm, precise and human. Confident without hype. British English. Explain the benefit in plain "
                "words; never shout.")
        guidelines, facts, reviews = NORTHSTAR_GUIDELINES, NORTHSTAR_FACTS, NORTHSTAR_REVIEWS
    elif data.slug == "meridian-foods":
        name, description = "Meridian Foods", "Speciality bakery — brand governance workspace."
        colors = ["#3b2f2a", "#c8a15a", "#f2ece2"]
        tone = "Plain, generous and unfussy."
        guidelines, facts, reviews = MERIDIAN_GUIDELINES, MERIDIAN_FACTS, MERIDIAN_REVIEWS
    else:
        raise ValueError("Unknown workspace slug. Supported: northstar-living, meridian-foods.")

    if not org:
        org = pod.table("bg_organizations").create({
            "name": name, "slug": data.slug, "description": description, "tone_summary": tone,
            "is_active": True,
            "settings": {"brand_colors": colors, "tone": tone.split(".")[0],
                         "industry": "Consumer goods", "region": "United Kingdom"}})
    org_id = org["id"]

    members = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    if not members:
        pod.table("bg_memberships").create({
            "organization_id": org_id, "member_user_id": ctx.user_id, "user_email": email,
            "display_name": email.split("@")[0].replace(".", " ").title(), "role": "admin",
            "status": "active", "notes": "workspace owner"})
    for peer, role in (("abhijeet.bansode24@vit.edu", "reviewer" if data.slug == "northstar-living"
                        else "compliance_reviewer"),):
        have = pod.records.list("bg_memberships", limit=5, filter=[
            {"field": "organization_id", "op": "eq", "value": org_id},
            {"field": "user_email", "op": "eq", "value": peer}]).to_dict()["items"]
        if not have:
            pod.table("bg_memberships").create({
                "organization_id": org_id, "user_email": peer, "display_name": "Abhijeet Bansode",
                "role": role, "status": "active", "notes": "pod member"})

    if not pod.records.list("bg_policies", limit=1, filter=[
            {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]:
        pod.table("bg_policies").create({**DEFAULT_POLICY, "organization_id": org_id,
                                         "ai_model": "platform runtime (pod agent brandguard-analyst)",
                                         "updated_by": email})
    if not pod.records.list("bg_ai_providers", limit=1, filter=[
            {"field": "organization_id", "op": "eq", "value": org_id}]).to_dict()["items"]:
        pod.records.bulk_create("bg_ai_providers", [
            {"organization_id": org_id, "provider": "lemma_platform_runtime",
             "model": "platform-managed (pod agent brandguard-analyst)", "configured": True, "is_default": True,
             "status_note": "Analysis runs on the platform runtime. No model key is stored in this app.",
             "data_flow_note": "Submitted content, the workspace's active guidelines, approved facts and asset "
                               "metadata are sent to the platform model for analysis.",
             "last_checked_at": _now()},
            {"organization_id": org_id, "provider": "external_llm_api", "configured": False,
             "status_note": "Not configured - BrandGuard AI does not call an external LLM API directly.",
             "data_flow_note": "Nothing is transmitted to an external provider.",
             "last_checked_at": _now()}])

    # guidelines
    g_count = 0
    for g in guidelines:
        row = pod.table("bg_guidelines").create({
            "organization_id": org_id, "code": g["code"], "title": g["title"],
            "description": g.get("rule_text"), "category": g["category"], "rule_type": g["rule_type"],
            "severity": g["severity"], "enforcement": g["enforcement"], "rule_text": g["rule_text"],
            "compliant_examples": g.get("compliant_examples") or [],
            "noncompliant_examples": g.get("noncompliant_examples") or [],
            "match_terms": g.get("match_terms"),
            "source_section": g.get("source_section"), "version": 1,
            "status": g.get("status") or "active",
            "created_by": ctx.user_id, "created_by_email": email,
            "approved_by": email if (g.get("status") or "active") == "active" else None,
            "approved_at": _now() if (g.get("status") or "active") == "active" else None,
            "notes": "Seeded workspace rule." if (g.get("status") or "active") == "active"
                     else "Draft rule awaiting approval."})
        pod.table("bg_guideline_versions").create({
            "guideline_id": row["id"], "version": 1,
            "snapshot": {k: v for k, v in row.items() if k not in ("created_at", "updated_at")},
            "change_summary": "Created with the workspace", "changed_by": ctx.user_id,
            "changed_by_email": email})
        g_count += 1

    # facts + evidence
    f_count = 0
    for f in facts:
        row = pod.table("bg_facts").create({
            "organization_id": org_id, "statement": f["statement"], "category": f["category"],
            "scope": f.get("scope") or "As stated in the approved record.",
            "value_text": f.get("value_text"), "numeric_value": f.get("numeric_value"),
            "unit": f.get("unit"), "match_keywords": f.get("match_keywords") or [],
            "source_location": f.get("source_location"),
            "verification_status": f["verification_status"], "approval_status": f["approval_status"],
            "effective_date": f.get("effective_date"), "expiration_date": f.get("expiration_date"),
            "owner": f.get("owner") or email, "last_verified_at": _now(), "version": 1,
            "notes": None})
        f_count += 1
        if f["category"] == "certification":
            pod.table("bg_fact_evidence").create({
                "fact_id": row["id"], "evidence_type": "certificate",
                "title": "AHAM certificate AHAM-2026-1188",
                "description": "Certificate covering smoke CADR 250 for the AirPure 300. Scope is limited to the "
                               "tested model and test conditions.",
                "source_location": "Certificate AHAM-2026-1188, page 1", "strength": "strong", "added_by": email})
        if f["verification_status"] == "no_evidence":
            pod.table("bg_fact_evidence").create({
                "fact_id": row["id"], "evidence_type": "internal_record",
                "title": "Claims register entry C-2026-014",
                "description": "Claim rejected: no laboratory evidence supplied. The claim may not be used.",
                "source_location": "Claims register, entry C-2026-014", "strength": "unverified", "added_by": email})

    # assets (real PNG files with perceptual hashes)
    a_count = 0
    asset_ids = {}
    if data.slug == "northstar-living":
        navy, gold, sand = (12, 35, 64), (232, 179, 60), (245, 241, 234)
        specs = [
            ("logo-primary", "Northstar Living logo — primary", "logo", "approved", navy, gold, "square"),
            ("logo-reversed", "Northstar Living logo — reversed", "logo_variant", "approved", sand, navy, "circle"),
            ("logo-2021", "Northstar Living logo 2021", "logo", "archived", (12, 35, 64), (200, 120, 40), "square"),
            ("airpure-banner", "AirPure 300 launch banner", "campaign_image", "approved", (31, 122, 108), gold, "bar"),
        ]
    else:
        specs = [("meridian-logo", "Meridian Foods logo", "logo", "approved", (59, 47, 42), (200, 161, 90), "square")]
    for fname, aname, category, status, bg, accent, mark in specs:
        px = _logo_pixels(320, 160, bg, accent, mark)
        path = _upload_asset_file(pod, fname, 320, 160, px)
        row = pod.table("bg_assets").create({
            "organization_id": org_id, "name": aname, "category": category, "file_path": path,
            "mime_type": "image/png", "size_bytes": len(_png_bytes(320, 160, px)), "version": 1,
            "approval_status": status, "usage_restrictions":
                "Keep proportions. Do not recolour." if category in ("logo", "logo_variant")
                else "Approved for the AirPure 300 launch only.",
            "dominant_colors": _dominant(320, 160, px), "image_hash": _ahash(320, 160, px),
            "width": 320, "height": 160,
            "uploaded_by": ctx.user_id, "uploaded_by_email": email,
            "approved_by": email if status == "approved" else None,
            "approved_at": _now() if status == "approved" else None,
            "notes": "Superseded in 2025 — kept for historical review records." if status == "archived" else None})
        pod.table("bg_asset_versions").create({
            "asset_id": row["id"], "version": 1, "file_path": path, "change_summary": "Initial upload",
            "created_by": email})
        asset_ids[fname] = row
        a_count += 1

    # starting reviews
    review_ids = []
    peer_email = "abhijeet.bansode24@vit.edu"
    peer_name = "Abhijeet Bansode"
    if data.with_reviews:
        for r in reviews:
            images = []
            if r.get("image") and r["image"] in asset_ids:
                a = asset_ids[r["image"]]
                images.append({"path": a.get("file_path"), "name": a["name"], "width": a.get("width"),
                               "height": a.get("height"), "ahash": a.get("image_hash"),
                               "colors": a.get("dominant_colors")})
            rev = pod.table("bg_reviews").create({
                "organization_id": org_id, "title": r["title"], "content_type": r["content_type"],
                "campaign": r.get("campaign"), "audience": r.get("audience"), "channel": r.get("channel"),
                "language": "en", "context_notes": r.get("notes"),
                "source_text": r["text"], "current_text": r["text"],
                "status": "submitted", "current_version": 1,
                "created_by": ctx.user_id if r.get("creator") != "peer" else None,
                "created_by_email": peer_email if r.get("creator") == "peer" else email,
                "created_by_name": peer_name if r.get("creator") == "peer"
                                   else email.split("@")[0].replace(".", " ").title(),
                "analysis_status": "queued", "submitted_at": _now(), "image_paths": images})
            pod.table("bg_review_versions").create({
                "review_id": rev["id"], "version": 1, "content": r["text"],
                "change_summary": "Original submission", "change_source": "original",
                "author_id": ctx.user_id, "author_email": email})
            text = r["text"]
            seq, pos = 1, 0
            for block in [b for b in text.split("\n\n") if b.strip()]:
                start = text.find(block, pos)
                pod.table("bg_segments").create({
                    "review_id": rev["id"], "seq": seq, "kind": "text", "content": block.strip(),
                    "start_offset": start, "end_offset": start + len(block)})
                seq += 1
                pos = start + len(block)
            review_ids.append(rev["id"])

    pod.table("bg_audit_logs").create({
        "organization_id": org_id, "actor_id": ctx.user_id, "actor_email": email,
        "action": "workspace.seeded" if created else "workspace.reset",
        "subject_type": "bg_organizations", "subject_id": org_id,
        "summary": f"Workspace '{name}' provisioned: {g_count} rules, {f_count} approved facts, {a_count} assets, "
                   f"{len(review_ids)} submissions",
        "metadata": {"slug": data.slug, "reset": data.reset}})

    return BgSeedWorkspaceResult(
        organization_id=org_id, slug=data.slug, created=created, guidelines=g_count, facts=f_count,
        assets=a_count, reviews=review_ids,
        message=f"Workspace '{name}' is ready: {g_count} guideline rules, {f_count} approved facts, {a_count} "
                f"assets and {len(review_ids)} submissions to review.")

#input_type_name: BgCampaignScanInput
#output_type_name: BgCampaignScanResult
#function_name: bg_campaign_scan

"""Cross-channel brand consistency scanner.

Reads every submitted piece of a campaign - social posts, website copy, emails, brochures,
advertisements - extracts the factual signals each one carries (prices, warranty terms,
product names, discounts, slogans, availability dates, claim strength) and reports the ones
that disagree with each other or with the approved facts. The score is a weighted penalty
built from those conflicts, and the breakdown is returned so it can be explained.
"""
import re
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-campaign-1.0"
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
PUBLIC_CHANNELS = ("instagram", "facebook", "linkedin", "tiktok", "x", "twitter", "website", "web",
                   "blog", "email", "print", "display", "ads", "banner", "landing")
CLAIM_WORDS = [r"\bguarantee[ds]?\b", r"\bbest\b", r"\bnumber one\b", r"#1", r"\b100\s?%",
               r"\bclinically proven\b", r"\bunbeatable\b", r"\bworld'?s best\b", r"\bcures?\b",
               r"\bcarbon[- ]neutral\b", r"\brisk[- ]free\b", r"\boutperforms?\b"]
PRICE_RE = re.compile(r"[$€£₹]\s?(\d[\d,]*(?:\.\d+)?)")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
WARRANTY_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+)\s*[- ]?\s*(year|month)s?\b", re.I)
PRODUCT_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:\s+(?:[A-Z][A-Za-z0-9]*|\d{1,4})){1,3})\b")
SLOGAN_RE = re.compile(r"[“\"']([^”\"']{6,80})[”\"']")
STOP_TOKENS = {"the", "our", "this", "that", "every", "united", "kingdom", "only", "ever", "best",
               "value", "internal", "not", "do", "meet", "new", "all", "more", "less", "save",
               "offer", "valid", "while", "stocks", "last", "discount", "applies", "recommended",
               "retail", "price", "free", "warranty", "year", "years", "month", "months"}
DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|"
                     r"November|December)\s+(20\d{2})\b")


class BgCampaignScanInput(BaseModel):
    organization_id: str
    campaign: str | None = None
    min_channels: int = 2
    persist: bool = True


class BgCampaignScanResult(BaseModel):
    scans: list[dict]
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _clean(candidate):
    words = candidate.split()
    while words and words[0].lower() in ("the", "our", "this", "that", "a", "an"):
        words = words[1:]
    return " ".join(words).strip()


def _plausible(candidate, brand_tokens):
    """A candidate product name: multi-token, not a stop phrase, and either branded or numbered."""
    words = candidate.split()
    if not (2 <= len(words) <= 4):
        return False
    if candidate.isupper():
        return False
    lowered = [w.lower().strip(".,!?") for w in words]
    if any(w in STOP_TOKENS for w in lowered):
        return False
    if len(set(lowered)) < len(lowered):
        return False
    has_digit = any(ch.isdigit() for ch in candidate)
    has_brand = any(w in brand_tokens for w in lowered)
    return has_digit or has_brand


def _similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _fuzzy_approved(candidate, approved_names, threshold=0.62):
    """Which approved name does this candidate most resemble? Longer, more specific names win ties,
    so a product name beats the brand name it contains."""
    best, best_ratio = None, 0.0
    for name in sorted(approved_names, key=len, reverse=True):
        ratio = _similar(candidate, name)
        # also compare token by token, so transposed spellings (PureAir vs AirPure) are caught
        for token in candidate.split():
            for approved_token in name.split():
                token_ratio = _similar(token, approved_token)
                if token_ratio >= 0.8:
                    ratio = max(ratio, 0.62)
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    return (best, best_ratio) if best_ratio >= threshold else (None, best_ratio)


def _most_specific(matches):
    """Pick the longest name from a set of matches (product name over brand name)."""
    return sorted(set(matches), key=len, reverse=True)[0] if matches else None


def extract_signals(review, approved_names, brand_tokens):
    """Pull the comparable facts out of one piece of content."""
    text = review.get("current_text") or review.get("source_text") or ""
    out = {"prices": [], "warranties": [], "discounts": [], "products": [], "slogans": [],
           "dates": [], "claims": []}

    candidates = [_clean(c) for c in PRODUCT_RE.findall(text)]
    candidates = [c for c in candidates if _plausible(c, brand_tokens)]
    identity, identity_ratio = None, 0.0
    identity_matches = []
    for candidate in candidates:
        matched, ratio = _fuzzy_approved(candidate, approved_names)
        if matched:
            identity_matches.append(matched)
            if ratio >= identity_ratio:
                identity, identity_ratio = matched, ratio
    identity = _most_specific(identity_matches) or identity
    if not identity and candidates:
        identity = candidates[0]

    def subject_for(sentence):
        matches = [name for name in approved_names if name.lower() in sentence.lower()]
        for candidate in [_clean(c) for c in PRODUCT_RE.findall(sentence)]:
            matched, _ = _fuzzy_approved(candidate, approved_names)
            if matched:
                matches.append(matched)
        return _most_specific(matches) or identity or "the campaign"

    for sentence in _sentences(text):
        for m in PRICE_RE.finditer(sentence):
            value = m.group(1).replace(",", "")
            out["prices"].append({"value": f"${value}", "raw": m.group(0), "subject": subject_for(sentence)})
        for m in PERCENT_RE.finditer(sentence):
            if re.search(r"off|save|discount|deal|offer", sentence, re.I):
                out["discounts"].append({"value": f"{m.group(1)}% off", "raw": m.group(0),
                                         "subject": "the campaign offer"})
        for m in WARRANTY_RE.finditer(sentence):
            if re.search(r"warrant|guarantee|covered", sentence, re.I):
                number = m.group(1).lower()
                amount = WORD_NUMBERS.get(number)
                if amount is None:
                    try:
                        amount = int(number)
                    except ValueError:
                        amount = None
                if amount is None:
                    continue
                unit = m.group(2).lower()
                months = amount * 12 if unit.startswith("year") else amount
                label = f"{amount} {unit}{'' if amount == 1 else 's'}"
                out["warranties"].append({"value": label, "months": months, "raw": m.group(0),
                                          "subject": subject_for(sentence)})
        for m in SLOGAN_RE.finditer(text):
            out["slogans"].append({"value": m.group(1).strip(), "raw": m.group(0), "subject": "the campaign"})
        for m in DATE_RE.finditer(sentence):
            out["dates"].append({"value": f"{m.group(1)} {m.group(2)}", "raw": m.group(0),
                                 "subject": "availability"})
        for pattern in CLAIM_WORDS:
            m = re.search(pattern, sentence, re.I)
            if m:
                out["claims"].append({"value": m.group(0).lower(), "raw": m.group(0),
                                      "subject": subject_for(sentence)})

    for candidate in candidates:
        matched, ratio = _fuzzy_approved(candidate, approved_names)
        out["products"].append({"value": candidate, "raw": candidate,
                                "approved_match": matched, "similarity": round(ratio, 2),
                                "is_approved": bool(matched and ratio >= 0.9)})
    out["identity"] = identity
    return out


SEVERITY_PENALTY = {"critical": 30, "high": 22, "medium": 14, "low": 6}
SIGNAL_SEVERITY = {"prices": "high", "warranties": "high", "products": "high", "discounts": "high",
                   "dates": "medium", "slogans": "medium"}


def compare(signals_by_review, approved_facts, approved_names):
    """Find signals that disagree across channels or contradict the approved facts."""
    conflicts = []
    grouped = defaultdict(lambda: defaultdict(list))
    for review_id, data in signals_by_review.items():
        for kind, items in data["signals"].items():
            # "products" is compared separately (naming), "claims"/"identity" are not comparable values
            if kind in ("claims", "identity", "products") or not isinstance(items, list):
                continue
            for item in items:
                subject = (item.get("subject") or "the campaign").strip()
                grouped[(kind, subject)][item["value"]].append({
                    "review_id": review_id, "title": data["title"], "channel": data["channel"],
                    "content_type": data["content_type"], "raw": item.get("raw")})

    for (kind, subject), values in grouped.items():
        if len(values) < 2:
            continue
        severity = SIGNAL_SEVERITY.get(kind, "medium")
        channels = {v["channel"] or "unspecified" for items in values.values() for v in items}
        if len(channels) < 2:
            continue
        approved = None
        for fact in approved_facts:
            if subject.lower() in (fact.get("statement") or "").lower():
                approved = fact
                break
        conflicts.append({
            "type": kind,
            "subject": subject,
            "severity": severity,
            "values": [{"value": value, "channels": sorted({i["channel"] or "unspecified" for i in items}),
                        "items": items} for value, items in sorted(values.items())],
            "approved_reference": ({"fact_id": approved["id"], "statement": approved.get("statement"),
                                    "value_text": approved.get("value_text"),
                                    "verification_status": approved.get("verification_status"),
                                    "expiration_date": approved.get("expiration_date")}
                                   if approved else None),
            "explanation": (
                f"“{subject}” is described as {' / '.join(sorted(values))} across "
                f"{len(channels)} channels. A reader who sees more than one of these gets two different facts."
            ),
            "recommended_action": (
                f"Agree one value for {kind.replace('_', ' ')} on “{subject}” and update the other channel(s); "
                + ("align it with the approved fact." if approved else "add the agreed value to the approved facts library.")
            ),
            "penalty": SEVERITY_PENALTY[severity],
        })

    # product naming: a name that is not the approved one, especially a near-miss of it
    approved_names = sorted(set(approved_names))
    seen = set()
    for review_id, data in signals_by_review.items():
        for item in data["signals"]["products"]:
            name = item["value"]
            if name in seen or item.get("is_approved"):
                continue
            seen.add(name)
            matched, ratio = item.get("approved_match"), item.get("similarity") or 0
            if matched and ratio >= 0.62:
                conflicts.append({
                    "type": "product_names", "subject": name, "severity": "high",
                    "values": [{"value": name, "channels": [data["channel"] or "unspecified"],
                                "items": [{"review_id": review_id, "title": data["title"],
                                           "channel": data["channel"],
                                           "content_type": data["content_type"], "raw": item.get("raw")}]}],
                    "approved_reference": {"approved_names": approved_names, "closest": matched,
                                           "similarity": ratio},
                    "explanation": (f"“{name}” is used in this campaign and closely resembles the approved name "
                                    f"“{matched}” (similarity {ratio}). Near-miss spellings of a product name are "
                                    "the most common cross-channel inconsistency."),
                    "recommended_action": f"Replace “{name}” with the approved name “{matched}” everywhere it appears.",
                    "penalty": 22,
                })
            elif not matched:
                conflicts.append({
                    "type": "product_names", "subject": name, "severity": "medium",
                    "values": [{"value": name, "channels": [data["channel"] or "unspecified"],
                                "items": [{"review_id": review_id, "title": data["title"],
                                           "channel": data["channel"],
                                           "content_type": data["content_type"], "raw": item.get("raw")}]}],
                    "approved_reference": {"approved_names": approved_names},
                    "explanation": (f"“{name}” appears in this campaign but matches no approved product name."),
                    "recommended_action": ("Confirm the name and either correct it or add it to the approved facts "
                                           "library so every channel can be checked against it."),
                    "penalty": 14,
                })
    return conflicts


def bg_campaign_scan(ctx: FunctionContext, data: BgCampaignScanInput) -> BgCampaignScanResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    if not _member(pod, data.organization_id, email):
        raise PermissionError("You are not a member of this workspace.")

    reviews = pod.records.list("bg_reviews", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    reviews = [r for r in reviews if r.get("status") not in ("draft", "archived")]
    facts = pod.records.list("bg_facts", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    approved = [f for f in facts if f.get("approval_status") == "approved"]
    approved_subjects = []
    for fact in approved:
        m = PRODUCT_RE.search(fact.get("statement") or "")
        if m:
            name = _clean(m.group(1))
            if name and name not in approved_subjects:
                approved_subjects.append(name)
    brand_tokens = set()
    for name in approved_subjects:
        for token in name.lower().split():
            if token not in STOP_TOKENS and len(token) > 2:
                brand_tokens.add(token)

    campaigns = defaultdict(list)
    for review in reviews:
        campaigns[review.get("campaign") or "(no campaign)"].append(review)
    if data.campaign:
        campaigns = {data.campaign: campaigns.get(data.campaign, [])}

    results = []
    for campaign, items in campaigns.items():
        if not items:
            results.append({"campaign": campaign, "status": "failed", "review_count": 0,
                            "consistency_score": None,
                            "summary": f"No submitted content found for campaign “{campaign}”.",
                            "conflicts": [], "channels": []})
            continue
        signals_by_review = {}
        channel_map = defaultdict(list)
        for review in items:
            signals_by_review[review["id"]] = {
                "signals": extract_signals(review, approved_subjects, brand_tokens),
                "title": review["title"],
                "channel": review.get("channel") or "",
                "content_type": review.get("content_type") or "",
                "audience": review.get("audience") or "",
            }
            channel_map[review.get("channel") or "unspecified"].append(
                {"review_id": review["id"], "title": review["title"],
                 "content_type": review.get("content_type"), "status": review.get("status")})

        conflicts = compare(signals_by_review, approved, approved_subjects)
        channels = sorted(channel_map)
        penalty = sum(c["penalty"] for c in conflicts)
        score = max(0.0, 100.0 - penalty)
        breakdown = [{"reason": f"{c['type']} conflict on “{c['subject']}”", "points": c["penalty"],
                      "severity": c["severity"]} for c in conflicts]
        if len(channels) < data.min_channels:
            summary = (f"Only {len(channels)} channel(s) carry this campaign, so cross-channel consistency cannot "
                       f"be compared yet. {len(conflicts)} conflict(s) were still found against the approved facts.")
        elif not conflicts:
            summary = (f"{len(items)} submissions across {len(channels)} channels agree on price, warranty, "
                       "product naming and offer wording.")
        else:
            summary = (f"{len(conflicts)} cross-channel conflict(s) across {len(channels)} channels: "
                       + "; ".join(f"{c['type']} on {c['subject']}" for c in conflicts[:4]) + ".")

        scan = {
            "organization_id": data.organization_id, "campaign": campaign, "status": "completed",
            "consistency_score": round(score, 1), "review_count": len(items), "channel_count": len(channels),
            "signals": {"breakdown": breakdown, "per_review": {
                rid: {k: v for k, v in d["signals"].items() if v} for rid, d in signals_by_review.items()}},
            "conflicts": conflicts, "channels": [{"channel": c, "items": channel_map[c]} for c in channels],
            "summary": summary, "engine_version": ENGINE_VERSION, "scanned_by": email, "scanned_at": _now(),
        }
        if data.persist:
            saved = pod.table("bg_campaign_scans").create(scan)
            scan["id"] = saved["id"]
        results.append(scan)

    if data.persist and results:
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "action": "campaign.scanned", "subject_type": "bg_campaign_scans", "subject_id": None,
            "summary": f"Cross-channel scan of {len(results)} campaign(s): "
                       + ", ".join(f"{r['campaign']} {r.get('consistency_score')}" for r in results[:4]),
            "metadata": {"engine": ENGINE_VERSION}})

    return BgCampaignScanResult(
        scans=results,
        message=f"Scanned {len(results)} campaign(s) across "
                f"{sum(len(r.get('channels') or []) for r in results)} channels.")

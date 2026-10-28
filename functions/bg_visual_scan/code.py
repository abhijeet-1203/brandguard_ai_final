#input_type_name: BgVisualScanInput
#output_type_name: BgVisualScanResult
#function_name: bg_visual_scan

"""Visual brand compliance with text extraction.

Two independent layers, both reported honestly:

1. **Pixel analysis (deterministic, pure Python).** PNG files are decoded and measured -
   palette conformance against the workspace brand colours, content bounding box and safe
   margins, the largest logo-like block, text bands, and comparison with the closest approved
   asset's geometry. An annotated overlay image is written so a reviewer can see exactly which
   regions were measured.
2. **Text extraction (vision model).** The runtime has no OCR engine, so a vision agent reads
   the creative and transcribes every visible word; that transcription is then run through the
   same terminology, disclaimer, claim and confidentiality checks used on submitted copy, and
   any findings are attached to the review.

Checks that cannot be performed are recorded as limitations - never as passes.
"""
import asyncio
import io
import re
import struct
import time
import zlib
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-visual-1.0"
VISION_AGENT = "brandguard-visual-inspector"
CLAIM_WORDS = [r"\bguarantee[ds]?\b", r"\bbest\b", r"\bnumber one\b", r"#1", r"\b100\s?%",
               r"\bclinically proven\b", r"\bunbeatable\b", r"\bworld'?s best\b", r"\bcures?\b",
               r"\bcarbon[- ]neutral\b", r"\brisk[- ]free\b"]
CONFIDENTIAL = [r"\binternal only\b", r"\bdo not (?:distribute|share)\b", r"\bconfidential\b",
                r"\bembargoed?\b", r"\bunreleased\b"]


class BgVisualScanInput(BaseModel):
    organization_id: str
    review_id: str | None = None
    image_path: str | None = None
    image_name: str | None = None
    use_vision: bool = True
    persist: bool = True


class BgVisualScanResult(BaseModel):
    scans: list[dict]
    findings_created: int
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


# --------------------------------------------------------------------- PNG (stdlib only)
def decode_png(data):
    """Minimal PNG decoder: 8-bit greyscale/RGB/RGBA, non-interlaced. Returns (w, h, pixels)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, width, height, depth, colour_type, idat = 8, None, None, None, None, bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, colour_type, comp, filt, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or interlace != 0 or colour_type not in (0, 2, 6):
                raise ValueError(f"unsupported PNG (depth {depth}, colour type {colour_type}, interlace {interlace})")
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
    channels = {0: 1, 2: 3, 6: 4}[colour_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    src = 0
    for y in range(height):
        ftype = raw[src]
        src += 1
        line = bytearray(raw[src:src + stride])
        src += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)


def encode_png(width, height, pixels):
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def to_rgb(width, height, channels, pixels):
    if channels == 3:
        return bytearray(pixels)
    out = bytearray(width * height * 3)
    for i in range(width * height):
        if channels == 4:
            out[i * 3:i * 3 + 3] = pixels[i * 4:i * 4 + 3]
        else:
            v = pixels[i]
            out[i * 3:i * 3 + 3] = bytes((v, v, v))
    return out


def hex_of(pixel):
    return "#%02x%02x%02x" % pixel


def analyse(width, height, rgb, brand_colors, template):
    """Deterministic geometry, palette and text-band measurements."""
    total = width * height
    counts = {}
    for i in range(0, total):
        key = (rgb[i * 3] // 24 * 24, rgb[i * 3 + 1] // 24 * 24, rgb[i * 3 + 2] // 24 * 24)
        counts[key] = counts.get(key, 0) + 1
    palette = [{"hex": hex_of(k), "share": round(v / total, 3)}
               for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:6]]
    background = max(counts.items(), key=lambda kv: kv[1])[0]

    def is_background(x, y):
        i = (y * width + x) * 3
        return (abs(rgb[i] - background[0]) < 26 and abs(rgb[i + 1] - background[1]) < 26
                and abs(rgb[i + 2] - background[2]) < 26)

    min_x, min_y, max_x, max_y = width, height, 0, 0
    for y in range(height):
        for x in range(width):
            if not is_background(x, y):
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    bbox = {"x0": round(min_x / width, 3), "y0": round(min_y / height, 3),
            "x1": round((max_x + 1) / width, 3), "y1": round((max_y + 1) / height, 3)} if max_x >= min_x else None
    margins = {"left": round(min_x / width, 3), "top": round(min_y / height, 3),
               "right": round((width - 1 - max_x) / width, 3), "bottom": round((height - 1 - max_y) / height, 3)} \
        if bbox else None

    # text bands: rows where many pixels differ from the row's dominant colour
    bands, run_start = [], None
    for y in range(height):
        diffs = 0
        for x in range(0, width, 2):
            if not is_background(x, y):
                diffs += 1
        dense = diffs > (width / 2) * 0.22
        if dense and run_start is None:
            run_start = y
        elif not dense and run_start is not None:
            if y - run_start >= 4:
                bands.append({"y0": round(run_start / height, 3), "y1": round(y / height, 3)})
            run_start = None
    if run_start is not None and height - run_start >= 4:
        bands.append({"y0": round(run_start / height, 3), "y1": round(height / height, 3)})

    # logo-like block: largest saturated block in the top-left / left third
    logo = None
    grid = 16
    cells = []
    for gy in range(grid):
        for gx in range(grid):
            x0, x1 = gx * width // grid, max(gx * width // grid + 1, (gx + 1) * width // grid)
            y0, y1 = gy * height // grid, max(gy * height // grid + 1, (gy + 1) * height // grid)
            r = g = b = n = 0
            for y in range(y0, y1, 2):
                for x in range(x0, x1, 2):
                    i = (y * width + x) * 3
                    r += rgb[i]; g += rgb[i + 1]; b += rgb[i + 2]; n += 1
            r, g, b = r // n, g // n, b // n
            sat = (max(r, g, b) - min(r, g, b)) / max(1, max(r, g, b))
            cells.append({"gx": gx, "gy": gy, "hex": hex_of((r, g, b)), "sat": round(sat, 2),
                          "x": round(x0 / width, 3), "y": round(y0 / height, 3)})
    left = [c for c in cells if c["gx"] < grid / 3 and c["sat"] >= 0.25]
    if left:
        best = max(left, key=lambda c: c["sat"])
        logo = {"x": best["x"], "y": best["y"], "hex": best["hex"], "saturation": best["sat"],
                "region": "left third" if best["gx"] < grid / 3 else "elsewhere"}

    # palette conformance against the workspace brand colours
    def rgb_of(hex_value):
        h = hex_value.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else None

    conformance = None
    off_palette = []
    if brand_colors:
        targets = [rgb_of(c) for c in brand_colors if rgb_of(c)]
        near = 0
        for key, value in counts.items():
            close = any(sum((a - b) ** 2 for a, b in zip(key, t)) ** 0.5 <= 48 for t in targets)
            if close:
                near += value
            elif value / total >= 0.02:
                off_palette.append({"hex": hex_of(key), "share": round(value / total, 3)})
        conformance = round(near / total, 3)

    deltas = []
    if template and template.get("geometry"):
        tpl = template["geometry"]
        if bbox and tpl.get("bbox"):
            for edge in ("x0", "y0", "x1", "y1"):
                diff = abs(bbox[edge] - tpl["bbox"][edge])
                if diff > 0.12:
                    deltas.append({"measure": f"content {edge}", "submitted": bbox[edge],
                                   "approved_template": tpl["bbox"][edge], "delta": round(diff, 3)})
        if logo and tpl.get("logo"):
            if abs(logo["x"] - tpl["logo"]["x"]) > 0.15 or abs(logo["y"] - tpl["logo"]["y"]) > 0.15:
                deltas.append({"measure": "logo position", "submitted": f"{logo['x']},{logo['y']}",
                               "approved_template": f"{tpl['logo']['x']},{tpl['logo']['y']}",
                               "delta": round(max(abs(logo['x'] - tpl['logo']['x']),
                                                  abs(logo['y'] - tpl['logo']['y'])), 3)})
    return {"palette": palette, "background": hex_of(background), "bbox": bbox, "margins": margins,
            "text_bands": bands, "logo_block": logo, "palette_conformance": conformance,
            "off_palette": off_palette, "template_deltas": deltas, "grid": cells}


def overlay(width, height, rgb, analysis, brand_colors):
    """Draw the measured regions back onto the image so a reviewer can see what was checked."""
    out = bytearray(rgb)
    def box(x0, y0, x1, y1, colour):
        for x in range(max(0, x0), min(width, x1)):
            for y in (y0, y1 - 1):
                if 0 <= y < height:
                    out[(y * width + x) * 3:(y * width + x) * 3 + 3] = bytes(colour)
        for y in range(max(0, y0), min(height, y1)):
            for x in (x0, x1 - 1):
                if 0 <= x < width:
                    out[(y * width + x) * 3:(y * width + x) * 3 + 3] = bytes(colour)
    if analysis.get("bbox"):
        b = analysis["bbox"]
        box(int(b["x0"] * width), int(b["y0"] * height), int(b["x1"] * width), int(b["y1"] * height), (0, 150, 90))
    for band in analysis.get("text_bands") or []:
        box(0, int(band["y0"] * height), width - 1, int(band["y1"] * height), (29, 78, 216))
    if analysis.get("logo_block"):
        lx, ly = int(analysis["logo_block"]["x"] * width), int(analysis["logo_block"]["y"] * height)
        box(max(0, lx - 4), max(0, ly - 4), min(width - 1, lx + 40), min(height - 1, ly + 40), (232, 179, 60))
    grid, cells = 16, analysis.get("grid") or []
    palette_rgb = []
    for colour in brand_colors or []:
        h = colour.lstrip("#")
        if len(h) == 6:
            palette_rgb.append((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    if palette_rgb:
        for cell in cells:
            c = tuple(int(cell["hex"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            if all(sum((a - b) ** 2 for a, b in zip(c, t)) ** 0.5 > 48 for t in palette_rgb):
                x0 = int(cell["x"] * width)
                y0 = int(cell["y"] * height)
                box(x0, y0, min(width - 1, x0 + width // grid), min(height - 1, y0 + height // grid), (180, 35, 24))
    return bytes(out)


async def _ask_vision(pod, path, title, timeout_s=300):
    message = (f"Image path in pod files: {path}\n"
               "Fetch it with the workspace CLI, look at it, and return the structured observation. "
               "Transcribe every visible word exactly; put anything you cannot determine in limitations.")
    conv = pod.agents.run(VISION_AGENT, message, title=title)
    cid = str(conv.id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for m in reversed(pod.conversations.messages(cid, limit=100).to_dict()["items"]):
            md = m.get("metadata") or {}
            if m.get("role") == "assistant" and md.get("is_final_answer"):
                if isinstance(md.get("structured_output"), dict):
                    return md["structured_output"]
        await asyncio.sleep(2)
    return None


def text_checks(text, rules, facts):
    """Run the same rule categories used on submitted copy, over the transcribed text."""
    out = []
    for rule in rules:
        terms = ((rule.get("match_terms") or {}).get("terms")) or []
        kind = rule.get("rule_type")
        if kind in ("prohibited_term", "deprecated_term") and terms:
            for term in terms:
                m = re.search(r"(?<![A-Za-z0-9])" + re.escape(str(term)) + r"(?![A-Za-z0-9])", text, re.I)
                if m:
                    out.append({"category": "terminology", "severity": rule.get("severity") or "medium",
                                "title": f"{'Prohibited' if kind == 'prohibited_term' else 'Deprecated'} term in the creative: “{term}”",
                                "original_text": m.group(0), "rule_code": rule.get("code"), "rule_id": rule["id"],
                                "explanation": f"Text extracted from the image contains “{term}”, which rule "
                                               f"{rule.get('code')} forbids. {rule.get('rule_text')}"})
        elif kind == "required_disclaimer" and terms:
            applies = (rule.get("match_terms") or {}).get("applies_when") or []
            if applies and not any(re.search(re.escape(str(k)), text, re.I) for k in applies):
                continue
            for term in terms:
                if not re.search(re.escape(str(term)), text, re.I):
                    out.append({"category": "messaging", "severity": rule.get("severity") or "high",
                                "title": "Required disclaimer is missing from the creative",
                                "original_text": None, "rule_code": rule.get("code"), "rule_id": rule["id"],
                                "explanation": f"The extracted text does not contain the disclaimer required by "
                                               f"rule {rule.get('code')}: “{term}”.",
                                "suggested_correction": str(term)})
        elif kind == "confidentiality_rule":
            for pattern in (terms or CONFIDENTIAL):
                try:
                    m = re.search(pattern, text, re.I)
                except re.error:
                    m = None
                if m:
                    out.append({"category": "confidentiality", "severity": rule.get("severity") or "critical",
                                "title": f"Confidential marker visible in the creative: “{m.group(0)}”",
                                "original_text": m.group(0), "rule_code": rule.get("code"), "rule_id": rule["id"],
                                "explanation": "Text read from the image matches a confidentiality pattern. "
                                               "A person must confirm before publication."})
                    break
    approved_claims = [f for f in facts if f.get("verification_status") == "verified"
                       and f.get("approval_status") == "approved"]
    for pattern in CLAIM_WORDS:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        sentence = next((s for s in re.split(r"(?<=[.!?])\s+|\n", text) if m.group(0).lower() in s.lower()), text)
        if any(any(str(k).lower() in sentence.lower() for k in (f.get("match_keywords") or []))
               for f in approved_claims):
            continue
        out.append({"category": "promotional_claim", "severity": "high",
                    "title": f"Claim in the creative with no approved evidence: “{m.group(0)}”",
                    "original_text": m.group(0), "rule_code": None, "rule_id": None,
                    "explanation": "The claim was found in text extracted from the image; no approved fact in this "
                                   "workspace substantiates it."})
    return out


async def bg_visual_scan(ctx: FunctionContext, data: BgVisualScanInput) -> BgVisualScanResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    org = pod.table("bg_organizations").get(data.organization_id)
    brand_colors = ((org.get("settings") or {}).get("brand_colors")) or []
    rules = pod.records.list("bg_guidelines", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id},
        {"field": "status", "op": "eq", "value": "active"}]).to_dict()["items"]
    facts = pod.records.list("bg_facts", limit=500, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
    assets = pod.records.list("bg_assets", limit=200, filter=[
        {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]

    targets = []
    review = None
    if data.image_path:
        targets.append({"path": data.image_path, "name": data.image_name or data.image_path.rsplit("/", 1)[-1]})
    elif data.review_id:
        review = pod.table("bg_reviews").get(data.review_id)
        if review["organization_id"] != data.organization_id:
            raise PermissionError("This review belongs to another workspace.")
        for image in (review.get("image_paths") or []):
            if image.get("path"):
                targets.append({"path": image["path"], "name": image.get("name") or "creative",
                                "ahash": image.get("ahash")})
    if not targets:
        raise ValueError("No image to scan. Attach an image to the review or pass an image_path.")

    scans, created = [], 0
    for target in targets:
        limitations, regions = [], []
        geometry, palette, conformance, overlay_path, width, height = None, None, None, None, None, None
        try:
            raw = pod.files.download(target["path"])
        except Exception as e:  # noqa: BLE001
            limitations.append(f"The image could not be read from pod files ({type(e).__name__}); "
                               "no pixel analysis was performed.")
            raw = None
        if raw:
            try:
                width, height, channels, pixels = decode_png(raw)
                rgb = to_rgb(width, height, channels, pixels)
                template = None
                best_sim = 0.0
                submitted_hash = target.get("ahash")
                for asset in assets:
                    if not asset.get("image_hash") or not submitted_hash:
                        continue
                    try:
                        distance = bin(int(submitted_hash, 16) ^ int(asset["image_hash"], 16)).count("1")
                    except Exception:  # noqa: BLE001
                        continue
                    similarity = 1 - distance / 64
                    if similarity > best_sim:
                        best_sim, template = similarity, asset
                geometry = analyse(width, height, rgb, brand_colors, None)
                palette = geometry["palette"]
                conformance = geometry.get("palette_conformance")
                if template:
                    try:
                        t_raw = pod.files.download(template["file_path"])
                        t_w, t_h, t_c, t_px = decode_png(t_raw)
                        t_rgb = to_rgb(t_w, t_h, t_c, t_px)
                        t_analysis = analyse(t_w, t_h, t_rgb, brand_colors, None)
                        geometry["template_deltas"] = analyse(width, height, rgb, brand_colors, t_analysis)["template_deltas"]
                        geometry["template"] = {"asset_id": template["id"], "name": template["name"],
                                                "similarity": round(best_sim, 2),
                                                "approval_status": template.get("approval_status")}
                    except Exception:  # noqa: BLE001
                        limitations.append("The closest approved asset could not be decoded for geometry comparison.")
                else:
                    limitations.append("No approved asset carried a comparable hash, so template geometry "
                                       "comparison was skipped.")
                if geometry.get("bbox") and geometry.get("margins"):
                    tight = [edge for edge, value in geometry["margins"].items() if value < 0.03]
                    if tight:
                        regions.append({"kind": "margin", "severity": "low",
                                        "detail": f"Content reaches the {', '.join(tight)} edge(s) with less than 3% "
                                                  "clear space.",
                                        "box": geometry["bbox"]})
                for off in geometry.get("off_palette") or []:
                    regions.append({"kind": "colour", "severity": "medium",
                                    "detail": f"Colour {off['hex']} covers {round(off['share'] * 100)}% of the image "
                                              "and is outside the approved palette.",
                                    "hex": off["hex"], "share": off["share"]})
                for delta in geometry.get("template_deltas") or []:
                    regions.append({"kind": "template", "severity": "high",
                                    "detail": f"{delta['measure']} differs from the approved asset by "
                                              f"{delta['delta']} (submitted {delta['submitted']} vs "
                                              f"{delta['approved_template']})."})
                if conformance is not None and conformance < 0.6:
                    regions.append({"kind": "colour", "severity": "medium",
                                    "detail": f"Only {round(conformance * 100)}% of the image uses colours within "
                                              "tolerance of the approved palette."})
                try:
                    marked = overlay(width, height, rgb, geometry, brand_colors)
                    slug = org.get("slug") or "workspace"
                    overlay_path = f"/brandguard/{slug}/visual/overlay-{re.sub(r'[^a-zA-Z0-9]', '-', target['name'])[:40]}.png"
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
                        fh.write(encode_png(width, height, marked))
                        tmp = fh.name
                    pod.files.upload(tmp, path=overlay_path,
                                     description="BrandGuard AI visual check overlay (measured regions)")
                except Exception as e:  # noqa: BLE001
                    limitations.append(f"Annotated overlay could not be written ({type(e).__name__}).")
            except Exception as e:  # noqa: BLE001
                limitations.append(f"Pixel analysis was not possible for this file ({type(e).__name__}: {e}). "
                                   "PNG files are measured directly; other formats are only inspected visually.")

        ocr_text, ocr_source, vision = None, "none", None
        if data.use_vision:
            try:
                vision = await _ask_vision(pod, target["path"], title=f"Visual check: {target['name']}")
            except Exception as e:  # noqa: BLE001
                limitations.append(f"The vision inspection failed ({type(e).__name__}: {e}).")
                vision = None
            if vision:
                ocr_text = (vision.get("text") or "").strip() or None
                ocr_source = "vision_model"
                limitations.extend([str(x) for x in (vision.get("limitations") or [])])
                if not ocr_text:
                    limitations.append("No text was found in the image.")
            else:
                limitations.append("No text could be extracted: the vision model did not return an observation.")
        else:
            limitations.append("Vision inspection was disabled for this scan, so text inside the image was not read.")

        findings = text_checks(ocr_text, rules, facts) if ocr_text else []
        if ocr_text and not findings:
            limitations.append("The extracted text passed the terminology, disclaimer, claim and confidentiality checks.")

        if data.persist:
            existing = pod.records.list("bg_visual_scans", limit=200, filter=[
                {"field": "organization_id", "op": "eq", "value": data.organization_id}]).to_dict()["items"]
            prior = next((s for s in existing if s.get("image_path") == target["path"]), None)
            payload = {
                "organization_id": data.organization_id, "review_id": data.review_id,
                "image_path": target["path"], "image_name": target["name"], "status": "completed",
                "engine": ENGINE_VERSION, "ocr_text": ocr_text, "ocr_source": ocr_source,
                "width": width, "height": height, "palette": palette, "geometry": geometry,
                "regions": regions, "palette_conformance": conformance, "overlay_path": overlay_path,
                "findings_count": len(findings), "limitations": limitations, "created_by": email,
            }
            score = 100.0
            for region in regions:
                score -= {"high": 20, "medium": 12, "low": 5}.get(region["severity"], 5)
            score -= min(30, 10 * len(findings))
            payload["score"] = max(0.0, round(score, 1))
            if prior:
                pod.table("bg_visual_scans").update(prior["id"], payload)
                scan_id = prior["id"]
            else:
                created_row = pod.table("bg_visual_scans").create(payload)
                scan_id = created_row["id"]

            if data.review_id and findings:
                previous = [f for f in pod.records.list("bg_findings", limit=500, filter=[
                    {"field": "review_id", "op": "eq", "value": data.review_id}]).to_dict()["items"]
                    if f.get("detection_method") == "visual" and f.get("review_status") == "open"
                    and str(f.get("location_excerpt") or "").startswith(target["name"])]
                for old in previous:
                    pod.table("bg_findings").update(old["id"], {
                        "review_status": "resolved", "resolved_at": _now(), "resolved_by": "brandguard-visual",
                        "resolution_note": "No longer detected in the latest visual scan."})
                for finding in findings:
                    created_finding = pod.table("bg_findings").create({
                        "organization_id": data.organization_id, "review_id": data.review_id,
                        "run_id": None, "version": (review or {}).get("current_version"),
                        "category": finding["category"], "title": finding["title"][:300],
                        "description": f"Found in text extracted from “{target['name']}” "
                                       f"({width}×{height} px) by the vision model.",
                        "severity": finding["severity"], "confidence": 0.8,
                        "evidence_status": "supported" if finding.get("rule_id") else "not_found",
                        "original_text": finding.get("original_text"),
                        "location_excerpt": f"{target['name']}: {str(finding.get('original_text'))[:120]}",
                        "rule_id": finding.get("rule_id"), "rule_code": finding.get("rule_code"),
                        "source_reference": {"visual_scan_id": scan_id, "image_path": target["path"],
                                             "extraction": "vision_model",
                                             "note": finding.get("explanation")},
                        "explanation": finding.get("explanation"),
                        "suggested_correction": finding.get("suggested_correction"),
                        "review_status": "open", "detection_method": "visual",
                        "fingerprint": f"{finding['category']}|{str(finding.get('original_text'))[:60].lower()}|visual:{target['name'][:30]}",
                    })
                    created += 1

        scans.append({"image_path": target["path"], "image_name": target["name"], "status": "completed",
                      "width": width, "height": height, "palette": palette, "geometry": geometry,
                      "regions": regions, "palette_conformance": conformance, "overlay_path": overlay_path,
                      "ocr_text": ocr_text, "ocr_source": ocr_source, "vision": vision,
                      "findings": findings, "limitations": limitations})

    if data.persist and scans:
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "actor_name": member.get("display_name"), "action": "visual.scanned",
            "subject_type": "bg_visual_scans", "subject_id": None,
            "summary": f"Visual compliance scan of {len(scans)} creative(s); {created} finding(s) recorded",
            "metadata": {"engine": ENGINE_VERSION}})
    return BgVisualScanResult(
        scans=scans, findings_created=created,
        message=(f"Scanned {len(scans)} creative(s): {created} finding(s) recorded. "
                 + ("Text was read from the image by the vision model and checked against the workspace rules."
                    if any(s["ocr_text"] for s in scans) else
                    "No text could be extracted from the image, so only geometry and colour were checked.")))

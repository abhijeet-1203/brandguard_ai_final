#input_type_name: BgCreativeScanInput
#output_type_name: BgCreativeScanResult
#function_name: bg_creative_scan

"""Multi-format creative compliance, page by page.

Beyond the original PNG-only check this handles JPEG, WebP, TIFF, GIF and BMP creatives and
multi-page PDFs, and writes **one evidence overlay per page**.

Layers, each reported honestly:

1. Decoding - a real decoder (ImageMagick, present in this runtime) handles the raster formats,
   and the platform's converted page images are used for PDFs. The decoder actually used is
   recorded; a file that cannot be decoded is reported, not guessed at.
2. Page geometry - palette and palette conformance, content bounds and safe margins, text bands,
   the largest logo-like block, and an annotated overlay per page written back to pod files.
3. Text with word boxes and confidence - a vision model transcribes every word with its box and
   a reading-confidence estimate. This runtime has **no dedicated OCR engine** (no tesseract, no
   Pillow, no dependency builder), which is recorded on the scan rather than glossed over.
4. Dual-read cross-check - the model gives a second independent read of the headline, price and
   disclaimer; the two reads are compared and a per-page agreement score is stored.
5. Accessibility - WCAG contrast ratios measured inside each text band, and a minimum text-height
   check against a stated pixel floor.
6. Logo verification - aspect ratio against the approved asset (stretching), colour against the
   approved brand colours (recolouring), and clear space against the usage restriction.
"""
import asyncio
import re
import struct
import subprocess
import tempfile
import time
import zlib
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

ENGINE_VERSION = "bg-creative-1.0"
VISION_AGENT = "brandguard-creative-inspector"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif", ".bmp", ".avif")
PDF_EXT = (".pdf",)
DECK_EXT = (".ppt", ".pptx", ".key", ".odp")
CONVERT = "/usr/bin/convert"
MIN_CONTRAST = 4.5
MIN_CONTRAST_LARGE = 3.0
LARGE_TEXT_PX = 24
MIN_TEXT_PX = 12
CLEAR_SPACE_RATIO = 0.25
CLAIM_WORDS = [r"\bguarantee[ds]?\b", r"\bbest\b", r"\bnumber one\b", r"#1", r"\b100\s?%",
               r"\bclinically proven\b", r"\bunbeatable\b", r"\bworld'?s best\b", r"\bcures?\b",
               r"\bcarbon[- ]neutral\b", r"\brisk[- ]free\b"]
CONFIDENTIAL = [r"\binternal only\b", r"\bdo not (?:distribute|share)\b", r"\bconfidential\b",
                r"\bembargoed?\b", r"\bunreleased\b"]


class BgCreativeScanInput(BaseModel):
    organization_id: str
    review_id: str | None = None
    file_path: str | None = None
    file_name: str | None = None
    max_pages: int = 12
    use_vision: bool = True
    persist: bool = True


class BgCreativeScanResult(BaseModel):
    scan_id: str | None = None
    file_kind: str
    decoder: str | None = None
    page_count: int
    analysed_pages: int
    score: float | None = None
    ocr_engine: str
    ocr_note: str
    pages: list[dict]
    findings_created: int
    limitations: list[str]
    message: str
    engine_version: str = ENGINE_VERSION


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _kind(path):
    lower = (path or "").lower()
    if lower.endswith(PDF_EXT):
        return "pdf"
    if lower.endswith(DECK_EXT):
        return "presentation"
    if lower.endswith(IMAGE_EXT):
        return "image"
    return "unknown"


def decode_png_native(raw):
    try:
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            return None, None, None
        pos, width, height, depth, colour_type, idat = 8, None, None, None, None, bytearray()
        while pos < len(raw):
            length = struct.unpack(">I", raw[pos:pos + 4])[0]
            tag = raw[pos + 4:pos + 8]
            chunk = raw[pos + 8:pos + 8 + length]
            pos += 12 + length
            if tag == b"IHDR":
                width, height, depth, colour_type, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk)
                if depth != 8 or interlace != 0 or colour_type not in (0, 2, 6):
                    return None, None, None
            elif tag == b"IDAT":
                idat += chunk
            elif tag == b"IEND":
                break
        channels = {0: 1, 2: 3, 6: 4}[colour_type]
        data = zlib.decompress(bytes(idat))
        stride = width * channels
        out = bytearray(height * width * 3)
        prev = bytearray(stride)
        src = 0
        for y in range(height):
            ftype = data[src]
            src += 1
            line = bytearray(data[src:src + stride])
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
                    line[i] = (line[i] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))) & 0xFF
            for x in range(width):
                base = (y * width + x) * 3
                if channels == 3:
                    out[base:base + 3] = line[x * 3:x * 3 + 3]
                elif channels == 1:
                    v = line[x]
                    out[base:base + 3] = bytes((v, v, v))
                else:
                    out[base:base + 3] = line[x * 4:x * 4 + 3]
            prev = line
        return width, height, bytes(out)
    except Exception:  # noqa: BLE001
        return None, None, None


def decode_with_imagemagick(raw, suffix, limitations):
    try:
        handle = tempfile.NamedTemporaryFile(suffix=suffix or ".bin", delete=False)
        handle.write(raw)
        handle.close()
        proc = subprocess.run([CONVERT, handle.name, "-depth", "8", "ppm:-"],
                              capture_output=True, timeout=90)
    except Exception as e:  # noqa: BLE001
        limitations.append(f"The decoder could not be run ({type(e).__name__}: {e}).")
        return None, None, None
    if proc.returncode != 0 or not proc.stdout.startswith(b"P6"):
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        limitations.append("The file could not be decoded by ImageMagick"
                           + (f": {detail[0][:150]}" if detail else "."))
        return None, None, None
    data, fields, pos = proc.stdout, [], 2
    while len(fields) < 3 and pos < len(data):
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        fields.append(int(data[start:pos]))
    pos += 1
    width, height, _max = fields
    rgb = data[pos:pos + width * height * 3]
    if len(rgb) < width * height * 3:
        limitations.append("The decoded pixel data was truncated, so measurements were skipped.")
        return None, None, None
    return width, height, bytes(rgb)


def encode_png(width, height, rgb):
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _luminance(pixel):
    def channel(value):
        value = value / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(pixel[0]) + 0.7152 * channel(pixel[1]) + 0.0722 * channel(pixel[2])


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hex_of(pixel):
    return "#%02x%02x%02x" % tuple(pixel)


def rgb_of(value):
    h = (value or "").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else None


def measure(width, height, rgb, brand_colors, template):
    total = width * height
    counts = {}
    for i in range(total):
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
    bbox = ({"x0": round(min_x / width, 3), "y0": round(min_y / height, 3),
             "x1": round((max_x + 1) / width, 3), "y1": round((max_y + 1) / height, 3)}
            if max_x >= min_x else None)
    margins = ({"left": round(min_x / width, 3), "top": round(min_y / height, 3),
                "right": round((width - 1 - max_x) / width, 3), "bottom": round((height - 1 - max_y) / height, 3)}
               if bbox else None)

    runs, start = [], None
    for y in range(height):
        dense = sum(1 for x in range(0, width, 2) if not is_background(x, y)) > (width / 2) * 0.22
        if dense and start is None:
            start = y
        elif not dense and start is not None:
            if y - start >= 4:
                runs.append((start, y))
            start = None
    if start is not None and height - start >= 4:
        runs.append((start, height))

    bands = []
    for y0, y1 in runs[:20]:
        pixels = []
        for y in range(y0, y1, 2):
            for x in range(0, width, 3):
                i = (y * width + x) * 3
                pixels.append((rgb[i], rgb[i + 1], rgb[i + 2]))
        if not pixels:
            continue
        darkest = min(pixels, key=_luminance)
        lightest = max(pixels, key=_luminance)
        ratio = _contrast(darkest, lightest)
        band_px = y1 - y0
        bands.append({"y0": round(y0 / height, 3), "y1": round(y1 / height, 3), "height_px": band_px,
                      "contrast_ratio": round(ratio, 2), "text_colour": hex_of(darkest),
                      "background_colour": hex_of(lightest), "large_text": band_px >= LARGE_TEXT_PX,
                      "meets_minimum_size": band_px >= MIN_TEXT_PX,
                      "passes_contrast": ratio >= (MIN_CONTRAST_LARGE if band_px >= LARGE_TEXT_PX else MIN_CONTRAST)})

    grid, cells = 16, []
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
                          "x": round(x0 / width, 3), "y": round(y0 / height, 3),
                          "w": round((x1 - x0) / width, 3), "h": round((y1 - y0) / height, 3)})
    left = [c for c in cells if c["gx"] < grid / 3 and c["sat"] >= 0.25]
    logo = None
    if left:
        best = max(left, key=lambda c: c["sat"])
        lx0, ly0 = int(best["x"] * width), int(best["y"] * height)
        lx1, ly1 = lx0 + int(best["w"] * width), ly0 + int(best["h"] * height)
        nearest = None
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                if lx0 - 2 <= x <= lx1 + 2 and ly0 - 2 <= y <= ly1 + 2:
                    continue
                if not is_background(x, y):
                    distance = max(lx0 - x, x - lx1, ly0 - y, y - ly1, 0)
                    if nearest is None or distance < nearest:
                        nearest = distance
        logo_width = max(1, lx1 - lx0)
        logo = {"x": best["x"], "y": best["y"], "w": best["w"], "h": best["h"], "hex": best["hex"],
                "saturation": best["sat"], "region": "left third",
                "aspect_ratio": round((best["w"] * width) / max(1, best["h"] * height), 3),
                "clear_space_px": nearest,
                "clear_space_ratio": round((nearest or 0) / logo_width, 3)}

    conformance, off_palette = None, []
    if brand_colors:
        targets = [rgb_of(c) for c in brand_colors if rgb_of(c)]
        near = 0
        for key, value in counts.items():
            if any(sum((a - b) ** 2 for a, b in zip(key, t)) ** 0.5 <= 48 for t in targets):
                near += value
            elif value / total >= 0.02:
                off_palette.append({"hex": hex_of(key), "share": round(value / total, 3)})
        conformance = round(near / total, 3)

    deltas = []
    if template and template.get("geometry") and bbox and template["geometry"].get("bbox"):
        for edge in ("x0", "y0", "x1", "y1"):
            diff = abs(bbox[edge] - template["geometry"]["bbox"][edge])
            if diff > 0.12:
                deltas.append({"measure": f"content {edge}", "submitted": bbox[edge],
                               "approved_template": template["geometry"]["bbox"][edge], "delta": round(diff, 3)})
    return {"palette": palette, "background": hex_of(background), "bbox": bbox, "margins": margins,
            "text_bands": bands, "logo_block": logo, "palette_conformance": conformance,
            "off_palette": off_palette, "template_deltas": deltas, "grid": cells,
            "size_px": {"width": width, "height": height}}


def logo_checks(logo, template, usage_restrictions, limitations):
    checks, regions = [], []
    if not logo:
        limitations.append("No logo-like block was located, so logo checks could not be performed on this page.")
        return checks, regions
    checks.append({"check": "logo located", "ok": True,
                   "evidence": f"block at x {logo['x']}, y {logo['y']} ({logo['hex']})"})
    if template and template.get("width") and template.get("height"):
        expected = template["width"] / max(1, template["height"])
        actual = logo.get("aspect_ratio") or expected
        deviation = abs(actual - expected) / expected if expected else 0
        ok = deviation <= 0.15
        checks.append({"check": "logo not stretched", "ok": ok,
                       "evidence": (f"aspect ratio {round(actual, 2)} against the approved asset's "
                                    f"{round(expected, 2)} ({round(deviation * 100)}% deviation)")})
        if not ok:
            regions.append({"kind": "logo", "severity": "high", "measure": "aspect_ratio",
                            "detail": (f"The logo block's aspect ratio is {round(actual, 2)} where the approved asset "
                                       f"is {round(expected, 2)} - the mark looks stretched or squashed.")})
    else:
        limitations.append("No approved asset carries dimensions, so the logo aspect ratio could not be compared.")
    if template and template.get("dominant_colors"):
        approved = [rgb_of(c) for c in template["dominant_colors"] if rgb_of(c)]
        observed = rgb_of(logo["hex"])
        if approved and observed:
            closest = min(approved, key=lambda t: sum((a - b) ** 2 for a, b in zip(observed, t)))
            distance = sum((a - b) ** 2 for a, b in zip(observed, closest)) ** 0.5
            ok = distance <= 60
            checks.append({"check": "logo not recoloured", "ok": ok,
                           "evidence": (f"logo colour {logo['hex']} is {round(distance)} RGB units from the nearest "
                                        f"approved colour {hex_of(closest)}")})
            if not ok:
                regions.append({"kind": "logo", "severity": "medium", "measure": "logo_colour",
                                "detail": (f"The logo colour {logo['hex']} is {round(distance)} RGB units from the "
                                           "nearest approved brand colour, which suggests recolouring.")})
    if usage_restrictions and re.search(r"clear space|proportions|do not recolour", usage_restrictions, re.I):
        ratio = logo.get("clear_space_ratio") or 0
        ok = ratio >= CLEAR_SPACE_RATIO
        checks.append({"check": "logo clear space respected", "ok": ok,
                       "evidence": (f"nearest other content is {logo.get('clear_space_px')} px away, {ratio}× the "
                                    f"logo width (minimum {CLEAR_SPACE_RATIO}×)")})
        if not ok:
            regions.append({"kind": "logo", "severity": "medium", "measure": "clear_space",
                            "detail": (f"Only {ratio}× the logo width of clear space around the mark; the approved "
                                       f"usage restriction requires {CLEAR_SPACE_RATIO}×.")})
    return checks, regions


def text_checks(text, rules, facts):
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
                                "explanation": f"Text extracted from the page contains “{term}”, which rule "
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
                                "explanation": f"The extracted text does not contain the disclaimer required by rule "
                                               f"{rule.get('code')}: “{term}”.",
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
                                "explanation": "Text read from the page matches a confidentiality pattern. A person "
                                               "must confirm before publication."})
                    break
    approved = [f for f in facts if f.get("verification_status") == "verified"
                and f.get("approval_status") == "approved"]
    for pattern in CLAIM_WORDS:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        sentence = next((s for s in re.split(r"(?<=[.!?])\s+|\n", text) if m.group(0).lower() in s.lower()), text)
        if any(any(str(k).lower() in sentence.lower() for k in (f.get("match_keywords") or [])) for f in approved):
            continue
        out.append({"category": "promotional_claim", "severity": "high",
                    "title": f"Claim in the creative with no approved evidence: “{m.group(0)}”",
                    "original_text": m.group(0), "rule_code": None, "rule_id": None,
                    "explanation": "The claim was found in text extracted from the page; no approved fact in this "
                                   "workspace substantiates it."})
    return out


async def _ask_vision(pod, path, page_note, title, timeout_s=300):
    message = (f"Creative page to inspect - pod file path: {path}\n{page_note}\n"
               "Fetch it with the workspace CLI, look at it, and return the structured observation. Transcribe every "
               "visible word with its box and a confidence estimate, give a second independent read of the headline, "
               "price and disclaimer, and report anything you could not determine.")
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


def dual_read(vision, geometry, limitations):
    if not vision:
        return {"performed": False, "note": "no vision observation was returned"}
    first = (vision.get("text") or "").strip()
    second = " ".join(str(r.get("text") or "").strip() for r in (vision.get("second_read") or [])).strip()
    words = vision.get("words") or []
    confidences = [float(w.get("confidence")) for w in words if w.get("confidence") is not None]
    mean_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
    low = [w.get("text") for w in words if (w.get("confidence") or 1) < 0.6][:10]
    boxes = [w.get("box") for w in words if isinstance(w.get("box"), dict)]
    bands = (geometry or {}).get("text_bands") or []
    matched = 0
    for band in bands:
        for box in boxes:
            y = box.get("y")
            if y is not None and band["y0"] - 0.03 <= y <= band["y1"] + 0.03:
                matched += 1
                break
    box_agreement = round(matched / len(bands), 3) if bands else None

    def norm(value):
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())

    agreement = None
    if first and second:
        a, b = norm(first), norm(second)
        if a and b:
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            agreement = round(1.0 if shorter in longer else len(shorter) / max(1, len(longer)), 3)
    disagreements = [str(d) for d in (vision.get("disagreements") or [])]
    if box_agreement is not None and box_agreement < 0.6:
        limitations.append(f"Only {round(box_agreement * 100)}% of the measured text bands contained a word box "
                           "reported by the vision model, so word positions are only partly corroborated.")
    if agreement is not None and agreement < 0.8:
        limitations.append(f"The two independent readings of the key lines agree on only {round(agreement * 100)}% of "
                           "characters; the transcription is recorded but flagged.")
    return {"performed": True, "first_read_characters": len(first), "second_read_characters": len(second),
            "text_agreement": agreement, "box_agreement": box_agreement, "mean_word_confidence": mean_conf,
            "low_confidence_words": low, "disagreements": disagreements,
            "note": ("both readings agree" if (agreement or 0) >= 0.8
                     else "the two readings differ; the transcription is recorded but flagged")}


def build_overlay(width, height, rgb, geometry, brand_colors):
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
    if geometry.get("bbox"):
        b = geometry["bbox"]
        box(int(b["x0"] * width), int(b["y0"] * height), int(b["x1"] * width), int(b["y1"] * height), (0, 150, 90))
    for band in geometry.get("text_bands") or []:
        colour = (29, 78, 216) if band.get("passes_contrast") else (180, 35, 24)
        box(0, int(band["y0"] * height), width - 1, int(band["y1"] * height), colour)
    if geometry.get("logo_block"):
        lx, ly = int(geometry["logo_block"]["x"] * width), int(geometry["logo_block"]["y"] * height)
        box(max(0, lx - 4), max(0, ly - 4), min(width - 1, lx + 44), min(height - 1, ly + 44), (232, 179, 60))
    targets = [rgb_of(c) for c in (brand_colors or []) if rgb_of(c)]
    if targets:
        for cell in geometry.get("grid") or []:
            c = rgb_of(cell["hex"])
            if c and all(sum((a - b) ** 2 for a, b in zip(c, t)) ** 0.5 > 48 for t in targets):
                x0, y0 = int(cell["x"] * width), int(cell["y"] * height)
                box(x0, y0, min(width - 1, x0 + width // 16), min(height - 1, y0 + height // 16), (180, 35, 24))
    return bytes(out)


async def bg_creative_scan(ctx: FunctionContext, data: BgCreativeScanInput) -> BgCreativeScanResult:
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

    review, targets = None, []
    if data.file_path:
        targets.append({"path": data.file_path, "name": data.file_name or data.file_path.rsplit("/", 1)[-1]})
    elif data.review_id:
        review = pod.table("bg_reviews").get(data.review_id)
        if review["organization_id"] != data.organization_id:
            raise PermissionError("This review belongs to another workspace.")
        for image in (review.get("image_paths") or []):
            if image.get("path"):
                targets.append({"path": image["path"], "name": image.get("name") or "creative",
                                "ahash": image.get("ahash")})
        for path in (review.get("attachment_paths") or []):
            if isinstance(path, str) and _kind(path) in ("pdf", "presentation", "image"):
                targets.append({"path": path, "name": path.rsplit("/", 1)[-1]})
    if not targets:
        raise ValueError("No creative to scan. Attach an image or PDF to the review, or pass a file_path.")

    limitations = []
    ocr_engine = "vision_model (transcription)"
    ocr_note = ("No dedicated OCR engine is available in this runtime: tesseract, Pillow and the usual Python imaging "
                "libraries are absent and the function dependency builder is not installed, so no OCR package could be "
                "added. Text is transcribed by a vision model with word boxes and reading confidence, and every page "
                "is cross-checked with a second independent read.")
    pages, findings_created, scores = [], 0, []
    file_kind, decoder_used = _kind(targets[0]["path"]), None

    for target in targets:
        file_kind = _kind(target["path"])
        page_inputs = []
        if file_kind == "image":
            page_inputs = [{"label": "page 1", "path": target["path"],
                            "suffix": "." + target["path"].rsplit(".", 1)[-1], "local": False}]
        elif file_kind == "pdf":
            try:
                children = pod.files.list_children(target["path"]).to_dict().get("items") or []
            except Exception as e:  # noqa: BLE001
                limitations.append(f"The platform's page images for this PDF could not be listed "
                                   f"({type(e).__name__}); page-level analysis was skipped.")
                children = []
            page_children = sorted([c for c in children
                                    if re.search(r"pages/page_\d+\.(jpg|jpeg|png)$", str(c.get("path")))],
                                   key=lambda c: str(c.get("path")))
            if not page_children:
                limitations.append("This PDF has no converted page images, so it could not be analysed page by page.")
            for index, child in enumerate(page_children[:max(1, data.max_pages)], start=1):
                page_inputs.append({"label": f"page {index}", "path": child["path"],
                                    "suffix": "." + str(child["path"]).rsplit(".", 1)[-1], "local": True})
        else:
            limitations.append(
                f"Files of type '{file_kind}' cannot be decoded in this runtime (no office or presentation converter "
                "is installed). Export the slides to PDF and re-scan: PDFs are analysed page by page.")

        for page in page_inputs:
            page_limitations = []
            try:
                # child artifacts are fetched by their own full path, not the parent's
                raw = (pod.files.download_child(page["path"]) if page["local"]
                       else pod.files.download(page["path"]))
                raw = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode()
            except Exception as e:  # noqa: BLE001
                page_limitations.append(f"The page image could not be read ({type(e).__name__}).")
                raw = None

            width = height = rgb = None
            if raw:
                if page["suffix"] == ".png":
                    width, height, rgb = decode_png_native(raw)
                    if width:
                        decoder_used = "native png decoder"
                if width is None:
                    width, height, rgb = decode_with_imagemagick(raw, page["suffix"], page_limitations)
                    if width:
                        decoder_used = decoder_used or "imagemagick"
            geometry, regions, accessibility, logo_report, overlay_path = None, [], None, [], None
            if width and rgb:
                template, best = None, 0.0
                submitted_hash = target.get("ahash")
                for asset in assets:
                    if not asset.get("image_hash") or not submitted_hash:
                        continue
                    try:
                        similarity = 1 - bin(int(submitted_hash, 16) ^ int(asset["image_hash"], 16)).count("1") / 64
                    except Exception:  # noqa: BLE001
                        continue
                    if similarity > best:
                        best, template = similarity, asset
                geometry = measure(width, height, rgb, brand_colors, template)
                if template:
                    geometry["template"] = {"asset_id": template["id"], "name": template["name"],
                                            "similarity": round(best, 2),
                                            "approval_status": template.get("approval_status")}
                logo_report, logo_regions = logo_checks(geometry.get("logo_block"), template,
                                                        (template or {}).get("usage_restrictions"), page_limitations)
                regions.extend(logo_regions)
                if geometry.get("margins"):
                    tight = [edge for edge, value in geometry["margins"].items() if value < 0.03]
                    if tight:
                        regions.append({"kind": "margin", "severity": "low",
                                        "detail": f"Content reaches the {', '.join(tight)} edge(s) with less than 3% "
                                                  "clear space.", "box": geometry["bbox"]})
                for off in geometry.get("off_palette") or []:
                    regions.append({"kind": "colour", "severity": "medium",
                                    "detail": f"Colour {off['hex']} covers {round(off['share'] * 100)}% of the page and "
                                              "is outside the approved palette.", "hex": off["hex"]})
                for delta in geometry.get("template_deltas") or []:
                    regions.append({"kind": "template", "severity": "high",
                                    "detail": f"{delta['measure']} differs from the approved asset by {delta['delta']} "
                                              f"(submitted {delta['submitted']} vs {delta['approved_template']})."})
                bands = geometry.get("text_bands") or []
                failing = [b for b in bands if not b["passes_contrast"]]
                small = [b for b in bands if not b["meets_minimum_size"]]
                accessibility = {
                    "contrast_checks": len(bands), "contrast_failures": len(failing),
                    "worst_contrast": min((b["contrast_ratio"] for b in bands), default=None),
                    "minimum_required": MIN_CONTRAST, "large_text_required": MIN_CONTRAST_LARGE,
                    "size_checks": len(bands), "size_failures": len(small), "minimum_text_px": MIN_TEXT_PX,
                    "measurement_note": ("Contrast is measured between the darkest and lightest pixels inside each "
                                         "detected text band. Text height is measured in pixels at the image's own "
                                         "resolution, so the size check assumes the page is viewed at that size."),
                }
                for band in failing:
                    regions.append({"kind": "accessibility", "severity": "medium", "measure": "contrast",
                                    "detail": (f"A text band at y {band['y0']}-{band['y1']} has a contrast ratio of "
                                               f"{band['contrast_ratio']}:1 against its background, below the "
                                               f"{MIN_CONTRAST}:1 minimum.")})
                for band in small:
                    regions.append({"kind": "accessibility", "severity": "low", "measure": "text_size",
                                    "detail": (f"A text band at y {band['y0']}-{band['y1']} is only {band['height_px']} "
                                               f"px tall, below the {MIN_TEXT_PX} px legibility floor used here.")})
                try:
                    slug = org.get("slug") or "workspace"
                    safe = re.sub(r"[^a-zA-Z0-9]+", "-", target["name"])[:40]
                    page_no = re.sub(r"\D", "", page["label"]) or "1"
                    overlay_path = f"/brandguard/{slug}/creative/overlay-{safe}-p{page_no}.png"
                    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    handle.write(encode_png(width, height, build_overlay(width, height, rgb, geometry, brand_colors)))
                    handle.close()
                    pod.files.upload(handle.name, path=overlay_path,
                                     description=f"BrandGuard AI creative evidence overlay ({page['label']})")
                except Exception as e:  # noqa: BLE001
                    page_limitations.append(f"The page overlay could not be written ({type(e).__name__}).")
            else:
                page_limitations.append("No pixel measurements could be taken for this page.")

            vision, ocr_text = None, None
            dual = {"performed": False, "note": "vision inspection was disabled"}
            if data.use_vision:
                try:
                    vision = await _ask_vision(pod, page["path"], f"This is {page['label']} of {target['name']}.",
                                               title=f"Creative: {target['name']} {page['label']}")
                except Exception as e:  # noqa: BLE001
                    page_limitations.append(f"The vision inspection failed ({type(e).__name__}: {e}).")
                if vision:
                    ocr_text = (vision.get("text") or "").strip() or None
                    page_limitations.extend([str(x) for x in (vision.get("limitations") or [])])
                    dual = dual_read(vision, geometry or {}, page_limitations)
                    model_logo = vision.get("logo") or {}
                    flags = [k for k in ("stretched", "recoloured", "crowded", "low_resolution") if model_logo.get(k)]
                    if flags:
                        regions.append({"kind": "logo", "severity": "medium", "measure": "model_observation",
                                        "detail": (f"The vision model reports the logo as {', '.join(flags)}: "
                                                   f"{str(model_logo.get('clear_space_note') or model_logo.get('description') or '')[:200]}")})
                    if not ocr_text:
                        page_limitations.append("No text was found on this page.")
                else:
                    page_limitations.append("No text could be extracted: the vision model returned no observation.")
            else:
                page_limitations.append("Vision inspection was disabled, so text inside the page was not read.")

            page_findings = text_checks(ocr_text, rules, facts) if ocr_text else []
            for region in regions:
                if region["kind"] == "logo" and region.get("severity") in ("high", "medium"):
                    page_findings.append({"category": "visual", "severity": region["severity"],
                                          "title": f"Logo check: {region.get('measure', 'logo')}",
                                          "original_text": None, "rule_code": None, "rule_id": None,
                                          "explanation": region["detail"]})
                elif region["kind"] == "accessibility":
                    page_findings.append({"category": "accessibility", "severity": region["severity"],
                                          "title": f"Accessibility: {region.get('measure', 'measurement')}",
                                          "original_text": None, "rule_code": None, "rule_id": None,
                                          "explanation": region["detail"]})
            if ocr_text and not page_findings:
                page_limitations.append("The extracted text passed the terminology, disclaimer, claim and "
                                        "confidentiality checks.")

            page_score = 100.0
            for region in regions:
                page_score -= {"high": 20, "medium": 12, "low": 5}.get(region["severity"], 5)
            page_score = max(0.0, round(page_score - min(30, 10 * len(page_findings)), 1))
            scores.append(page_score)
            pages.append({"label": page["label"], "source_path": page["path"], "overlay_path": overlay_path,
                          "width": width, "height": height, "geometry": geometry, "regions": regions,
                          "accessibility": accessibility, "logo_checks": logo_report, "ocr_text": ocr_text,
                          "words": (vision or {}).get("words"), "dual_read": dual, "findings": page_findings,
                          "score": page_score, "limitations": page_limitations,
                          "status": "completed" if width else "failed"})

    overall = round(sum(scores) / len(scores), 1) if scores else None
    summary = (f"{len(pages)} page(s) analysed for {targets[0]['name']} ({file_kind}); "
               f"{findings_created} finding(s) recorded."
               + (f" Worst page score {min(scores)}." if scores else ""))
    scan_id = None
    if data.persist:
        scan = pod.table("bg_creative_scans").create({
            "organization_id": data.organization_id, "review_id": data.review_id,
            "file_path": targets[0]["path"], "file_name": targets[0]["name"], "file_kind": file_kind,
            "status": "completed" if pages else "failed", "engine": ENGINE_VERSION, "decoder": decoder_used,
            "page_count": len(pages), "analysed_pages": len(pages), "score": overall, "summary": summary,
            "ocr_engine": ocr_engine, "ocr_note": ocr_note, "limitations": limitations[:20],
            "findings_count": findings_created, "created_by": email})
        scan_id = scan["id"]
        for page in pages:
            pod.table("bg_creative_pages").create({
                "scan_id": scan_id, "organization_id": data.organization_id, "review_id": data.review_id,
                "page_number": int(re.sub(r"\D", "", page["label"]) or 1), "source_path": page["source_path"],
                "overlay_path": page["overlay_path"], "width": page["width"], "height": page["height"],
                "text": page["ocr_text"], "words": page["words"], "dual_read": page["dual_read"],
                "palette": (page["geometry"] or {}).get("palette"), "geometry": page["geometry"],
                "accessibility": page["accessibility"], "logo_checks": page["logo_checks"],
                "regions": page["regions"], "findings_count": len(page["findings"]),
                "limitations": page["limitations"], "status": page["status"]})
            if data.review_id:
                for finding in page["findings"]:
                    pod.table("bg_findings").create({
                        "organization_id": data.organization_id, "review_id": data.review_id,
                        "version": (review or {}).get("current_version"),
                        "category": finding["category"], "title": finding["title"][:300],
                        "description": (f"Found on {page['label']} of “{targets[0]['name']}” "
                                        f"({page['width']}×{page['height']} px)."),
                        "severity": finding["severity"], "confidence": 0.8,
                        "evidence_status": "supported" if finding.get("rule_id") else "not_found",
                        "original_text": finding.get("original_text"),
                        "location_excerpt": f"{page['label']}: {str(finding.get('original_text'))[:120]}",
                        "rule_id": finding.get("rule_id"), "rule_code": finding.get("rule_code"),
                        "source_reference": {"creative_scan_id": scan_id, "page": page["label"],
                                             "image_path": page["source_path"], "overlay_path": page["overlay_path"],
                                             "extraction": ocr_engine,
                                             "dual_read_agreement": page["dual_read"].get("text_agreement"),
                                             "note": finding.get("explanation")},
                        "explanation": finding.get("explanation"),
                        "suggested_correction": finding.get("suggested_correction"),
                        "review_status": "open", "detection_method": "visual",
                        "fingerprint": (f"{finding['category']}|{str(finding.get('original_text'))[:50].lower()}"
                                        f"|creative:{page['label']}")})
                    findings_created += 1
        pod.table("bg_audit_logs").create({
            "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
            "actor_name": member.get("display_name"), "action": "creative.scanned",
            "subject_type": "bg_creative_scans", "subject_id": scan_id,
            "summary": (f"Creative scan of {targets[0]['name']} ({file_kind}): {len(pages)} page(s), "
                        f"{findings_created} finding(s), score {overall}"),
            "metadata": {"engine": ENGINE_VERSION, "decoder": decoder_used, "limitations": limitations[:10]}})

    return BgCreativeScanResult(
        scan_id=scan_id, file_kind=file_kind, decoder=decoder_used, page_count=len(pages),
        analysed_pages=len(pages), score=overall, ocr_engine=ocr_engine, ocr_note=ocr_note,
        pages=[{k: v for k, v in page.items() if k not in ("words", "geometry")}
               | {"word_count": len(page["words"] or []),
                  "measured": {kk: vv for kk, vv in (page["geometry"] or {}).items()
                               if kk in ("palette_conformance", "margins", "text_bands", "logo_block", "size_px")}}
               for page in pages],
        findings_created=findings_created, limitations=limitations, message=summary)

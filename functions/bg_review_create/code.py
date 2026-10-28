#input_type_name: BgReviewCreateInput
#output_type_name: BgReviewCreateResult
#function_name: bg_review_create

"""Create a content review: validate, extract attached documents, preserve the
original submission, store version 1 and its segments, and record the submission."""
import re
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

CONTENT_TYPES = ["social_post", "marketing_email", "advertisement", "blog_post", "product_description",
                 "landing_page", "promotional_banner", "product_brochure", "marketing_document", "other"]
TEXT_EXT = (".txt", ".md", ".markdown", ".csv")
DOC_EXT = (".pdf", ".docx", ".doc")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg")
MAX_TEXT_CHARS = 200000


class Attachment(BaseModel):
    path: str
    name: str | None = None
    kind: str | None = None


class ImageMeta(BaseModel):
    path: str
    name: str | None = None
    width: int | None = None
    height: int | None = None
    ahash: str | None = None
    colors: list[str] | None = None


class BgReviewCreateInput(BaseModel):
    organization_id: str
    title: str
    content_type: str
    campaign: str | None = None
    audience: str | None = None
    channel: str | None = None
    language: str | None = "en"
    context_notes: str | None = None
    content_text: str | None = None
    attachments: list[Attachment] = []
    images: list[ImageMeta] = []
    submit: bool = True


class BgReviewCreateResult(BaseModel):
    review_id: str
    status: str
    version: int
    extracted_documents: list[dict]
    warnings: list[str]
    word_count: int
    message: str


def _now():
    return datetime.now(timezone.utc).isoformat()


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def _extract(pod, path, warnings):
    """Return (text, status, note, page_count). Uses the platform's converted markdown."""
    name = path.rsplit("/", 1)[-1]
    lower = name.lower()
    try:
        if lower.endswith(TEXT_EXT):
            raw = pod.files.download(path)
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            return text[:MAX_TEXT_CHARS], "extracted", f"Read {len(text)} characters from the text file.", None
        if lower.endswith(DOC_EXT) or lower.endswith(".pdf"):
            md = pod.files.download_markdown(path)
            text = md.decode("utf-8", errors="replace") if isinstance(md, (bytes, bytearray)) else str(md)
            pages = len(re.findall(r"<!--\s*Page\s+\d+", text)) or None
            if not text.strip():
                return "", "no_text", ("The document was converted but no text was found. It may be a scan; "
                                       "paste the relevant text instead."), pages
            note = f"Extracted {len(text)} characters of page-marked text."
            if pages:
                note += f" {pages} page(s) detected."
            return text[:MAX_TEXT_CHARS], "extracted", note, pages
        if lower.endswith(".csv"):
            raw = pod.files.download(path)
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            return text[:MAX_TEXT_CHARS], "partial", "CSV read as plain text; cell structure is not analysed.", None
        if lower.endswith(IMG_EXT):
            return "", "no_text", ("Images are not converted to text by this pipeline. Text embedded in an image "
                                   "was NOT checked (no OCR in this runtime)."), None
        warnings.append(f"Unsupported file type for text extraction: {name}")
        return "", "failed", "Unsupported file type.", None
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Could not extract text from {name}: {type(e).__name__}")
        return "", "failed", f"Extraction failed: {type(e).__name__}: {e}", None


def _segments(text):
    out, pos = [], 0
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            pos += len(block) + 2
            continue
        start = text.find(block, pos)
        if start < 0:
            start = pos
        kind = "heading" if re.match(r"^\s*(#{1,6}\s|\d+\.\s+[A-Z])", block) else "text"
        out.append({"seq": len(out) + 1, "kind": kind, "content": block.strip(),
                    "start_offset": start, "end_offset": start + len(block)})
        pos = start + len(block)
    return out[:400]


def bg_review_create(ctx: FunctionContext, data: BgReviewCreateInput) -> BgReviewCreateResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    member = _member(pod, data.organization_id, email)
    if not member:
        raise PermissionError("You are not a member of this workspace.")
    if not data.title or not data.title.strip():
        raise ValueError("A content title is required.")
    if data.content_type not in CONTENT_TYPES:
        raise ValueError("Unknown content type.")

    warnings: list[str] = []
    pieces = []
    if data.content_text and data.content_text.strip():
        pieces.append(data.content_text.strip())
    if len(pieces and pieces[0] or "") > MAX_TEXT_CHARS:
        raise ValueError("Submitted text is too large for one review; split it into smaller pieces.")

    extracted = []
    for att in data.attachments:
        path = att.path
        if not path.startswith("/"):
            raise ValueError("Attachment paths must be pod paths such as /brandguard/uploads/file.pdf")
        if ".." in path:
            raise ValueError("Invalid attachment path.")
        name = att.name or path.rsplit("/", 1)[-1]
        text, status, note, pages = _extract(pod, path, warnings)
        doc = pod.table("bg_documents").create({
            "organization_id": data.organization_id, "title": name,
            "kind": "content_file", "file_path": path,
            "mime_type": None, "size_bytes": None, "page_count": pages,
            "extracted_text": text or None, "extraction_status": status, "extraction_note": note,
            "uploaded_by": ctx.user_id, "uploaded_by_email": email,
        })
        extracted.append({"document_id": doc["id"], "name": name, "path": path,
                          "status": status, "note": note, "characters": len(text)})
        if text:
            pieces.append(f"--- Attached document: {name} ---\n{text}")
        if status in ("no_text", "failed"):
            warnings.append(f"{name}: {note}")

    content = "\n\n".join(pieces).strip()
    if not content:
        raise ValueError("No content to review. Paste the text or upload a document with extractable text.")

    images = []
    for img in data.images:
        images.append({"path": img.path, "name": img.name or img.path.rsplit("/", 1)[-1],
                       "width": img.width, "height": img.height, "ahash": img.ahash,
                       "colors": img.colors or []})

    status = "submitted" if data.submit else "draft"
    review = pod.table("bg_reviews").create({
        "organization_id": data.organization_id,
        "title": data.title.strip()[:300],
        "content_type": data.content_type,
        "campaign": data.campaign, "audience": data.audience, "channel": data.channel,
        "language": data.language or "en", "context_notes": data.context_notes,
        "source_text": content, "current_text": content,
        "status": status, "current_version": 1,
        "created_by": ctx.user_id, "created_by_email": email,
        "created_by_name": (member or {}).get("display_name"),
        "analysis_status": "queued",
        "submitted_at": _now() if data.submit else None,
        "attachment_paths": [a.path for a in data.attachments],
        "image_paths": images,
    })
    pod.table("bg_review_versions").create({
        "review_id": review["id"], "version": 1, "content": content,
        "change_summary": "Original submission", "change_source": "original",
        "author_id": ctx.user_id, "author_email": email, "author_name": (member or {}).get("display_name"),
    })
    segs = _segments(content)
    if segs:
        pod.records.bulk_create("bg_segments", [{**s, "review_id": review["id"]} for s in segs])

    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
        "actor_name": (member or {}).get("display_name"), "action": "review.created",
        "subject_type": "bg_reviews", "subject_id": review["id"],
        "summary": f"Review '{review['title']}' created as v1 ({status})",
        "metadata": {"content_type": data.content_type, "attachments": len(extracted),
                     "images": len(images), "warnings": warnings}})

    return BgReviewCreateResult(
        review_id=review["id"], status=status, version=1, extracted_documents=extracted,
        warnings=warnings, word_count=len(content.split()),
        message=("Content saved and submitted for review." if data.submit else "Content saved as a draft."))

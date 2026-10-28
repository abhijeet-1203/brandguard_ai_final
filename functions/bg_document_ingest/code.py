#input_type_name: BgDocumentIngestInput
#output_type_name: BgDocumentIngestResult
#function_name: bg_document_ingest

"""Register an uploaded pod file as a BrandGuard document and extract its text.

The platform converts PDF/DOCX uploads to page-marked markdown; that converted text is
what this function stores. A file that cannot be read is recorded honestly as failed or
having no text - never as a successful extraction."""
import re
from datetime import datetime, timezone

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

KINDS = ["guideline_source", "fact_source", "evidence", "content_file", "asset_file", "policy"]
TEXT_EXT = (".txt", ".md", ".markdown", ".csv")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg")


class BgDocumentIngestInput(BaseModel):
    organization_id: str
    title: str
    kind: str
    file_path: str
    mime_type: str | None = None
    size_bytes: int | None = None


class BgDocumentIngestResult(BaseModel):
    document_id: str
    extraction_status: str
    extraction_note: str | None = None
    characters: int
    preview: str | None = None
    message: str


def _member(pod, org_id, email):
    rows = pod.records.list("bg_memberships", limit=5, filter=[
        {"field": "organization_id", "op": "eq", "value": org_id},
        {"field": "user_email", "op": "eq", "value": email}]).to_dict()["items"]
    return rows[0] if rows else None


def bg_document_ingest(ctx: FunctionContext, data: BgDocumentIngestInput) -> BgDocumentIngestResult:
    pod = ctx.pod
    email = ctx.user_email or ""
    if not _member(pod, data.organization_id, email):
        raise PermissionError("You are not a member of this workspace.")
    if data.kind not in KINDS:
        raise ValueError("Unknown document kind.")
    path = data.file_path or ""
    if not path.startswith("/") or ".." in path:
        raise ValueError("Invalid file path.")

    name = path.rsplit("/", 1)[-1].lower()
    text, status, note, pages = "", "pending", None, None
    try:
        if name.endswith(TEXT_EXT):
            raw = pod.files.download(path)
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            status, note = "extracted", f"Read {len(text)} characters."
        elif name.endswith(IMG_EXT):
            status = "no_text"
            note = "Image file: no text extraction (this runtime has no OCR)."
        else:
            md = pod.files.download_markdown(path)
            text = md.decode("utf-8", errors="replace") if isinstance(md, (bytes, bytearray)) else str(md)
            pages = len(re.findall(r"<!--\s*Page\s+\d+", text)) or None
            if text.strip():
                status = "extracted"
                note = f"Extracted {len(text)} characters of converted text" + (f" across {pages} page(s)." if pages else ".")
            else:
                status = "no_text"
                note = "No text found in the converted document (it may be a scan)."
    except Exception as e:  # noqa: BLE001
        status, note = "failed", f"Extraction failed: {type(e).__name__}: {e}"

    doc = pod.table("bg_documents").create({
        "organization_id": data.organization_id, "title": data.title, "kind": data.kind,
        "file_path": path, "mime_type": data.mime_type, "size_bytes": data.size_bytes,
        "page_count": pages, "extracted_text": text or None,
        "extraction_status": status, "extraction_note": note,
        "uploaded_by": ctx.user_id, "uploaded_by_email": email,
    })
    pod.table("bg_audit_logs").create({
        "organization_id": data.organization_id, "actor_id": ctx.user_id, "actor_email": email,
        "action": "document.ingested", "subject_type": "bg_documents", "subject_id": doc["id"],
        "summary": f"{data.kind} document '{data.title}' registered ({status})",
        "metadata": {"path": path, "status": status, "note": note}})
    return BgDocumentIngestResult(
        document_id=doc["id"], extraction_status=status, extraction_note=note, characters=len(text),
        preview=(text[:600] if text else None),
        message={"extracted": "Document text extracted and stored.",
                 "partial": "Document partially extracted - see the note.",
                 "no_text": "No text could be extracted from this document.",
                 "failed": "Document processing failed. Try another supported format or paste the text."}[status])

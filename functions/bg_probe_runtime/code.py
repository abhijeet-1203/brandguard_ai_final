#input_type_name: BgProbeInput
#output_type_name: BgProbeResult
#function_name: bg_probe_runtime
"""Diagnostic: what image, OCR and PDF capabilities does the function runtime actually have?"""
import importlib, os, shutil, subprocess, sys
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod


class BgProbeInput(BaseModel):
    pass


class BgProbeResult(BaseModel):
    modules: dict
    binaries: dict
    python: str
    notes: list[str]


def bg_probe_runtime(ctx: FunctionContext, data: BgProbeInput) -> BgProbeResult:
    modules, notes = {}, []
    for name in ("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "numpy", "cv2", "pytesseract",
                 "fitz", "pypdf", "pdf2image", "docx", "pandas", "sklearn", "requests", "zlib",
                 "struct", "base64", "hashlib", "hmac"):
        try:
            m = importlib.import_module(name)
            modules[name] = getattr(m, "__version__", "present")
        except Exception as e:  # noqa: BLE001
            modules[name] = f"MISSING ({type(e).__name__})"
    binaries = {}
    for binary in ("tesseract", "pdftoppm", "pdfimages", "gs", "convert", "libreoffice", "soffice"):
        path = shutil.which(binary)
        binaries[binary] = path or "not found"
    if modules.get("PIL", "").startswith("MISSING") is False:
        try:
            from PIL import features
            notes.append(f"Pillow features: jpeg={features.check('jpg')} webp={features.check('webp')} "
                         f"webp_anim={features.check('webp_anim')} zlib={features.check('zlib')}")
            notes.append(f"Pillow registered extensions: {sorted(features.get_supported())[:20]}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Pillow feature check failed: {e}")
    if "not found" not in binaries.get("tesseract", "not found"):
        try:
            out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=20)
            notes.append("tesseract: " + (out.stdout or out.stderr).splitlines()[0])
        except Exception as e:  # noqa: BLE001
            notes.append(f"tesseract version call failed: {e}")
    notes.append(f"python {sys.version.split()[0]}; cwd={os.getcwd()}; writable_tmp={os.access('/tmp', os.W_OK)}")
    return BgProbeResult(modules=modules, binaries=binaries, python=sys.version.split()[0], notes=notes)

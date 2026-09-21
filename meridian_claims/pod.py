"""Extract text from POD PDFs; fall back to vision for image-only scans."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time

from pypdf import PdfReader

from meridian_claims.models import PodResult

TEXT_MIN_CHARS = 20
TEXT_MIN_WORDS = 5
# Among non-whitespace characters
MIN_ALPHA_RATIO = 0.45
MAX_NON_ALNUM_RATIO = 0.35
# Sample POD_scan_*.pdf pages are white+gray fills with no text/images.
BLANK_MAX_UNIQUE_COLORS = 4

_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_POD_ID_RE = re.compile(
    r"\b(?:MF-\d{5}|PO-\d{7}|BOL\d{6,}|PRO\d{7,})\b",
    re.IGNORECASE,
)

# Common POD / shipping vocabulary (lowercase). Presence is a soft corroboration
# that extracted text is about a delivery document, not random OCR noise.
POD_SHIPPING_TERMS = frozenset(
    {
        "pod",
        "proof",
        "delivery",
        "delivered",
        "bol",
        "load",
        "bill",
        "lading",
        "receiver",
        "consignee",
        "shipper",
        "carrier",
        "exception",
        "exceptions",
        "seal",
        "seals",
        "intact",
        "signature",
        "signed",
        "condition",
        "damaged",
        "damage",
        "shortage",
        "freight",
        "pickup",
        "destination",
        "origin",
        "pieces",
        "weight",
        "driver",
        "trailer",
        "refused",
        "refusal",
        "os&d",
        "osd",
    }
)


def extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid or unreadable PDF: {exc}") from exc
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def assess_pod_text_quality(text: str) -> tuple[bool, list[str]]:
    """
    Lightweight deterministic check that extracted POD text is usable.

    Returns (ok, failure_reasons). Does not invent POD facts — only gates
    whether the text layer may be treated as readable evidence.
    """
    reasons: list[str] = []
    cleaned = (text or "").strip()
    if len(cleaned) < TEXT_MIN_CHARS:
        return False, [f"too_short({len(cleaned)}<{TEXT_MIN_CHARS})"]

    words = _WORD_RE.findall(cleaned)
    if len(words) < TEXT_MIN_WORDS:
        reasons.append(f"insufficient_words({len(words)}<{TEXT_MIN_WORDS})")

    non_ws = [c for c in cleaned if not c.isspace()]
    if not non_ws:
        return False, ["empty_after_strip"]

    alpha = sum(1 for c in non_ws if c.isalpha())
    alnum = sum(1 for c in non_ws if c.isalnum())
    alpha_ratio = alpha / len(non_ws)
    non_alnum_ratio = 1.0 - (alnum / len(non_ws))

    if alpha_ratio < MIN_ALPHA_RATIO:
        reasons.append(f"low_alpha_ratio({alpha_ratio:.2f}<{MIN_ALPHA_RATIO})")
    if non_alnum_ratio > MAX_NON_ALNUM_RATIO:
        reasons.append(
            f"excessive_non_alnum({non_alnum_ratio:.2f}>{MAX_NON_ALNUM_RATIO})"
        )

    lowered = {w.lower() for w in words}
    term_hits = lowered & POD_SHIPPING_TERMS
    has_id = bool(_POD_ID_RE.search(cleaned))
    if not term_hits and not has_id:
        reasons.append("no_pod_shipping_terms_or_ids")

    return (len(reasons) == 0), reasons


def render_pdf_page_png(data: bytes, page_index: int = 0) -> bytes:
    """Rasterize first page for vision. Uses PyMuPDF."""
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if doc.page_count < 1:
            raise ValueError("PDF has no pages")
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        return pix.tobytes("png")
    finally:
        doc.close()


def is_blank_scan(data: bytes) -> bool:
    """
    True when a no-text PDF looks like a blank/placeholder scan.

    Uses page structure (no images, few fills) and a tiny unique-color count on
    the raster — enough to catch the sample gray-rectangle POD scans without
    sending them to vision.
    """
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if doc.page_count < 1:
            return True
        page = doc.load_page(0)
        if page.get_images():
            return False
        drawings = page.get_drawings()
        if len(drawings) <= 1:
            # Single rect fill / empty page with no embedded image content.
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1))
            samples = pix.samples
            if not samples:
                return True
            n = pix.n  # components per pixel
            step = max(n, (len(samples) // 3000) * n)
            colors: set[tuple[int, ...]] = set()
            for i in range(0, len(samples) - n + 1, step):
                colors.add(tuple(samples[i : i + n]))
                if len(colors) > BLANK_MAX_UNIQUE_COLORS:
                    return False
            return len(colors) <= BLANK_MAX_UNIQUE_COLORS
        return False
    finally:
        doc.close()


def process_pod_attachment(
    filename: str,
    data: bytes,
    *,
    use_vision: bool = True,
) -> PodResult:
    """
    Process a POD PDF.

    - Usable text layer (length + quality) → mode=text / readable
    - Long but low-quality / garbage OCR → unreadable (no fact inference)
    - No/short text + blank raster → mode=blank_page / unreadable (no model call)
    - No text + content → try Anthropic vision once (if allowed)
    - Vision finds nothing usable → mode=unreadable (honest failure)
    - Corrupt/empty attachment → invalid
    """
    if not filename:
        return PodResult(
            filename=None,
            mode="invalid",
            excerpt="",
            findings="Attachment has no filename.",
            status="invalid",
        )
    if not data:
        return PodResult(
            filename=filename,
            mode="invalid",
            excerpt="",
            findings="Attachment is empty (0 bytes).",
            status="invalid",
        )
    if not filename.lower().endswith(".pdf"):
        return PodResult(
            filename=filename,
            mode="invalid",
            excerpt="",
            findings="Attachment is not a PDF; coordinator should open manually.",
            status="invalid",
        )

    try:
        text = extract_pdf_text(data)
    except ValueError as exc:
        return PodResult(
            filename=filename,
            mode="invalid",
            excerpt="",
            findings=str(exc),
            status="invalid",
        )

    if text:
        ok, quality_reasons = assess_pod_text_quality(text)
        if ok:
            return PodResult(
                filename=filename,
                mode="text",
                excerpt=text[:1500],
                findings="Text layer extracted from POD PDF (quality check passed).",
                status="readable",
            )
        # Enough characters to look like a text layer, but quality failed → do not
        # treat as evidence and do not invent POD facts from garbage OCR.
        if len(text.strip()) >= TEXT_MIN_CHARS:
            return PodResult(
                filename=filename,
                mode="unreadable",
                excerpt="",
                findings=(
                    "Extracted POD text failed quality check "
                    f"({'; '.join(quality_reasons)}). "
                    "Treated as unreadable; coordinator must open the attachment."
                ),
                status="unreadable",
            )
        # Short/sparse text → fall through to blank-scan / vision path.

    # No usable text layer — check blank/placeholder scan before vision (no PII image upload).
    try:
        if is_blank_scan(data):
            return PodResult(
                filename=filename,
                mode="blank_page",
                excerpt="",
                findings=(
                    "No text layer; page looks blank/placeholder (few fills, no images). "
                    "Vision skipped. Coordinator must open the attachment."
                ),
                status="unreadable",
            )
    except Exception as exc:  # noqa: BLE001
        return PodResult(
            filename=filename,
            mode="unreadable",
            excerpt="",
            findings=f"No text layer; blank-scan check failed ({exc}).",
            status="unreadable",
        )

    if not use_vision:
        return PodResult(
            filename=filename,
            mode="unreadable",
            excerpt="",
            findings="No text layer; vision disabled for this run.",
            status="unreadable",
        )

    # Fail closed: image pixels cannot be safely redacted. Opt-in only after DPA.
    from meridian_claims.pii_gate import vision_allowed, vision_block_report

    if not vision_allowed():
        report = vision_block_report()
        return PodResult(
            filename=filename,
            mode="vision_blocked",
            excerpt="",
            findings=report.notes[0] if report.notes else "Vision blocked pending DPA.",
            status="unreadable",
        )

    from meridian_claims.agent import resolve_api_key

    api_key = resolve_api_key()
    if not api_key:
        return PodResult(
            filename=filename,
            mode="unreadable",
            excerpt="",
            findings="No text layer and ANTHROPIC_API_KEY missing for vision.",
            status="unreadable",
        )

    try:
        png = render_pdf_page_png(data)
        findings = _vision_pod(api_key, png)
        usable = findings.get("usable", False)
        summary = findings.get("summary", "")
        if usable and summary:
            return PodResult(
                filename=filename,
                mode="vision",
                excerpt=summary[:1500],
                findings=summary,
                status="readable",
            )
        return PodResult(
            filename=filename,
            mode="unreadable",
            excerpt="",
            findings=summary
            or "Scanned/image POD has no usable content after vision review.",
            status="unreadable",
        )
    except Exception as exc:  # noqa: BLE001 — surface failure mode to coordinator
        return PodResult(
            filename=filename,
            mode="unreadable",
            excerpt="",
            findings=f"POD vision/OCR failed: {exc}",
            status="unreadable",
        )


def select_pod_attachment(
    attachments: list,
) -> tuple[str, bytes] | None:
    """
    Backward-compatible helper: return a single POD (name, bytes) when unambiguous.

    Prefer ``decide_pod_attachments`` for auditable inventory. Returns None when
    there is no POD or when multiple POD candidates would require human choice.
    """
    decision = decide_pod_attachments(attachments)
    if decision.get("selection_status") != "selected":
        return None
    selected = decision.get("selected") or {}
    name = selected.get("filename")
    data = selected.get("data")
    if not name or data is None:
        return None
    return name, data


def decide_pod_attachments(attachments: list) -> dict:
    """
    Inventory every attachment and pick a POD candidate deterministically.

    Rules (no LLM, no invoice/document processing):
    - Inventory all named attachments.
    - Strong POD candidates: PDFs whose filename contains ``pod``.
    - If exactly one strong POD candidate → select it; other files = present_not_processed.
    - If multiple strong POD candidates → do not select; escalate.
    - If no strong POD candidate and exactly one PDF → select that PDF (legacy fallback).
    - If no strong POD candidate and multiple PDFs → do not select; escalate.
    - If no PDFs → no_pod.
    """
    inventory: list[dict] = []
    pdfs: list[tuple[str, bytes, str]] = []  # filename, data, content_type

    for a in attachments or []:
        name = getattr(a, "filename", None) or ""
        if not name:
            continue
        ctype = getattr(a, "content_type", None) or ""
        data = getattr(a, "data", b"") or b""
        is_pdf = name.lower().endswith(".pdf")
        strong_pod = is_pdf and "pod" in name.lower()
        if is_pdf:
            kind = "pod_candidate" if strong_pod else "pdf"
            pdfs.append((name, data, ctype))
        else:
            kind = "other"
        inventory.append(
            {
                "filename": name,
                "content_type": ctype,
                "size_bytes": len(data),
                "kind": kind,
                "is_pdf": is_pdf,
                "strong_pod_candidate": strong_pod,
            }
        )

    strong = [row for row in inventory if row.get("strong_pod_candidate")]
    pdf_rows = [row for row in inventory if row.get("is_pdf")]

    def _unprocessed(exclude: str | None) -> list[dict]:
        out = []
        for row in inventory:
            if exclude and row["filename"] == exclude:
                continue
            out.append(
                {
                    "filename": row["filename"],
                    "kind": row["kind"],
                    "status": "present_not_processed",
                    "note": "Inventoried only; not opened for claims POD extraction.",
                }
            )
        return out

    if len(strong) > 1:
        names = [r["filename"] for r in strong]
        return {
            "inventory": inventory,
            "pod_candidates": names,
            "selected": None,
            "selection_status": "ambiguous_multiple_pods",
            "unprocessed": _unprocessed(None),
            "reason": (
                f"Multiple POD candidates ({', '.join(names)}); "
                "none auto-selected — human must choose."
            ),
        }

    if len(strong) == 1:
        chosen = strong[0]["filename"]
        data = next(d for n, d, _ in pdfs if n == chosen)
        return {
            "inventory": inventory,
            "pod_candidates": [chosen],
            "selected": {
                "filename": chosen,
                "data": data,
                "content_type": strong[0].get("content_type") or "application/pdf",
            },
            "selection_status": "selected",
            "unprocessed": _unprocessed(chosen),
            "reason": f"Selected sole POD-named PDF: {chosen}",
        }

    if len(pdf_rows) > 1:
        names = [r["filename"] for r in pdf_rows]
        return {
            "inventory": inventory,
            "pod_candidates": names,
            "selected": None,
            "selection_status": "ambiguous_multiple_pods",
            "unprocessed": _unprocessed(None),
            "reason": (
                f"Multiple PDFs without a unique POD-named file ({', '.join(names)}); "
                "none auto-selected — human must choose."
            ),
        }

    if len(pdf_rows) == 1:
        chosen = pdf_rows[0]["filename"]
        data = next(d for n, d, _ in pdfs if n == chosen)
        return {
            "inventory": inventory,
            "pod_candidates": [chosen],
            "selected": {
                "filename": chosen,
                "data": data,
                "content_type": pdf_rows[0].get("content_type") or "application/pdf",
            },
            "selection_status": "selected",
            "unprocessed": _unprocessed(chosen),
            "reason": f"Selected sole PDF attachment (no POD-named file): {chosen}",
        }

    return {
        "inventory": inventory,
        "pod_candidates": [],
        "selected": None,
        "selection_status": "no_pod",
        "unprocessed": _unprocessed(None),
        "reason": "No PDF/POD attachment on the email.",
    }


def _vision_pod(api_key: str, png_bytes: bytes) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    prompt = (
        "You are reviewing a proof-of-delivery (POD) page image for a freight broker.\n"
        "Return ONLY JSON with keys:\n"
        '  usable: boolean — true if you can read load/BOL/PO/condition/signatures\n'
        "  summary: string — delivery condition, exceptions, identifiers only. "
        "Do NOT include receiver/signer personal names or phone numbers; "
        "replace any with [REDACTED].\n"
        "  exceptions_noted: boolean | null\n"
        "If the page is blank, gray, or illegible, set usable=false."
    )
    t0 = time.perf_counter()
    resp = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    _ = time.perf_counter() - t0
    text = "".join(block.text for block in resp.content if block.type == "text")
    return _parse_json_object(text)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        return {"usable": False, "summary": text[:500], "exceptions_noted": None}

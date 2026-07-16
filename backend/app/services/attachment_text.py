"""Local text extraction for trip attachments."""

from __future__ import annotations

import base64
import io
import re
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader


def extract(
    *,
    name: str,
    content: str,
    media_type: str,
    encoding: str = "utf-8",
) -> tuple[str, dict]:
    if encoding != "data-url":
        return content, {"extraction": "plain_text"}
    raw = _decode_data_url(content)
    lower = name.casefold()
    if media_type == "application/pdf" or lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return _clean(text), {"extraction": "pdf", "pages": len(reader.pages)}
    if lower.endswith(".docx") or "wordprocessingml" in media_type:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        return _clean(text), {"extraction": "docx"}
    raise ValueError(f"{name} is not a supported offline text document")


def _decode_data_url(value: str) -> bytes:
    match = re.match(r"^data:[^;,]+;base64,(.+)$", value, re.DOTALL)
    if not match:
        raise ValueError("Attachment was not a valid data URL")
    return base64.b64decode(match.group(1), validate=True)


def _clean(value: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", value).strip()

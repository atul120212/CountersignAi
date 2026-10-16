"""PDF text layer helpers used to verify that extraction quotes really exist on the cited page."""
from __future__ import annotations

import io
import re

from pypdf import PdfReader

_WS = re.compile(r"[^a-z0-9]+")


def norm(s: str) -> str:
    s = (s or "").lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", s).strip()


def page_texts(pdf: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf))
    out = []
    for p in reader.pages:
        try:
            out.append(p.extract_text() or "")
        except Exception:
            out.append("")
    return out


def _contains(page_norm: str, quote_norm: str) -> bool:
    if not quote_norm:
        return False
    if quote_norm in page_norm:
        return True
    words = quote_norm.split()
    if len(words) < 6:
        return False
    shingles = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    hits = sum(1 for s in shingles if s in page_norm)
    return hits / len(shingles) >= 0.8


def locate_quote(pages_norm: list[str], quote: str, page_hint: int | None) -> tuple[bool, int | None]:
    """Return (verified, page). Checks the cited page first, then every other page."""
    q = norm(quote)
    order = []
    if page_hint and 1 <= page_hint <= len(pages_norm):
        order.append(page_hint)
    order += [i for i in range(1, len(pages_norm) + 1) if i != page_hint]
    for i in order:
        if _contains(pages_norm[i - 1], q):
            return True, i
    return False, page_hint

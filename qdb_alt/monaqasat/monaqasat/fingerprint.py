"""Change detection that ignores markup churn.

Monaqasat embeds a Dynatrace RUM script whose attributes differ on every
response, so hashing raw bytes reports a change on every fetch. That would
overwrite `fetched_at` and re-parse pages that have not actually moved.

``canonical_html_sha256`` strips scripts, styles, comments and whitespace
before hashing. That is the key stored on `raw_page`.
"""

from __future__ import annotations

import hashlib
import re

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def canonical_html(text: str) -> str:
    stripped = _SCRIPT_OR_STYLE.sub("", text)
    stripped = _COMMENT.sub("", stripped)
    return _WHITESPACE.sub(" ", stripped).strip()


def canonical_html_sha256(text: str) -> str:
    return hashlib.sha256(canonical_html(text).encode("utf-8")).hexdigest()

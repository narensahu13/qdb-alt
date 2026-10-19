"""Downloading the directory PDFs from qdb.qa.

Each PDF is ~125-135 MB and published once (May 2024), so it is downloaded
once into the data folder and re-read from there. A download is written to a
.part file and renamed only when it is complete and starts like a PDF, so an
interrupted run or an HTML error page never passes for the directory.
"""

from __future__ import annotations

from pathlib import Path

import requests

BASE = "https://www.qdb.qa/-/media/qdbapp/other-documents/"
EDITIONS = {
    "manufacturing": BASE + "smed_2024_manufacturing_for-digital-version_6-may.pdf",
    "services": BASE + "smed_2024_services_for-digital-version_6-may.pdf",
}
UA = {"User-Agent": "qdb-alt/qdb_sme_directory (+https://github.com/narensahu13/qdb-alt)"}


def use_system_trust_store() -> None:
    """Trust the Windows certificate store (a QDB proxy re-signs TLS); see icv/README."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def download(edition: str, folder: Path, session=None, timeout: float = 600) -> Path:
    """The edition's PDF in `folder`, downloading it if it is not there yet."""
    url = EDITIONS[edition]
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / f"qdb_sme_directory_2024_{edition}.pdf"
    if pdf.exists():
        return pdf
    part = pdf.with_suffix(".part")
    http = session or requests
    with http.get(url, headers=UA, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    with open(part, "rb") as f:
        if f.read(5) != b"%PDF-":
            part.unlink()
            raise ValueError(f"{url}: the response is not a PDF")
    part.replace(pdf)
    return pdf

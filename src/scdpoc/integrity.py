"""Integrity checks over an issued envelope, and tamper simulations (AT-04, AT-10).

The checks compare an envelope against the facts recorded at issuance:
outer SHA-256, XDS repository hash/size, embedded payload digest, byte
identity with the exchange projection, document identity, and a rendition
check that every clinical fact derived from the embedded IPS is visible on
the PDF pages. Any single failure makes the envelope untrustworthy.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass

import pikepdf
from pikepdf import Name

from .ips.view import build_view
from .pdfa.extract import extract_ips, page_text


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha1(b: bytes) -> str:
    """XDS.b repository 'hash' slot uses SHA-1 (ITI TF-3). Shown alongside SHA-256."""
    return hashlib.sha1(b).hexdigest()


@dataclass
class IntegrityCheck:
    id: str
    label: str
    passed: bool
    expected: str = ""
    actual: str = ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def verify_envelope(pdf_bytes: bytes, *, document_urn: str, pdf_sha256: str, ips_sha256: str,
                    xds_hash: str, size: int, projection: bytes | None = None) -> list[IntegrityCheck]:
    checks: list[IntegrityCheck] = []
    actual = sha256(pdf_bytes)
    checks.append(IntegrityCheck("IC-1", "Envelope SHA-256 matches issuance record", actual == pdf_sha256,
                                 pdf_sha256, actual))
    a1 = sha1(pdf_bytes)
    checks.append(IntegrityCheck("IC-2", "XDS repository hash (SHA-1) and size match",
                                 a1 == xds_hash and len(pdf_bytes) == size,
                                 f"{xds_hash} / {size} bytes", f"{a1} / {len(pdf_bytes)} bytes"))
    try:
        emb = extract_ips(pdf_bytes)
    except Exception as exc:
        checks.append(IntegrityCheck("IC-3", "Embedded FHIR IPS present (AFRelationship=Source)", False,
                                     "present", str(exc)))
        return checks
    checks.append(IntegrityCheck("IC-3", "Embedded FHIR IPS present (AFRelationship=Source)", True,
                                 "present", emb.filename))
    checks.append(IntegrityCheck("IC-4", "Embedded IPS SHA-256 matches issuance record",
                                 emb.sha256 == ips_sha256, ips_sha256, emb.sha256))
    if projection is not None:
        checks.append(IntegrityCheck("IC-5", "Embedded IPS is byte-identical to the exchange projection",
                                     emb.data == projection, sha256(projection), emb.sha256))
    try:
        bundle = json.loads(emb.data)
        doc_id = bundle.get("identifier", {}).get("value", "")
        checks.append(IntegrityCheck("IC-6", "Embedded IPS document identifier matches registry entry",
                                     doc_id == document_urn, document_urn, doc_id))
        view = build_view(bundle)
        text = _norm(page_text(pdf_bytes))
        missing = [f for f in view.visible_facts() if _norm(f) not in text]
        checks.append(IntegrityCheck("IC-7", "Every clinical fact in the embedded IPS is visible on the pages",
                                     not missing, "0 missing facts",
                                     f"{len(missing)} missing" + (f": {missing[:3]}" if missing else "")))
    except Exception as exc:
        checks.append(IntegrityCheck("IC-6", "Embedded IPS is parseable", False, "valid JSON IPS", str(exc)))
    return checks


def checks_to_dict(checks: list[IntegrityCheck]) -> dict:
    return {"passed": all(c.passed for c in checks), "checks": [asdict(c) for c in checks]}


# --- tamper simulations (never applied to stored objects) -----------------

def tamper_embedded_payload(pdf_bytes: bytes) -> tuple[bytes, str]:
    """Silently alter a medication dosage inside the embedded IPS, leaving the pages untouched."""
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    spec = pdf.Root.AF[0]
    stream = spec.EF.F
    bundle = json.loads(stream.read_bytes())
    changed = "no MedicationStatement found; changed Bundle.timestamp"
    for e in bundle["entry"]:
        r = e["resource"]
        if r["resourceType"] == "MedicationStatement" and r.get("dosage"):
            old = r["dosage"][0]["text"]
            r["dosage"][0]["text"] = "10 x " + old
            changed = f"dosage '{old}' -> '{r['dosage'][0]['text']}'"
            break
    else:
        bundle["timestamp"] = "1999-01-01T00:00:00Z"
    new = (json.dumps(bundle, indent=2, ensure_ascii=False) + "\n").encode()
    stream.write(new)
    stream[Name.Params][Name.Size] = len(new)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue(), f"Embedded IPS altered ({changed}); visible pages unchanged"


def tamper_outer_bytes(pdf_bytes: bytes) -> tuple[bytes, str]:
    """Append bytes after %%EOF, as an unsigned incremental update would."""
    return pdf_bytes + b"\n% appended after issuance\n", "Bytes appended to the envelope after issuance"

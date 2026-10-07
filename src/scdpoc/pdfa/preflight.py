"""Offline PDF/A-3b pre-flight.

Checks a focused subset of ISO 19005-3 requirements that this packager is
responsible for. It exists so that the demonstrator works without Java and
so that regressions are caught in unit tests. It is NOT a conformance
validator and its result is never presented as PDF/A conformance - that
claim requires veraPDF (see ``verapdf.py``, AT-05).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pikepdf
from pikepdf import Name


@dataclass
class Check:
    id: str
    passed: bool
    message: str


@dataclass
class PreflightResult:
    engine: str = "builtin-pdfa-preflight"
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        return {"engine": self.engine, "passed": self.passed,
                "note": "Pre-flight subset only; not a PDF/A conformance claim.",
                "checks": [c.__dict__ for c in self.checks]}


def _iter_fonts(pdf: pikepdf.Pdf):
    for page in pdf.pages:
        res = page.obj.get(Name.Resources, {})
        fonts = res.get(Name.Font, {}) if res else {}
        for font in fonts.values():
            yield font
            yield from font.get(Name.DescendantFonts, [])


def preflight(pdf_bytes: bytes) -> PreflightResult:
    r = PreflightResult()

    def add(cid: str, ok: bool, msg: str) -> None:
        r.checks.append(Check(cid, bool(ok), msg))

    add("header", pdf_bytes.startswith(b"%PDF-1."), "file begins with a %PDF-1.n header")
    add("binary-comment", len(pdf_bytes) > 20 and all(b > 127 for b in pdf_bytes.split(b"\n", 2)[1][1:5]),
        "header followed by a comment of at least four binary bytes")
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    add("not-encrypted", not pdf.is_encrypted, "document is not encrypted")
    add("trailer-id", "/ID" in pdf.trailer, "trailer contains a file identifier (/ID)")

    meta = pdf.open_metadata()
    add("xmp-pdfaid", meta.get("pdfaid:part") == "3" and meta.get("pdfaid:conformance") == "B",
        "XMP declares pdfaid:part=3 and pdfaid:conformance=B")
    title = str(pdf.docinfo.get(Name.Title, ""))
    add("info-xmp-title", title == str(meta.get("dc:title", "")), "Info /Title matches XMP dc:title")
    add("info-xmp-producer", str(pdf.docinfo.get(Name.Producer, "")) == str(meta.get("pdf:Producer", "")),
        "Info /Producer matches XMP pdf:Producer")

    intents = pdf.Root.get(Name.OutputIntents, [])
    ok_intent = any(oi.get(Name.S) == Name.GTS_PDFA1 and Name.DestOutputProfile in oi for oi in intents)
    add("output-intent", ok_intent, "GTS_PDFA1 output intent with an ICC destination profile")

    fonts = list(_iter_fonts(pdf))
    unembedded = []
    for f in fonts:
        if f.get(Name.Subtype) == Name.Type0:
            continue
        fd = f.get(Name.FontDescriptor)
        if fd is None or not any(k in fd for k in (Name.FontFile, Name.FontFile2, Name.FontFile3)):
            unembedded.append(str(f.get(Name.BaseFont, "?")))
    add("fonts-embedded", fonts and not unembedded,
        "all fonts are embedded" + (f" (missing: {', '.join(unembedded)})" if unembedded else ""))

    transparency = any(
        (p.obj.get(Name.Group) or {}).get(Name.S) == Name.Transparency for p in pdf.pages)
    add("no-transparency-groups", not transparency, "no page-level transparency groups (avoids blending rules)")
    add("no-javascript", Name.JavaScript not in (pdf.Root.get(Name.Names) or {}) and Name.OpenAction not in pdf.Root,
        "no document JavaScript or open actions")

    af = pdf.Root.get(Name.AF, [])
    add("af-present", len(af) > 0, "catalog has an Associated Files (/AF) array")
    for i, spec in enumerate(af):
        stream = (spec.get(Name.EF) or {}).get(Name.F)
        add(f"af[{i}]-relationship", Name.AFRelationship in spec, "file specification declares AFRelationship")
        add(f"af[{i}]-f-uf", Name.F in spec and Name.UF in spec, "file specification has /F and /UF")
        add(f"af[{i}]-mime", stream is not None and Name.Subtype in stream,
            "embedded file stream declares a MIME /Subtype")
        add(f"af[{i}]-moddate", stream is not None and Name.ModDate in stream.get(Name.Params, {}),
            "embedded file /Params has /ModDate")
    return r

"""Deterministic conformance test vectors (profile 0.3.0, Annex A: test vectors).

Each invalid vector is derived from V-01 (or V-02) by exactly one change, so a failure can be attributed to
one rule. Vectors are written as:

    <id>/envelope.pdf           the artefact under test
    <id>/projection.json        the exchange projection as served
    <id>/issuance-record.json   the issuer's record (LIF-01)
    manifest.json               per vector: description, claimed classes and options, rules that must fail
                                and, for valid vectors, the rules that must pass (CHK-04)

Run ``scdpoc build-vectors`` to regenerate and ``scdpoc check-vectors`` to run the Checker over them.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name, String

from . import __version__
from .config import Settings
from .integrity import tamper_embedded_payload
from .ips.composer import compose_ips, serialise
from .ips.view import build_view
from .pdfa.packager import attachment_name_for, build_envelope
from .render.html import RENDERER_VERSION
from .render.pdf import render_pdf
from .safety import load_fixture

ISSUED = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
ATTESTED = datetime(2026, 10, 8, 12, 5, tzinfo=UTC)
DOC_ID = uuid.uuid5(uuid.NAMESPACE_URL, "ips-pe:vector:V-01")
DOC_ID_V02 = uuid.uuid5(uuid.NAMESPACE_URL, "ips-pe:vector:V-02")
FIXTURE = "amara-okafor"
ATTESTER = {"name": "Dr Sam Synthetic", "id": "SYN-PRAC-1", "time": ATTESTED}

# Rules each valid vector exercises for the Envelope class; ENV-01 and SRC-01 additionally pass only when the
# external validators are configured (CHK-02).
# Obligations each valid vector exercises for the Envelope class. ENV-01 and SRC-01 additionally pass only when the
# external validators are configured (CHK-02).
EXERCISED = ["ENV-11", "SRC-03", "SRC-05", "PROV-01", "PROV-03", "PROV-04", "PROV-05", "PROV-06",
             "REN-02", "REN-03", "REN-05", "REN-07.a", "REN-07.b", "REN-08", "REN-10", "REN-11", "REN-12", "REN-14",
             "ENV-02", "ENV-03.a", "ENV-03.b", "ENV-04.a", "ENV-04.b", "ENV-05", "ENV-06.a", "ENV-06.b", "ENV-07",
             "ENV-09", "ENV-10", "IPS-01", "IPS-03", "IPS-04", "IPS-05", "IPS-06"]
EXERCISED_AI = ["PROV-02.a", "PROV-02.b", "IPS-07"]
WITH_VALIDATORS = ["ENV-01", "SRC-01"]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _package(bundle_bytes: bytes, pages: bytes | None = None, **record_kw) -> tuple[bytes, dict]:
    bundle = json.loads(bundle_bytes)
    view = build_view(bundle)
    pages = pages or render_pdf(view, attachment_name_for(bundle["id"]))
    env = build_envelope(pages, bundle_bytes, document_id=bundle["id"], issued=ISSUED, title=view.title,
                         author=view.custodian, subject="Conformance test vector (synthetic)",
                         identifier=bundle["identifier"]["value"], language=view.language)
    return env.pdf_bytes, _record(bundle, bundle_bytes, env.pdf_bytes, **record_kw)


def _record(bundle: dict, bundle_bytes: bytes, pdf: bytes, *, assurance: str | None = None,
            evidence: dict | None = None) -> dict:
    comp = bundle["entry"][0]["resource"]
    idx = {e["fullUrl"]: e["resource"] for e in bundle["entry"]}
    custodian = idx.get(comp["custodian"]["reference"], {}).get("name")
    terms = sorted({c.get("system") for e in bundle["entry"] for c in _codings(e["resource"]) if c.get("system")})
    return {"documentId": bundle["identifier"]["value"], "seriesId": comp.get("identifier", {}).get("value"),
            "version": 1, "issued": bundle["timestamp"],
            "assurance": assurance or ("attested-issuance" if comp.get("attester") else "preserved-snapshot"),
            "attestationEvidence": evidence,
            "envelopeSha256": _sha(pdf), "ipsSha256": _sha(bundle_bytes), "renderer": RENDERER_VERSION,
            "provenance": {"sourceOrganisation": custodian, "custodian": custodian, "renderer": RENDERER_VERSION},
            "validationEvidence": {
                "ipsPackage": "hl7.fhir.uv.ips#2.0.1",
                "preflight": {"status": "passed", "engine": "builtin-preflight"},
                "hl7Validator": {"status": "not-run", "version": None,
                                 "detail": "vector generator: run the HL7 FHIR validator 7.0.1 to complete the evidence"},
                "pdfa": {"validator": "veraPDF", "status": "not-run", "version": None, "profile": "PDF/A-3B"}},
            "representation": {"ipsPackage": "hl7.fhir.uv.ips#2.0.1", "fhirVersion": "4.0.1",
                               "pdfa": "PDF/A-3b (ISO 19005-3:2012)", "renderer": RENDERER_VERSION,
                               "presentationResources": {"layout": RENDERER_VERSION, "labels": "en-GB",
                                                         "fonts": "Bitstream Vera, embedded"},
                               "terminologies": [{"system": t, "version": None} for t in terms]},
            "replaces": None, "replacementReason": None}


def _codings(node):
    if isinstance(node, dict):
        if isinstance(node.get("coding"), list):
            yield from node["coding"]
        for v in node.values():
            yield from _codings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _codings(v)


def _compose(settings: Settings, **kw):
    return compose_ips(load_fixture(settings, FIXTURE), settings, issued=ISSUED, **kw)


def _with_relationship(pdf: bytes, rel: str) -> bytes:
    p = pikepdf.open(io.BytesIO(pdf))
    p.Root.AF[0][Name.AFRelationship] = Name("/" + rel)
    out = io.BytesIO()
    p.save(out, deterministic_id=True)
    return out.getvalue()


def _with_second_source(pdf: bytes, payload: bytes) -> bytes:
    p = pikepdf.open(io.BytesIO(pdf))
    ef = pikepdf.Stream(p, payload)
    ef[Name.Type] = Name.EmbeddedFile
    ef[Name.Subtype] = Name("/application/fhir+json")
    ef[Name.Params] = Dictionary(Size=len(payload), ModDate=String("D:20261008120000+00'00'"))
    spec = p.make_indirect(Dictionary(Type=Name.Filespec, F=String("second.fhir.json"), UF=String("second.fhir.json"),
                                      AFRelationship=Name.Source, EF=Dictionary(F=ef, UF=ef)))
    p.Root.AF.append(spec)
    names = p.Root.Names.EmbeddedFiles.Names
    names.extend([String("second.fhir.json"), spec])
    out = io.BytesIO()
    p.save(out, deterministic_id=True)
    return out.getvalue()


def _pages_with_unembedded_font(pages: bytes) -> bytes:
    p = pikepdf.open(io.BytesIO(pages))
    page = p.pages[0]
    font = p.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    page.Resources.Font[Name("/FX")] = font
    page.contents_add(pikepdf.Stream(p, b"BT /FX 6 Tf 20 20 Td (x) Tj ET"), prepend=False)
    out = io.BytesIO()
    p.save(out, deterministic_id=True)
    return out.getvalue()


def _incremental_update(pdf: bytes) -> bytes:
    """Append a genuine incremental update (new Info dictionary) after the issuer's %%EOF."""
    p = pikepdf.open(io.BytesIO(pdf))
    size = int(p.trailer.Size)
    root = p.trailer.Root.objgen
    prev = int(re.findall(rb"startxref\s+(\d+)", pdf)[-1])
    body = pdf if pdf.endswith(b"\n") else pdf + b"\n"
    obj_off = len(body)
    obj = f"{size} 0 obj\n<< /Title (Altered after issuance) >>\nendobj\n".encode()
    xref_off = obj_off + len(obj)
    xref = (f"xref\n{size} 1\n{obj_off:010d} 00000 n \n"
            f"trailer\n<< /Size {size + 1} /Root {root[0]} {root[1]} R /Info {size} 0 R /Prev {prev} >>\n"
            f"startxref\n{xref_off}\n%%EOF\n").encode()
    return body + obj + xref


def _section(view, code):
    return next(s for s in view.sections if s.code == code)


def _without_tounicode(pdf: bytes) -> bytes:
    p = pikepdf.open(io.BytesIO(pdf))
    for page in p.pages:
        for _, f in (page.Resources.get(Name.Font) or {}).items():
            if Name.ToUnicode in f:
                del f[Name.ToUnicode]
    out = io.BytesIO()
    p.save(out, deterministic_id=True)
    return out.getvalue()


def build(out_dir: Path, settings: Settings | None = None) -> dict:
    from .ips.attestation import add_attestation, attested_content_digest
    settings = settings or Settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _compose(settings, document_id=DOC_ID)
    ips = base.json_bytes
    bundle = json.loads(ips)
    pdf, rec = _package(ips)
    name = attachment_name_for(bundle["id"])
    vectors: dict[str, dict] = {}

    def write(vid: str, mutation: str, envelope: bytes, projection: bytes, record: dict, must_fail: list[str],
              why: str, *, options: list[str] | None = None, must_fail_with_options: dict | None = None,
              valid: bool = False, classes: list[str] | None = None, expected: dict | None = None) -> None:
        d = out_dir / vid
        d.mkdir(exist_ok=True)
        (d / "envelope.pdf").write_bytes(envelope)
        (d / "projection.json").write_bytes(projection)
        (d / "issuance-record.json").write_text(json.dumps(record, indent=2) + "\n")
        v = {"description": mutation, "mutation": mutation, "classes": classes or ["Envelope"],
             "options": options or [], "valid": valid, "mustFail": must_fail, "why": why,
             "expected": expected or {}, "envelopeSha256": _sha(envelope)}
        if valid:
            v["mustPass"] = EXERCISED + (EXERCISED_AI if "AI" in (options or []) else [])
            v["mustPassWithValidators"] = WITH_VALIDATORS
        if must_fail_with_options:
            v["mustFailWithOptions"] = must_fail_with_options
        vectors[vid] = v

    write("V-01", "None: valid preserved snapshot", pdf, ips, rec, [], "-", valid=True)

    # V-02 follows the attestation sequence (PROV-08): the V-01 content was reviewed, the attestation recorded against
    # its attested content digest, and the final source is the reviewed draft plus the attestation (IPS-07).
    att_bundle, att_ips = add_attestation(bundle, {**ATTESTER, "kind": "person"}, DOC_ID_V02, ATTESTED)
    ev = {"attester": ATTESTER["name"], "attesterId": ATTESTER["id"], "attesterKind": "person",
          "time": "2026-10-08T12:05:00+00:00", "mode": "legal",
          "method": "Conformance vector: a declared synthetic attestation action, not a clinical one",
          "statement": "I have reviewed this patient summary and accept responsibility for its content.",
          "reviewedDraft": str(DOC_ID), "reviewedIpsSha256": _sha(ips),
          "attestedContentDigest": attested_content_digest(bundle)}
    pdf2, rec2 = _package(att_ips, evidence=ev)
    write("V-02", "None: valid attested issuance with evidence of the attestation action (option AI)", pdf2,
          att_ips, rec2, [], "-", options=["AI"], valid=True)

    tampered, what = tamper_embedded_payload(pdf)
    write("I-01", f"{what}; pages, projection and issuance record unchanged", tampered, ips, rec,
          ["ENV-05", "REN-02", "XCH-02"],
          "The embedded octets no longer match the recorded digest (ENV-05) or the projection (XCH-02), and the pages "
          "no longer show the value the embedded source now carries (REN-02).", classes=["Envelope", "Issuer"])

    write("I-02", "AFRelationship of the embedded IPS set to /Data", _with_relationship(pdf, "Data"), ips, rec,
          ["ENV-02", "ENV-06.a"], "No associated file has relationship Source.")

    view = build_view(bundle)
    _section(view, "10160-0").rows = _section(view, "10160-0").rows[:-1]
    pdf4, rec4 = _package(ips, render_pdf(view, name))
    write("I-04", "One medication entry omitted from the pages", pdf4, ips, rec4, ["REN-02"],
          "The omitted entry's required elements are not visible.")

    write("I-05", "Second associated file with relationship /Source", _with_second_source(pdf, ips), ips, rec,
          ["ENV-06.a"], "Two files have relationship Source.")

    pdf6, rec6 = _package(ips, _pages_with_unembedded_font(render_pdf(build_view(bundle), name)))
    write("I-06", "Text set in a non-embedded standard font (Helvetica)", pdf6, ips, rec6, ["ENV-01"],
          "PDF/A-3 requires every font used for rendering to be embedded. ENV-10 is not expected: a standard font "
          "with a standard encoding has a Unicode mapping.")

    write("I-07", "Incremental update appended after issuance", _incremental_update(pdf), ips, rec, ["ENV-11"],
          "The file differs from the issued file.")

    b8 = copy.deepcopy(bundle)
    b8["type"] = "collection"
    ips8 = serialise(b8)
    pdf8, rec8 = _package(ips8)
    write("I-08", "Bundle.type changed to collection (record and envelope consistent)", pdf8, ips8, rec8,
          ["SRC-01"], "An IPS Bundle has type document; the structural pre-flight fails before any validator runs.")

    reser = (json.dumps(bundle, separators=(",", ":"), ensure_ascii=False)).encode()
    write("I-09", "Projection re-serialised with different white space", pdf, reser, rec, ["XCH-02", "SRC-02"],
          "The exchanged octets differ from the embedded ones.", classes=["Envelope", "Issuer"])

    view = build_view(bundle)
    view.sections = [s for s in view.sections if s.code != "18776-5"]
    pdf10, rec10 = _package(ips, render_pdf(view, name))
    write("I-10", "Narrative-only section (plan of care) omitted from the pages", pdf10, ips, rec10,
          ["REN-02", "REN-12"], "The section title and its narrative are required and absent.")

    b11 = copy.deepcopy(bundle)
    med = next(e["resource"] for e in b11["entry"] if e["resource"]["resourceType"] == "MedicationStatement"
               and e["resource"]["medicationCodeableConcept"]["coding"][0]["code"] == "C09AA05")
    med["dosage"][0]["doseAndRate"][0]["doseQuantity"]["value"] = 10
    pdf11, rec11 = _package(ips, render_pdf(build_view(b11), name))
    write("I-11", "Dose on the pages differs from the source (dose 10 mg shown, 5 mg in the IPS)", pdf11, ips, rec11,
          ["REN-10"], "The entry is shown but its dose is not the canonical form of the source value.")

    rec12 = {**rec, "assurance": "attested-issuance"}
    write("I-12", "Issuance record claims attested issuance without attestation evidence", pdf, ips, rec12,
          ["PROV-04"], "Attestation is claimed and no evidence exists; with option AI the evidence obligation fails.",
          must_fail_with_options={"AI": ["PROV-02.a"]})

    b13 = json.loads(_compose(settings, document_id=DOC_ID, attestation=ATTESTER).json_bytes)
    ips13 = serialise(b13)
    pdf13, rec13 = _package(ips13, assurance="preserved-snapshot")
    write("I-13", "Legal attester in the source with no attestation evidence (the 0.2.0 behaviour)", pdf13, ips13,
          rec13, ["PROV-04", "IPS-03"], "The source claims attestation for a preserved snapshot.")

    view = build_view(bundle)
    probs, meds = _section(view, "11450-4"), _section(view, "10160-0")
    meds.rows.append([*probs.rows[0][:2], "", "", "", ""])
    pdf14, rec14 = _package(ips, render_pdf(view, name))
    write("I-14", "A problem code shown in the medication section", pdf14, ips, rec14, ["REN-08"],
          "The code is carried neither by that section's entries nor by its source narrative.")

    b15 = copy.deepcopy(bundle)
    b15["entry"][0]["resource"]["status"] = "preliminary"
    ips15 = serialise(b15)
    pdf15, rec15 = _package(ips15)
    write("I-15", "Composition.status preliminary", pdf15, ips15, rec15, ["IPS-04"],
          "An issuance is final, or amended for a correction.")

    b16 = copy.deepcopy(bundle)
    comp = b16["entry"][0]["resource"]
    msec = next(s for s in comp["section"] if s["code"]["coding"][0]["code"] == "10160-0")
    first, rest = msec["entry"][0], msec["entry"][1:]
    msec["entry"] = rest
    msec["section"] = [{"title": "Cardiovascular medicines",
                        "code": {"coding": [{"system": "http://loinc.org", "code": "10160-0"}]},
                        "text": msec["text"], "entry": [first]}]
    ips16 = serialise(b16)
    view = build_view(b16)
    nested = _section(view, "10160-0").subsections[0]
    nested.rows = [[*r[:3], r[3].replace(" · once per day", ""), *r[4:]] for r in nested.rows]
    pdf16, rec16 = _package(ips16, render_pdf(view, name))
    write("I-16", "Nested section; the nested entry's dosage timing omitted from the pages", pdf16, ips16, rec16,
          ["REN-02", "REN-12"], "A required element of an entry in a nested section is not visible.")

    pdf17, rec17 = _package(ips, _without_tounicode(render_pdf(build_view(bundle), name)))
    write("I-17", "ToUnicode maps removed from the embedded fonts", pdf17, ips, rec17, ["ENV-10"],
          "The embedded subset fonts use an identity encoding, so without ToUnicode the page text has no Unicode "
          "mapping. ENV-10 is advisory: the failure is reported as a warning. REN-02 may also fail, because the "
          "Checker reads the pages through that mapping.")

    b18 = copy.deepcopy(att_bundle)
    m18 = next(e["resource"] for e in b18["entry"] if e["resource"]["resourceType"] == "MedicationStatement"
               and e["resource"]["medicationCodeableConcept"]["coding"][0]["code"] == "C09AA05")
    m18["dosage"][0]["doseAndRate"][0]["doseQuantity"]["value"] = 10
    m18["dosage"][0]["text"] = "10 mg orally once daily"
    ips18 = serialise(b18)
    pdf18, rec18 = _package(ips18, evidence=ev)
    write("I-18", "Attested issuance whose dose was changed after attestation; evidence unchanged; pages rendered from "
          "the changed source (option AI)", pdf18, ips18, rec18, ["PROV-02.b", "IPS-07"],
          "The attested content digest of the issued source differs from the digest that was attested.",
          options=["AI"])

    b19 = copy.deepcopy(bundle)
    cond = next(e["resource"] for e in b19["entry"] if e["resource"]["resourceType"] == "Condition")
    cond["stage"] = [{"summary": {"text": "Stage 1 hypertension"}}]
    ips19 = serialise(b19)
    pdf19, rec19 = _package(ips19)
    write("I-19", "Boundary: a Condition stage (disposition N) populated and shown; no inspection declared", pdf19,
          ips19, rec19, [], "The element has no canonical form, so automated checking cannot establish its "
          "fidelity: REN-14 is not-tested, and no obligation fails.", expected={"REN-14": "not-tested"})

    manifest = {
        "profile": "urn:oid:2.25.11064312502901710892401295388462879468", "profileVersion": "0.4.0",
        "generator": f"scdpoc {__version__}", "fixture": FIXTURE, "synthetic": True,
        "note": "I-03 (sIPS format code on the envelope DocumentEntry) is a registry-level vector exercised by live "
                "scenario L-03 against a running registry.",
        "vectors": vectors,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest

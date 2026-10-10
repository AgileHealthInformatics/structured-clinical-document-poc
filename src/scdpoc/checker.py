"""Checker for the IPS Preservation Envelope Profile 0.3.0 (Annex A: CHK-01 .. CHK-09).

The Checker is driven by the profile's machine-readable rule catalogue (``conformance/rules.json``,
generated from the specification). For a claim - a set of conformance classes and options - it reports
every applicable rule, each with an outcome per required verification method, and a verdict:

* ``non-conformant``  an applicable MUST / MUST NOT rule failed;
* ``incomplete``      otherwise, an applicable MUST / MUST NOT rule is not-tested;
* ``conformant``      every applicable mandatory rule passed or is not applicable.

Evidence comes from four sources, one per verification method:

* ``artefact``   this module, offline, from an envelope, its projection and its issuance record;
* ``claim``      this module, from a conformance claim (JSON);
* ``live``       a result file produced by ``scdpoc live-check`` against a running implementation;
* ``vectors``    a result file produced by ``scdpoc check-vectors``;
* ``inspection`` inspection records declared in the claim (reported as declared, CHK-08).

Independence note (CHK-05): this Checker is written by the same project as the demonstrator it is first
used on. Its report is evidence, not independent assurance.

Usage:  scdpoc check envelope.pdf [--projection ips.json] [--record issuance.json]
                     [--class Envelope ...] [--option AI ...] [--claim claim.json]
                     [--evidence live.json ...] [--out report.json]
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import pikepdf
from pikepdf import Name

from . import __version__, fidelity
from .config import Settings
from .ips.attestation import attested_content_digest
from .ips.validator import BuiltinIpsPreflight, Hl7FhirValidator
from .ips.view import ASSURANCE_ATTESTED, ASSURANCE_SNAPSHOT
from .pdfa.extract import extract_embedded, page_text
from .pdfa.preflight import preflight
from .pdfa.verapdf import VeraPdf

CONFORMANCE = Settings().home / "conformance"
CANONICAL_ID = "urn:oid:2.25.11064312502901710892401295388462879468"
PROFILE = "ips-preservation-envelope"
PROFILE_VERSION = "0.4.0"
IPS_MIME = "application/fhir+json"
CHECKER = f"scdpoc-checker {__version__}"
INDEPENDENCE = ("Produced by a Checker from the same project as the demonstrator: evidence, not independent "
                "assurance (CHK-05).")
OUTCOMES = ("pass", "fail", "not-applicable", "not-tested")

# Capabilities and actor bindings (profile 0.4.0, "Capabilities and actor bindings"): per class, the bindings and
# the transactions each requires; "all" means every binding of the class is required, "any" at least one.
BINDINGS = {
    "Publisher": ("all", {"XDS.b Document Source": {"ITI-41"},
                          "XDS Metadata Update Document Administrator": {"ITI-57"}}),
    "Responder": ("any", {"XDS.b Document Repository": {"ITI-41", "ITI-43"},
                          "XDS.b Document Registry": {"ITI-18", "ITI-57"},
                          "MHD Document Responder": {"ITI-67", "ITI-68"}}),
    "Responding Gateway": ("all", {"XCPD Responding Gateway": {"ITI-55"},
                                   "XCA Responding Gateway": {"ITI-38", "ITI-39"}}),
    "Receiver": ("any", {"XCPD and XCA Initiating Gateway": {"ITI-55", "ITI-38", "ITI-39"},
                         "MHD Document Consumer": {"ITI-67", "ITI-68"},
                         "XDS.b Document Consumer": {"ITI-18", "ITI-43"}}),
}
FORBIDDEN_CLAIMS = re.compile(r"\b(EHDS|MyHealth@EU|EEHRxF|European electronic health record exchange format|"
                              r"HL7|IHE|IPS|XDS|MHD)\b[^.]{0,60}\b(complian\w*|conform\w*|certified|certification)\b"
                              r"|\b(complian\w*|conform\w*|certified)\b[^.]{0,30}\b(with|to)\b[^.]{0,20}"
                              r"\b(EHDS|MyHealth@EU|HL7|IHE)\b", re.I)
MANDATORY = {"MUST", "MUST NOT"}
ADVISORY = {"SHOULD", "SHOULD NOT"}


class CatalogueError(ValueError):
    """CHK-10: a catalogue, manifest, result file or claim refers to something undefined."""


# ------------------------------------------------------------------ catalogue

def validate_catalogue(cat: dict) -> None:
    problems = []
    tests = set(cat.get("tests", []))
    methods = set(cat.get("verificationMethods", []))
    for oid, o in cat["obligations"].items():
        if o["level"] not in MANDATORY | ADVISORY:
            problems.append(f"{oid}: unknown level {o['level']}")
        if not o["verification"] or set(o["verification"]) - methods:
            problems.append(f"{oid}: missing or unknown verification method {o['verification']}")
        if set(o["tests"]) - tests:
            problems.append(f"{oid}: unknown tests {sorted(set(o['tests']) - tests)}")
        opt = (o.get("option") or "").removeprefix("not:")
        if opt and opt not in cat.get("options", {}):
            problems.append(f"{oid}: unknown option {o['option']}")
    if problems:
        raise CatalogueError("; ".join(problems[:10]))


def index_catalogue(cat: dict) -> dict:
    cat["obligations"] = {}
    cat["ruleObligations"] = {}
    for r in cat["rules"]:
        cat["ruleObligations"][r["id"]] = [o["id"] for o in r["obligations"]]
        for o in r["obligations"]:
            cat["obligations"][o["id"]] = {**o, "rule": r["id"]}
    cat["index"] = {r["id"]: r for r in cat["rules"]}
    return cat


@lru_cache(maxsize=4)
def load_catalogue(path: str | None = None) -> dict:
    p = Path(path) if path else CONFORMANCE / "rules.json"
    raw = p.read_bytes()
    cat = index_catalogue(json.loads(raw))
    cat["sha256"] = hashlib.sha256(raw).hexdigest()
    validate_catalogue(cat)
    return cat


@lru_cache(maxsize=2)
def load_dependencies(path: str | None = None) -> dict:
    p = Path(path) if path else CONFORMANCE / "dependencies.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    deps = {d["key"]: d for d in data["dependencies"]}
    deps["__permitted__"] = data.get("permittedAlternatives", [])
    return deps


def applies(ob: dict, classes: set[str]) -> bool:
    return "All classes" in ob["classes"] or bool(classes & set(ob["classes"]))


def option_applies(ob: dict, options: set[str]) -> tuple[bool, str]:
    opt = ob.get("option")
    if not opt:
        return True, ""
    if opt.startswith("not:"):
        o = opt[4:]
        return o not in options, f"applies only when option {o} is not claimed"
    return opt in options, f"option {opt} not claimed"


def required_methods(ob: dict, classes: set[str], scope: dict) -> tuple[set[str], list[str]]:
    """Methods required for an obligation given the claimed classes (profile, "Verification methods")."""
    targets = classes if "All classes" in ob["classes"] else classes & set(ob["classes"])
    methods = set(ob["verification"])
    out: set[str] = set()
    uncovered = []
    for k in targets:
        mk = {m for m in methods - {"inspection"} if "*" in scope[m] or k in scope[m]}
        if not mk and "inspection" in methods:
            mk = {"inspection"}
        if not mk:
            uncovered.append(k)
        out |= mk
    return out, uncovered


# ------------------------------------------------------------------ report model

@dataclass
class Result:
    rule: str                         # obligation id: "ENV-01" or "PRES-05.a"
    outcome: str                      # pass | fail | not-applicable | not-tested
    evidence: str
    level: str | None = None
    mandatory: bool = True
    methods: dict = field(default_factory=dict)     # method -> {outcome, evidence}
    ruleId: str = ""


@dataclass
class Report:
    profile: str
    profileName: str
    profileVersion: str
    ruleCatalogue: dict
    checker: str
    independence: str
    claimed: dict
    tested: dict
    validators: dict
    testedAt: str
    results: list[Result] = field(default_factory=list)
    verdict: str = "incomplete"
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    evidenceSources: list[dict] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.outcome] = out.get(r.outcome, 0) + 1
        return out

    def outcome(self, rule: str) -> str:
        """Outcome of an obligation, or of a rule (aggregated over its applicable obligations)."""
        exact = next((r.outcome for r in self.results if r.rule == rule), None)
        if exact:
            return exact
        outs = [r.outcome for r in self.results if r.ruleId == rule]
        if not outs:
            return "absent"
        for o in ("fail", "not-tested", "pass"):
            if o in outs:
                return o
        return "not-applicable"

    def method_outcome(self, rule: str, method: str = "artefact") -> str:
        r = next((r for r in self.results if r.rule == rule), None)
        return (r.methods.get(method) or {}).get("outcome", "absent") if r else "absent"

    def failed(self) -> set[str]:
        """Failed obligations, and the rules they belong to."""
        f = {r.rule for r in self.results if r.outcome == "fail"}
        return f | {r.ruleId for r in self.results if r.outcome == "fail"}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = self.summary()
        return d


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ artefact method

class _Obs(dict):
    """rule -> (outcome, evidence) for one method."""

    def add(self, rule: str, outcome: str, evidence: str) -> None:
        assert outcome in OUTCOMES, outcome
        self[rule] = (outcome, evidence)


def _name(idx: dict, ref: str | None) -> tuple[str, str]:
    r = idx.get(ref or "", {})
    return r.get("resourceType", ""), fidelity._name(r) if r else ""


def artefact_results(pdf_bytes: bytes, projection: bytes | None, record: dict | None, settings: Settings,
                     vera: VeraPdf, hl7: Hl7FhirValidator) -> _Obs:
    o = _Obs()
    no_rec = "no issuance record supplied"
    try:
        pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    except Exception as exc:
        o.add("ENV-01", "fail", f"not a readable PDF: {exc}")
        return o

    # ENV-11: no incremental update after the issuer's single save.
    eofs = len(re.findall(rb"%%EOF", pdf_bytes))
    trailing = pdf_bytes.rstrip()[-5:] == b"%%EOF"
    if record and record.get("envelopeSha256"):
        same = _sha256(pdf_bytes) == record["envelopeSha256"]
        o.add("ENV-11", "pass" if same else "fail",
              "envelope SHA-256 equals issuance record" if same else "envelope differs from issuance record")
    else:
        ok = eofs == 1 and trailing
        o.add("ENV-11", "pass" if ok else "fail", f"{eofs} %%EOF marker(s); structural test only ({no_rec})")

    pf = preflight(pdf_bytes)
    meta = pdf.open_metadata()
    part_ok = meta.get("pdfaid:part") == "3"
    if vera.configured():
        v = vera.validate(pdf_bytes)
        o.add("ENV-01", "pass" if v.status == "passed" and part_ok else
              "not-tested" if v.status in ("error", "not-run") else "fail",
              f"veraPDF {v.status}: {v.detail}; pdfaid:part={meta.get('pdfaid:part')}")
    else:
        failed = [c.id for c in pf.checks if not c.passed]
        if failed or not part_ok:
            o.add("ENV-01", "fail", f"pre-flight failures: {failed}")
        else:
            o.add("ENV-01", "not-tested", "pre-flight subset passed; veraPDF not configured, so conformance is "
                                          "not decided (CHK-02)")
    unmapped = _unmapped_fonts(pdf)
    o.add("ENV-10", "pass" if not unmapped else "fail",
          "every font has a ToUnicode map or a standard encoding" if not unmapped
          else f"fonts without a Unicode mapping: {unmapped[:4]}")
    js = next((c.passed for c in pf.checks if c.id == "no-javascript"), False)
    o.add("ENV-07", "pass" if js and not pdf.is_encrypted else "fail",
          "no encryption, JavaScript or open action" if js and not pdf.is_encrypted else
          "encryption, JavaScript or an open action present")

    files = extract_embedded(pdf_bytes)
    sources = [f for f in files if f.relationship == "Source"]
    others = [f.filename for f in files if f.relationship != "Source"]
    o.add("ENV-06.a", "pass" if len(sources) == 1 else "fail", f"{len(sources)} Source file(s)")
    o.add("ENV-06.b", "pass" if not others else "fail", f"other embedded files: {others or 'none'}")
    src = sources[0] if len(sources) == 1 else None
    creator = meta.get("xmp:CreatorTool") or ""
    ren05 = bool(creator) and str(pdf.docinfo.get(Name.Creator, "")) == creator and \
        (record is None or (record.get("renderer") or "") in creator)
    o.add("REN-05", "pass" if ren05 else "fail", f"xmp:CreatorTool={creator!r}; record renderer="
          f"{(record or {}).get('renderer')!r}")
    if src is None:
        o.add("ENV-02", "fail", "no unique Source associated file")
        return o

    o.add("ENV-02", "pass" if src.listed_in_af and src.listed_in_name_tree else "fail",
          f"listed in /AF: {src.listed_in_af}; in EmbeddedFiles: {src.listed_in_name_tree}")
    spec = _filespec(pdf, src.filename)
    stream = spec.EF.F if spec is not None else None
    params = stream.get(Name.Params, {}) if stream is not None else {}
    o.add("ENV-03.a", "pass" if src.mime_type == IPS_MIME and Name.ModDate in params else "fail",
          f"Subtype={src.mime_type}; ModDate={'present' if Name.ModDate in params else 'absent'}")
    o.add("ENV-03.b", "pass" if Name.Size in params else "fail",
          f"Size={'present' if Name.Size in params else 'absent'}")
    has_f_uf = spec is not None and Name.F in spec and Name.UF in spec
    o.add("ENV-04.a", "pass" if has_f_uf else "fail", "/F and /UF present" if has_f_uf else "/F or /UF missing")
    o.add("ENV-04.b", "pass" if spec is not None and Name.Desc in spec else "fail",
          "/Desc present" if spec is not None and Name.Desc in spec else "/Desc absent")

    data = src.data
    if record and record.get("ipsSha256"):
        same = src.sha256 == record["ipsSha256"]
        o.add("ENV-05", "pass" if same else "fail",
              f"embedded SHA-256 {'equals' if same else 'differs from'} issuance record")
    elif projection is not None:
        o.add("ENV-05", "pass" if data == projection else "fail", "embedded octets compared with projection")
    else:
        o.add("ENV-05", "not-tested", "no issuance record or projection to compare with")
    if projection is not None:
        same = data == projection
        o.add("XCH-02", "pass" if same else "fail", "projection octets identical to embedded source" if same
              else "projection octets differ from embedded source")
        o.add("SRC-02", "pass" if same else "fail", "one serialisation used for embedding and exchange" if same
              else "projection re-serialised")
    else:
        o.add("XCH-02", "not-tested", "no projection supplied")
        o.add("SRC-02", "not-tested", "no projection supplied")

    try:
        bundle = json.loads(data.decode("utf-8"))
        o.add("SRC-03", "pass", "UTF-8 JSON, application/fhir+json")
    except (UnicodeDecodeError, ValueError) as exc:
        o.add("SRC-03", "fail", f"not UTF-8 JSON: {exc}")
        return o

    pre = BuiltinIpsPreflight(settings).run(bundle)
    errors = [f"{i.rule} {i.location}" for i in pre.issues if i.severity == "error"]
    if errors:
        o.add("SRC-01", "fail", f"structural errors: {errors[:5]}")
    elif hl7.available():
        h = hl7.run(data)
        o.add("SRC-01", "pass" if h.status == "passed" else "fail", f"HL7 FHIR validator {h.status}")
    else:
        o.add("SRC-01", "not-tested", "structural pre-flight passed; HL7 FHIR validator not configured (CHK-02)")

    entries = bundle.get("entry") or [{}]
    comp = entries[0].get("resource", {})
    idx = {e.get("fullUrl"): e.get("resource", {}) for e in entries}
    ident = (bundle.get("identifier") or {})
    series = (comp.get("identifier") or {}).get("value")
    o.add("SRC-05", "pass" if ident.get("value") and series else "fail",
          f"Bundle.identifier={'present' if ident.get('value') else 'absent'}; series identifier="
          f"{'present' if series else 'absent'}")
    o.add("IPS-01", "pass" if ident.get("system") == "urn:ietf:rfc:3986" and str(ident.get("value", "")).startswith(
        "urn:uuid:") and series else "fail", f"Bundle.identifier {ident.get('system')}|{ident.get('value')}; "
          f"Composition.identifier {'present' if series else 'absent'}")

    replaces = [r for r in comp.get("relatesTo", []) if r.get("code") == "replaces"]
    rec_replaces = (record or {}).get("replaces")
    if replaces or rec_replaces:
        target = (replaces[0].get("targetIdentifier") or {}).get("value") if replaces else None
        ok = bool(target) and (rec_replaces is None or target == rec_replaces)
        o.add("SRC-06", "pass" if ok else "fail", f"relatesTo replaces {target}; record replaces {rec_replaces}")
        o.add("IPS-02", "pass" if ok and str(target).startswith("urn:uuid:") else "fail",
              f"Composition.relatesTo replaces targetIdentifier={target}")
    else:
        o.add("SRC-06", "not-applicable", "not a replacement")
        o.add("IPS-02", "not-applicable", "not a replacement")

    status = comp.get("status")
    ok = status == "final" or (status == "amended" and bool(replaces))
    o.add("IPS-04", "pass" if ok else "fail", f"Composition.status={status}"
          + ("; amended without a replaced issuance" if status == "amended" and not replaces else ""))

    # ---- provenance and assurance
    text_pages = page_text(pdf_bytes)
    authors = [_name(idx, a.get("reference")) for a in comp.get("author", [])]
    custodian = _name(idx, (comp.get("custodian") or {}).get("reference"))
    attesters = comp.get("attester") or []
    evidence = (record or {}).get("attestationEvidence")
    rec_assurance = (record or {}).get("assurance")
    claims_attested = bool(attesters) or rec_assurance == "attested-issuance"

    if record is None:
        o.add("PROV-01", "not-tested", f"source: author={'present' if authors else 'absent'}, custodian="
              f"{'present' if custodian[1] else 'absent'}; record part not tested ({no_rec})")
    else:
        prov = record.get("provenance") or {}
        roles = {"author": bool(authors and all(n for _, n in authors)), "custodian": bool(custodian[1]),
                 "record source organisation": bool(prov.get("sourceOrganisation")),
                 "record renderer": bool(record.get("renderer") or prov.get("renderer")),
                 "record assurance": rec_assurance in ("preserved-snapshot", "attested-issuance")}
        o.add("PROV-01", "pass" if all(roles.values()) else "fail",
              ", ".join(f"{k}={'present' if v else 'absent'}" for k, v in roles.items()))

    if not claims_attested:
        for k in ("PROV-02.a", "PROV-02.b", "IPS-07"):
            o.add(k, "not-applicable", "a preserved snapshot: no attestation is claimed")
    else:
        att_ok = any(a.get("mode") in ("legal", "official") and a.get("time") and
                     (a.get("party") or {}).get("reference") in idx for a in attesters)
        needed = ["attester", "time", "method", "statement", "reviewedIpsSha256"]
        ev_missing = [k for k in needed if not (evidence or {}).get(k)] if record is not None else needed
        if record is None:
            o.add("PROV-02.a", "not-tested", f"source attester {'valid' if att_ok else 'invalid'}; evidence not tested "
                                           f"({no_rec})")
        else:
            o.add("PROV-02.a", "pass" if att_ok and not ev_missing else "fail",
                  f"source attester {'mode legal with time and party' if att_ok else 'absent or incomplete'}; "
                  + (f"attestation evidence lacks {ev_missing}" if ev_missing else "attestation evidence complete"))
        if record is None or not (evidence or {}).get("attestedContentDigest"):
            for k in ("PROV-02.b", "IPS-07"):
                o.add(k, "fail" if record is not None else "not-tested",
                      "no attested content digest recorded" if record is not None else no_rec)
        else:
            actual = attested_content_digest(bundle)
            same = actual == evidence["attestedContentDigest"]
            msg = ("attested content digest of the issued source equals the digest attested" if same else
                   f"attested content digest {actual[:16]}… differs from the digest attested "
                   f"{evidence['attestedContentDigest'][:16]}…: content changed after attestation")
            o.add("PROV-02.b", "pass" if same else "fail", msg)
            o.add("IPS-07", "pass" if same else "fail", msg)

    if record is None:
        o.add("PROV-04", "not-tested" if attesters else "pass",
              "attester in source; cannot tell whether evidence exists without the issuance record" if attesters
              else "no attestation claimed in the source or the pages")
    else:
        has_evidence = bool(evidence and all(evidence.get(k) for k in ("attester", "time", "method")))
        claims = []
        if attesters:
            claims.append("Composition.attester")
        if rec_assurance == "attested-issuance":
            claims.append("issuance record assurance")
        if fidelity._has(text_pages.lower(), ASSURANCE_ATTESTED):
            claims.append("rendition statement")
        if has_evidence:
            o.add("PROV-04", "pass", "attestation evidence present: " + ", ".join(claims or ["no claim"]))
        else:
            o.add("PROV-04", "fail" if claims else "pass",
                  f"attestation claimed without evidence in: {claims}" if claims
                  else "no attestation claimed anywhere: a preserved snapshot")

    expected_statement = ASSURANCE_ATTESTED if attesters else ASSURANCE_SNAPSHOT
    shown = fidelity._has(text_pages.lower(), expected_statement)
    o.add("IPS-06", "pass" if shown else "fail", f"assurance statement {'shown' if shown else 'missing'}: "
          f"{expected_statement!r}")
    if record is None:
        o.add("PROV-05", "not-tested", f"rendition statement {'shown' if shown else 'missing'}; {no_rec}")
    else:
        consistent = (rec_assurance == "attested-issuance") == bool(attesters)
        ok = rec_assurance in ("preserved-snapshot", "attested-issuance") and consistent and shown
        o.add("PROV-05", "pass" if ok else "fail", f"record assurance={rec_assurance}; source "
              f"{'has' if attesters else 'has no'} attester; rendition statement {'shown' if shown else 'missing'}")

    allowed = {"Practitioner", "PractitionerRole", "Organization", "Device"}
    a_ok = bool(authors) and all(t in allowed for t, _ in authors) and bool(custodian[1])
    if attesters:
        att_valid = all(a.get("mode") in ("legal", "official") and a.get("time") and a.get("party") for a in attesters)
        snapshot = record is not None and not (evidence and evidence.get("attester"))
        ok = a_ok and att_valid and not snapshot
        o.add("IPS-03", "pass" if ok else "fail" if record is not None or not att_valid else "not-tested",
              f"author types {[t for t, _ in authors]}; attester {'valid' if att_valid else 'invalid'}"
              + ("; attester present on a preserved snapshot (no attestation evidence)" if snapshot else ""))
    else:
        o.add("IPS-03", "pass" if a_ok else "fail", f"author types {[t for t, _ in authors]}; custodian "
              f"{'present' if custodian[1] else 'absent'}; no attester (preserved snapshot)")

    prov6 = {"author": bool(authors and authors[0][1]), "custodian": bool(custodian[1]),
             "clinical content date": bool(comp.get("date")), "status": bool(status),
             "document identifier": bool(ident.get("value")), "series identifier": bool(series),
             "replaced identifier": bool(replaces) or not rec_replaces,
             "attester": bool(attesters) or not claims_attested}
    o.add("PROV-06", "pass" if all(prov6.values()) else "fail",
          "carried in the structured source: " + ", ".join(k for k, v in prov6.items() if v)
          + ("; missing: " + ", ".join(k for k, v in prov6.items() if not v) if not all(prov6.values()) else ""))

    # ---- fidelity facets
    fid = fidelity.check(bundle, text_pages)
    entry_regions_missing = [m for m in fid.missing if m.get("entry", -1) >= 0]
    o.add("REN-02", "pass" if not fid.missing else "fail",
          f"{fid.expected} contract facts; {len(fid.missing)} not visible in canonical form"
          + (f"; first: {fid.missing[0]['element']} = {fid.missing[0]['text']!r}" if fid.missing else ""))
    anchored = _anchored(fid, bundle, text_pages)
    accuracy = [m for m in entry_regions_missing if (m["scope"], m["entry"]) in anchored]
    o.add("REN-10", "pass" if not accuracy else "fail",
          "every element of a shown entry is in its canonical form" if not accuracy else
          f"{len(accuracy)} element(s) of shown entries absent or not in canonical form; first: "
          f"{accuracy[0]['element']} expected {accuracy[0]['text']!r}")
    o.add("REN-11", "pass" if not fid.misplaced else "fail",
          "every element within its own entry and section" if not fid.misplaced else
          f"{len(fid.misplaced)} element(s) outside their entry or section; first: {fid.misplaced[0]['element']}")
    rec_issues = [m for m in fid.missing + fid.misplaced
                  if "/" in m.get("scope", "") or m.get("element") in ("section.text", "section.emptyReason",
                                                                        "section.title")]
    nested = sorted({f.scope for f in fidelity.expected_facts(bundle) if "/" in f.scope})
    o.add("REN-12", "pass" if not rec_issues else "fail",
          f"sections at every depth covered ({len(nested)} nested); narrative-only and empty sections shown"
          if not rec_issues else f"{len(rec_issues)} section-level or nested fact(s) not shown; first: "
          f"{rec_issues[0].get('scope')} {rec_issues[0].get('element')}")
    o.add("REN-08", "pass" if not fid.foreign_codes else "fail",
          "no code from another section or from no entry" if not fid.foreign_codes else
          f"unsupported codes: {fid.foreign_codes[:3]}")
    o.add("REN-01", "pass" if not fid.foreign_codes and not fid.misplaced else "fail",
          "no statement from outside the source detected (" + fidelity.LIMITATION + ")" if not fid.foreign_codes
          else "statements not supported by the source shown")
    o.add("IPS-05", "pass" if fid.passed else "fail", "fidelity matrix and canonical forms satisfied" if fid.passed
          else "see REN-02, REN-10, REN-11, REN-12")
    doc_missing = [m["element"] for m in fid.missing if m.get("scope") == "document"]
    prov3 = [e for e in doc_missing if e.startswith("Composition.") or e in ("Bundle.identifier", "assurance")]
    o.add("PROV-03", "pass" if not prov3 else "fail", "author, custodian, status, identifier and assurance shown"
          + (" with attester, mode and time" if attesters else "") if not prov3 else f"not shown: {prov3}")

    flat = re.sub(r"\s+", "", text_pages)
    visible = bool(ident.get("value")) and re.sub(r"\s+", "", ident["value"]) in flat
    embedded_note = "embedded" in text_pages.lower()
    o.add("REN-03", "pass" if visible and embedded_note else "fail",
          f"document identifier {'visible' if visible else 'not visible'}; embedding statement "
          f"{'present' if embedded_note else 'absent'}")
    lang = comp.get("language")
    xmp_lang = meta.get("dc:language")
    o.add("REN-07.a", "pass" if lang else "fail", f"Composition.language={lang}")
    o.add("REN-07.b", "pass" if lang and xmp_lang and lang in xmp_lang else "fail", f"XMP dc:language={xmp_lang}")
    # PROV-09: the five times are carried in distinct elements and are consistent.
    times = {"content (Composition.date)": comp.get("date"), "issuance (Bundle.timestamp)": bundle.get("timestamp")}
    problems9 = [k for k, v in times.items() if not v]
    if comp.get("date") and bundle.get("timestamp") and comp["date"] > bundle["timestamp"]:
        problems9.append("content time after issuance time")
    if record is not None and record.get("issued") and record["issued"] != bundle.get("timestamp"):
        problems9.append("record issue time differs from Bundle.timestamp")
    if attesters and not all(a.get("time") for a in attesters):
        problems9.append("attestation without a time")
    if evidence and attesters and evidence.get("time") and attesters[0].get("time") and \
            evidence["time"][:19].replace("+00:00", "") != attesters[0]["time"][:19]:
        problems9.append("attestation time differs between source and evidence")
    o.add("PROV-09", "pass" if not problems9 else "fail",
          "content, issuance and attestation times in their own elements" if not problems9 else f"{problems9}")
    # REN-14: populated elements with disposition N need separate assurance.
    unverified = fidelity.unverified_elements(bundle)
    o.add("REN-14", "not-tested" if unverified else "pass",
          f"elements not covered by automated verification are populated: {unverified[:4]}; separate assurance "
          "(inspection) is required" if unverified else "no element with disposition N is populated")
    xmp_id = meta.get("dc:identifier")
    o.add("ENV-09", "pass" if xmp_id == ident.get("value") else "fail", f"XMP dc:identifier={xmp_id}")

    # ---- issuer record obligations
    if record is None:
        for r in ("LIF-01", "SRC-04", "ENV-08", "PRES-01"):
            o.add(r, "not-tested", no_rec)
        return o
    needed = ["documentId", "seriesId", "version", "issued", "assurance", "envelopeSha256", "ipsSha256",
              "renderer", "validationEvidence", "provenance", "representation"]
    missing = [k for k in needed if not record.get(k)]
    if record.get("replaces") and not record.get("replacementReason"):
        missing.append("replacementReason")
    if rec_assurance == "attested-issuance" and not evidence:
        missing.append("attestationEvidence")
    o.add("LIF-01", "pass" if not missing else "fail", "issuance record complete" if not missing else
          f"issuance record lacks {missing}")
    ve = record.get("validationEvidence") or {}
    h7 = ve.get("hl7Validator") or {}
    o.add("SRC-04", "pass" if h7.get("status") == "passed" and h7.get("version") else "fail",
          f"HL7 FHIR validator {h7.get('version') or '(no version)'}: {h7.get('status', 'not run')}"
          + ("" if h7.get("status") == "passed" else " - issued without the content binding's validator"))
    pa = ve.get("pdfa") or {}
    o.add("ENV-08", "pass" if pa.get("status") == "passed" and pa.get("version") else "fail",
          f"PDF/A validator {pa.get('validator', '?')} {pa.get('version') or '(no version)'}: "
          f"{pa.get('status', 'not run')}")
    rep = record.get("representation") or {}
    terms = rep.get("terminologies") or []
    rep_missing = [k for k in ("ipsPackage", "fhirVersion", "pdfa", "renderer", "presentationResources")
                   if not rep.get(k)]
    if not terms or any(not t.get("version") for t in terms):
        rep_missing.append("terminology versions")
    o.add("PRES-01", "pass" if not rep_missing else "fail", "representation information complete" if not rep_missing
          else f"representation information lacks {rep_missing}")
    return o


def _anchored(fid: fidelity.FidelityResult, bundle: dict, text: str) -> set[tuple[str, int]]:
    """Entries whose first contract fact is visible in their section (the entry is shown)."""
    low = text.lower()
    out = set()
    first: dict[tuple[str, int], str] = {}
    for f in fidelity.expected_facts(bundle):
        if f.entry >= 0:
            first.setdefault((f.scope, f.entry), f.text)
    for k, t in first.items():
        if fidelity._has(low, t):
            out.add(k)
    return out


def _filespec(pdf: pikepdf.Pdf, filename: str):
    for spec in pdf.Root.get(Name.AF, []):
        if str(spec.get(Name.UF) or spec.get(Name.F)) == filename:
            return spec
    return None


STANDARD_ENCODINGS = {"/WinAnsiEncoding", "/MacRomanEncoding", "/StandardEncoding"}


def _unmapped_fonts(pdf: pikepdf.Pdf) -> list[str]:
    """ENV-10: a font has a Unicode mapping if it has a ToUnicode map, or is a simple font with a standard
    encoding and no Differences array."""
    out = []
    for page in pdf.pages:
        fonts = (page.obj.get(Name.Resources) or {}).get(Name.Font, {})
        for key, f in fonts.items():
            if Name.ToUnicode in f:
                continue
            enc = f.get(Name.Encoding)
            simple = str(f.get(Name.Subtype)) in ("/Type1", "/TrueType")
            if simple and enc is not None and str(enc) in STANDARD_ENCODINGS:
                continue
            if simple and enc is None and str(f.get(Name.BaseFont, "")).lstrip("/") in STANDARD_14:
                continue
            out.append(f"{key} {f.get(Name.BaseFont)}")
    return sorted(set(out))


STANDARD_14 = {"Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique", "Times-Roman",
               "Times-Bold", "Times-Italic", "Times-BoldItalic", "Courier", "Courier-Bold", "Courier-Oblique",
               "Courier-BoldOblique", "Symbol", "ZapfDingbats"}


# ------------------------------------------------------------------ claim method

def dependency_status(key: str, version: str | None, deps: dict) -> str:
    if version is None:
        return "unstated"
    if str(version) == _lock_version(deps[key]):
        return "exact"
    if any(a.get("key") == key and str(a.get("version")) == str(version) for a in deps["__permitted__"]):
        return "permitted-alternative"
    return "unsupported-deviation"


def claim_results(claim: dict, classes: set[str], options: set[str], deps: dict, catalogue: dict) -> _Obs:
    o = _Obs()
    req = ["profile", "profileVersion", "classes", "options", "bindings", "actorBindings"]
    missing = [k for k in req if k not in claim]
    ok = not missing and claim.get("profile") == CANONICAL_ID and claim.get("profileVersion") == PROFILE_VERSION
    em = claim.get("evidenceManifest") or {}
    o.add("CON-01", "pass" if ok and em else "fail",
          f"claim names {claim.get('profile')} {claim.get('profileVersion')}, classes {claim.get('classes')}, "
          f"options {claim.get('options')}, bindings; evidence manifest "
          f"{'referenced' if em else 'missing'}; the verdict of this report applies" if ok
          else f"claim lacks {missing} or names another profile/version")
    stated = claim.get("dependencies") or {}
    statuses = {}
    for k in (k for k in deps if not k.startswith("__")):
        v = stated.get(k)
        version = v.get("version") if isinstance(v, dict) else v
        statuses[k] = dependency_status(k, version, deps)
        declared = v.get("status") if isinstance(v, dict) else None
        if declared and declared != statuses[k]:
            statuses[k] = f"declared {declared} but is {statuses[k]}"
    val = claim.get("validators") or {}
    no_val = [k for k in ("hl7-validator", "verapdf") if not val.get(k)]
    unstated = [k for k, st in statuses.items() if st == "unstated" or st.startswith("declared")]
    o.add("CON-02.a", "pass" if not unstated and not no_val else "fail",
          f"dependency statuses: {statuses}" if not unstated and not no_val
          else f"versions or statuses missing or wrong: {unstated}; validator versions missing: {no_val}")
    deviations = {k: stated.get(k) for k, st in statuses.items() if st == "unsupported-deviation"}
    o.add("CON-02.b", "fail" if deviations else "pass",
          f"unsupported deviations (not eligible for a conformant verdict, whatever the reason recorded): {deviations}"
          if deviations else "every dependency is exact or a permitted alternative")
    text = " ".join(str(claim.get(k, "")) for k in ("statement", "description", "scope"))
    bad = FORBIDDEN_CLAIMS.search(text)
    o.add("CON-03", "fail" if bad else "pass", f"claim text presents a conformance claim to another specification: "
          f"{bad.group(0)!r}" if bad else "no claim of conformance to EHDS, MyHealth@EU, HL7 or IHE specifications")
    known = set(catalogue.get("options", {}))
    opts = claim.get("options")
    o.add("CON-04", "pass" if isinstance(opts, list) and set(opts) <= known else "fail",
          f"options listed: {opts}" if isinstance(opts, list) else "options not listed")
    ab = claim.get("actorBindings") or {}
    problems5a, need = [], set()
    for k in classes & set(BINDINGS):
        rule, table = BINDINGS[k]
        declared = ab.get(k) or []
        unknown = [b for b in declared if b not in table]
        if unknown:
            problems5a.append(f"{k}: unknown bindings {unknown}")
        chosen = [b for b in declared if b in table]
        if rule == "all" and set(chosen) != set(table):
            problems5a.append(f"{k}: requires {sorted(table)}")
        if rule == "any" and not chosen:
            problems5a.append(f"{k}: declares no binding")
        for b in (table if rule == "all" else chosen):
            need |= table[b]
    mhd = "MHD Document Responder" in (ab.get("Responder") or [])
    if "Responder" in classes and mhd != ("MHD" in options):
        problems5a.append("the MHD Document Responder binding and the MHD option must be declared together")
    o.add("CON-05.a", "pass" if not problems5a else "fail",
          f"actor bindings {ab}" if not problems5a else "; ".join(problems5a))
    tx = set(claim.get("transactions") or [])
    lacking = sorted(need - tx)
    o.add("CON-05.b", "pass" if not lacking else "fail", f"transactions of the declared bindings: {sorted(need) or 'none'}"
          if not lacking else f"transactions of declared bindings not supported: {lacking}")
    o.add("CHK-06", "pass" if {"release", "ruleCatalogueSha256", "vectors", "reports"} <= set(em) else "fail",
          f"evidence manifest fields: {sorted(em)}")
    decl = (claim.get("declarations") or {})

    def declared(key: str, fields: tuple[str, ...]) -> tuple[str, str]:
        d = decl.get(key) or {}
        ok = all(d.get(f) for f in fields)
        return ("pass" if ok else "fail", f"declaration: {d}" if d else "no declaration")

    o.add("PRES-04", *declared("PRES-04", ("party", "frequency")))
    o.add("PRES-05.a", *declared("PRES-05", ("mechanism", "independentParty")))
    o.add("SEC-01.a", *declared("SEC-01", ("authorisation",)))
    o.add("SEC-01.b", *declared("SEC-01", ("inventoryShown",)))
    o.add("SEC-02", *declared("SEC-02", ("identification", "authentication", "consent", "audit", "lawfulBasis")))
    o.add("SEC-03", *declared("SEC-03", ("clinicalRiskManagement",)))
    over = re.search(r"semantic(ally)? equivalen|clinically safe|clinical safety (is )?(established|assured)", text, re.I)
    o.add("REN-13", "fail" if over else "pass", f"claim overstates automated checks: {over.group(0)!r}" if over
          else "claim does not present automated checks as semantic equivalence or clinical safety")
    return o


def _lock_version(d: dict) -> str:
    return d["version"]


def inspection_results(claim: dict | None, catalogue: dict) -> _Obs:
    o = _Obs()
    for oid, rec in ((claim or {}).get("inspections") or {}).items():
        if oid not in catalogue["obligations"]:
            if catalogue["ruleObligations"].get(oid) == [oid]:
                pass
            elif oid in catalogue["ruleObligations"]:
                raise CatalogueError(f"inspection of {oid}: name an obligation of the rule "
                                     f"({catalogue['ruleObligations'][oid]})")
            else:
                raise CatalogueError(f"inspection of unknown obligation {oid}")
        ok = rec.get("result") == "pass" and rec.get("inspector") and rec.get("date")
        o.add(oid, "pass" if ok else "fail",
              f"declared inspection by {rec.get('inspector')} on {rec.get('date')}: {rec.get('result')} "
              f"({rec.get('method', 'method not stated')}); not verified by the Checker")
    return o


def evidence_results(files: list[dict], catalogue: dict) -> dict[str, _Obs]:
    """Merge live-scenario and vector result files: {"method": "live"|"vectors", "results": {obligation: {...}}}.
    Unknown obligations, and rule ids of rules with several obligations, are rejected (CHK-10)."""
    out: dict[str, _Obs] = {}
    for f in files:
        if f.get("method") not in ("live", "vectors"):
            raise CatalogueError(f"evidence file with unknown method {f.get('method')!r}")
        obs = out.setdefault(f["method"], _Obs())
        for oid, r in f.get("results", {}).items():
            if oid not in catalogue["obligations"]:
                hint = f" (rule with obligations {catalogue['ruleObligations'][oid]})" \
                    if oid in catalogue["ruleObligations"] else ""
                raise CatalogueError(f"{f.get('source')}: result for unknown obligation {oid}{hint}")
            if r.get("outcome") not in OUTCOMES:
                raise CatalogueError(f"{f.get('source')}: {oid} has undefined outcome {r.get('outcome')!r}")
            prev = obs.get(oid)
            if prev and prev[0] == "fail":
                continue
            obs.add(oid, r["outcome"], f"{r.get('evidence', '')} [{', '.join(r.get('scenarios', [])) or f['method']}]")
    return out


def evaluate(catalogue: dict, classes: set[str], options: set[str], methods: dict[str, _Obs]) -> list[Result]:
    scope = catalogue["methodScope"]
    out = []
    for oid, ob in catalogue["obligations"].items():
        if not applies(ob, classes):
            continue
        res = Result(oid, "not-tested", "", ob["level"], ob["level"] in MANDATORY, ruleId=ob["rule"])
        ok, why = option_applies(ob, options)
        if not ok:
            res.outcome, res.evidence = "not-applicable", why
            out.append(res)
            continue
        req, uncovered = required_methods(ob, classes, scope)
        inspected = methods.get("inspection", {}).get(oid) if "inspection" in ob["verification"] else None
        outcomes = []
        for m in sorted(req):
            got = methods.get(m, {}).get(oid)
            if got is None:
                got = ("not-tested", {"artefact": "no artefact supplied or not evaluated by the artefact method",
                                      "claim": "no conformance claim supplied", "live": "no live-scenario result",
                                      "vectors": "no vector-run result",
                                      "inspection": "no inspection declared in the claim"}[m])
            if got[0] == "not-tested" and inspected and m != "inspection":
                # The obligation names inspection as its separate assurance (for example REN-14).
                got = (inspected[0], f"{got[1]}; covered by {inspected[1]}")
            res.methods[m] = {"outcome": got[0], "evidence": got[1]}
            outcomes.append(got[0])
        if uncovered:
            res.methods["coverage"] = {"outcome": "not-tested",
                                       "evidence": f"no verification method covers class(es) {uncovered}"}
            outcomes.append("not-tested")
        if "fail" in outcomes:
            res.outcome = "fail"
        elif outcomes and all(x == "not-applicable" for x in outcomes):
            res.outcome = "not-applicable"
        elif outcomes and all(x in ("pass", "not-applicable") for x in outcomes):
            res.outcome = "pass"
        else:
            res.outcome = "not-tested"
        res.evidence = " | ".join(f"{m}: {v['evidence']}" for m, v in res.methods.items())
        out.append(res)
    return out


def verdict(results: list[Result]) -> tuple[str, list[str]]:
    """CHK-07: evaluate each applicable obligation by its declared level."""
    warnings = [f"{r.rule} ({r.level}, advisory) failed: {r.evidence}" for r in results
                if r.outcome == "fail" and not r.mandatory]
    if any(r.outcome == "fail" and r.mandatory for r in results):
        return "non-conformant", warnings
    if any(r.outcome == "not-tested" and r.mandatory for r in results):
        return "incomplete", warnings
    return "conformant", warnings


def check(pdf_bytes: bytes | None = None, *, projection: bytes | None = None, record: dict | None = None,
          settings: Settings | None = None, name: str = "envelope.pdf", classes=("Envelope",), options=(),
          claim: dict | None = None, evidence: list[dict] | None = None,
          catalogue_path: str | None = None, catalogue: dict | None = None) -> Report:
    settings = settings or Settings()
    cat = catalogue or load_catalogue(catalogue_path)
    deps = load_dependencies()
    classes, options = set(classes), set(options)
    unknown = (classes - set(cat["classes"])) | (options - set(cat["options"]))
    if unknown:
        raise CatalogueError(f"unknown classes or options claimed: {sorted(unknown)}")
    vera, hl7 = VeraPdf(settings), Hl7FhirValidator(settings)
    rep = Report(
        profile=CANONICAL_ID, profileName=PROFILE, profileVersion=PROFILE_VERSION,
        ruleCatalogue={"profileVersion": cat["profileVersion"], "sha256": cat.get("sha256"),
                       "rules": len(cat["rules"]), "obligations": len(cat["obligations"])},
        checker=CHECKER, independence=INDEPENDENCE,
        claimed={"classes": sorted(classes), "options": sorted(options),
                 "actorBindings": (claim or {}).get("actorBindings")},
        tested={"artefact": name, "sha256": _sha256(pdf_bytes) if pdf_bytes else None,
                "projection": bool(projection), "issuanceRecord": bool(record), "claim": bool(claim)},
        validators={"pdfa": f"veraPDF (configured; locked version {deps['verapdf']['version']})"
                    if vera.configured() else "not configured",
                    "fhir": f"HL7 FHIR validator (configured; locked version {deps['hl7-validator']['version']})"
                    if hl7.available() else "not configured"},
        testedAt=datetime.now(UTC).isoformat(timespec="seconds"),
        limitations=[fidelity.LIMITATION, INDEPENDENCE,
                     "Obligations verified by declared inspection are reported as declared; the Checker does not "
                     "verify them (CHK-08)."])
    if cat["profileVersion"] != PROFILE_VERSION:
        raise CatalogueError(f"rule catalogue is for profile {cat['profileVersion']}, Checker implements "
                             f"{PROFILE_VERSION}")
    methods: dict[str, _Obs] = {}
    if pdf_bytes is not None:
        art = artefact_results(pdf_bytes, projection, record, settings, vera, hl7)
        bad = [k for k in art if k not in cat["obligations"]]
        if bad:
            raise CatalogueError(f"artefact method produced results for unknown obligations {bad}")
        methods["artefact"] = art
    if claim is not None:
        methods["claim"] = claim_results(claim, classes, options, deps, cat)
        methods["inspection"] = inspection_results(claim, cat)
    for m, obs in evidence_results(evidence or [], cat).items():
        methods.setdefault(m, _Obs()).update(obs)
    rep.evidenceSources = [{"method": f["method"], "source": f.get("source"), "generatedAt": f.get("generatedAt")}
                           for f in (evidence or [])]
    rep.results = evaluate(cat, classes, options, methods)
    rep.verdict, rep.warnings = verdict(rep.results)
    return rep


def check_envelope(pdf_bytes: bytes, *, projection: bytes | None = None, record: dict | None = None,
                   settings: Settings | None = None, name: str = "envelope.pdf", **kw) -> Report:
    """Envelope-class check of one artefact (the 0.2.0 entry point, kept for compatibility)."""
    return check(pdf_bytes, projection=projection, record=record, settings=settings, name=name, **kw)


def issuance_record_from_demo(iss: dict) -> dict:
    """Map a demonstrator issuance to the profile's issuance-record fields (LIF-01)."""
    return {"documentId": iss["documentUrn"], "seriesId": iss.get("seriesId"), "version": iss["version"],
            "issued": iss["issued"], "assurance": iss.get("assurance"),
            "attestationEvidence": iss.get("attestationEvidence"),
            "envelopeSha256": iss["envelope"]["sha256"], "ipsSha256": iss["ips"]["sha256"],
            "renderer": iss.get("renderer"), "validationEvidence": iss.get("validationEvidence"),
            "representation": iss.get("representation"), "provenance": iss.get("provenance"),
            "replaces": iss.get("replaces"), "replacementReason": iss.get("replacementReason")}


def main(args) -> int:
    pdf = Path(args.envelope).read_bytes() if args.envelope else None
    proj = Path(args.projection).read_bytes() if args.projection else None
    rec = json.loads(Path(args.record).read_text()) if args.record else None
    claim = json.loads(Path(args.claim).read_text()) if args.claim else None
    ev = [json.loads(Path(f).read_text()) for f in (args.evidence or [])]
    classes = args.cls or (claim or {}).get("classes") or ["Envelope"]
    options = args.option or (claim or {}).get("options") or []
    rep = check(pdf, projection=proj, record=rec, name=Path(args.envelope).name if args.envelope else "(none)",
                classes=classes, options=options, claim=claim, evidence=ev)
    out = json.dumps(rep.to_dict(), indent=2)
    if args.out:
        Path(args.out).write_text(out + "\n")
    print(out if not args.out else f"verdict {rep.verdict}; {rep.summary()} -> {args.out}")
    return {"conformant": 0, "incomplete": 2}.get(rep.verdict, 1)

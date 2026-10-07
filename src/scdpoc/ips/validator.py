"""IPS validation: an offline pre-flight plus the authoritative HL7 FHIR validator.

Two engines, deliberately kept separate:

* ``BuiltinIpsPreflight`` - fast, offline, always runs. Checks FHIR document
  semantics (AT-02), self-contained references, IPS required sections and the
  top-level required elements of each IPS profile, using a digest generated
  from the CC0 HL7 IPS package (config/ips-profile-digest.json). It is a
  pre-flight, not a conformance validator: it does not evaluate slicing,
  value-set bindings or FHIRPath invariants.

* ``Hl7FhirValidator`` - wraps the official HL7 FHIR validator
  (validator_cli.jar) against the pinned IPS package. This is the evidence for
  AT-01. It runs in CI and in the optional full-validation setup; when it is
  not configured the report says so explicitly rather than implying a pass.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from ..config import Settings

XHTML_NS = "http://www.w3.org/1999/xhtml"
INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
REQUIRED_SECTIONS = {"11450-4": "Problems", "48765-2": "Allergies and intolerances", "10160-0": "Medication summary"}
SECTION_TARGETS = {
    "11450-4": {"Condition"},
    "48765-2": {"AllergyIntolerance"},
    "10160-0": {"MedicationStatement", "MedicationRequest"},
    "11369-6": {"Immunization"},
    "30954-2": {"Observation", "DiagnosticReport"},
}


@dataclass
class Issue:
    severity: str            # error | warning | information
    rule: str
    location: str
    message: str
    engine: str = "builtin-preflight"


@dataclass
class EngineResult:
    engine: str
    status: str              # passed | failed | not-run
    detail: str = ""
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity in {"error", "fatal"})


@dataclass
class ValidationReport:
    package: str
    preflight_digest_source: str
    engines: list[EngineResult]
    publishable: bool
    gate: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for e, eng in zip(d["engines"], self.engines, strict=True):
            e["errors"] = eng.errors
            e["warnings"] = sum(1 for i in eng.issues if i.severity == "warning")
        return d


def _get_choice(resource: dict[str, Any], path: str) -> Any:
    if path.endswith("[x]"):
        stem = path[:-3]
        return next((v for k, v in resource.items() if k.startswith(stem) and k[len(stem):][:1].isupper()), None)
    return resource.get(path)


class BuiltinIpsPreflight:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.digest = settings.profile_digest

    def run(self, bundle: dict[str, Any]) -> EngineResult:
        issues: list[Issue] = []

        def err(rule: str, loc: str, msg: str, sev: str = "error") -> None:
            issues.append(Issue(sev, rule, loc, msg))

        if bundle.get("resourceType") != "Bundle":
            err("DOC-1", "Bundle", "root resource is not a Bundle")
            return EngineResult("builtin-preflight", "failed", issues=issues)
        if bundle.get("type") != "document":
            err("DOC-1", "Bundle.type", "Bundle.type must be 'document'")
        ident = bundle.get("identifier") or {}
        if not ident.get("system") or not ident.get("value"):
            err("DOC-2", "Bundle.identifier", "document Bundle requires identifier.system and identifier.value")
        if not INSTANT.match(str(bundle.get("timestamp", ""))):
            err("DOC-2", "Bundle.timestamp", "document Bundle requires a timestamp (instant)")

        entries = bundle.get("entry") or []
        if not entries or entries[0].get("resource", {}).get("resourceType") != "Composition":
            err("DOC-3", "Bundle.entry[0]", "first entry must be the IPS Composition")
            return EngineResult("builtin-preflight", "failed", issues=issues)

        index: dict[str, dict[str, Any]] = {}
        for i, e in enumerate(entries):
            fu = e.get("fullUrl")
            if not fu:
                err("DOC-4", f"Bundle.entry[{i}]", "entry has no fullUrl")
                continue
            if fu in index:
                err("DOC-4", f"Bundle.entry[{i}]", f"duplicate fullUrl {fu}")
            index[fu] = e.get("resource", {})
            rid = e.get("resource", {}).get("id")
            if fu.startswith("urn:uuid:") and rid and rid != fu[9:]:
                err("DOC-4", f"Bundle.entry[{i}]", "resource.id does not match fullUrl uuid", "warning")

        # Every reference must resolve inside the document (self-contained document).
        def walk(node: Any, loc: str) -> None:
            if isinstance(node, dict):
                ref = node.get("reference")
                if isinstance(ref, str) and ref not in index:
                    err("DOC-5", loc, f"reference {ref} does not resolve within the document")
                for k, v in node.items():
                    if k != "text":
                        walk(v, f"{loc}.{k}")
            elif isinstance(node, list):
                for n, v in enumerate(node):
                    walk(v, f"{loc}[{n}]")

        for i, e in enumerate(entries):
            walk(e.get("resource", {}), f"Bundle.entry[{i}].resource")

        patients = [r for r in index.values() if r.get("resourceType") == "Patient"]
        if len(patients) != 1:
            err("IPS-B1", "Bundle.entry", "IPS Bundle must contain exactly one Patient")

        # Profile digest: top-level required elements.
        profiles = self.digest["profiles"]
        self._check_required(bundle, profiles["Bundle"], "Bundle", issues)
        for i, e in enumerate(entries):
            res = e.get("resource", {})
            rt = res.get("resourceType")
            key = "Observation-lab" if rt == "Observation" else rt
            prof = profiles.get(key)
            if prof:
                self._check_required(res, prof, f"Bundle.entry[{i}].resource({rt})", issues)
                if prof["url"] not in (res.get("meta", {}).get("profile") or []):
                    err("PROF-2", f"Bundle.entry[{i}].resource.meta",
                        f"meta.profile does not claim {prof['url']}", "information")

        comp = entries[0]["resource"]
        self._check_composition(comp, index, issues)

        # Synthetic namespace guard.
        ns = self.settings.affinity_domain["affinity_domain"]["patient_assigning_authority"]
        for p in patients:
            ids = p.get("identifier") or []
            if not any(x.get("system") == ns["fhir_system"] and str(x.get("value", "")).startswith(ns["value_prefix"])
                       for x in ids):
                err("SAFE-1", "Patient.identifier", "patient identifier is not in the synthetic namespace")

        status = "failed" if any(i.severity == "error" for i in issues) else "passed"
        return EngineResult("builtin-preflight", status,
                            detail=f"profile digest from {self.digest['source']['package']}#"
                                   f"{self.digest['source']['version']}", issues=issues)

    @staticmethod
    def _check_required(res: dict[str, Any], prof: dict[str, Any], loc: str, issues: list[Issue]) -> None:
        for req in prof["required"]:
            v = _get_choice(res, req["path"])
            n = len(v) if isinstance(v, list) else (0 if v in (None, "", {}) else 1)
            if n < req["min"]:
                issues.append(Issue("error", "PROF-1", f"{loc}.{req['path']}",
                                    f"minimum cardinality {req['min']} not met (found {n})"))

    def _check_composition(self, comp: dict[str, Any], index: dict[str, Any], issues: list[Issue]) -> None:
        def err(rule: str, loc: str, msg: str, sev: str = "error") -> None:
            issues.append(Issue(sev, rule, loc, msg))

        codings = (comp.get("type") or {}).get("coding") or []
        if not any(c.get("system") == "http://loinc.org" and c.get("code") == "60591-5" for c in codings):
            err("IPS-C1", "Composition.type", "Composition.type must be LOINC 60591-5 (Patient summary Document)")
        subj = comp.get("subject", {}).get("reference")
        if index.get(subj, {}).get("resourceType") != "Patient":
            err("IPS-C2", "Composition.subject", "subject must reference the Patient in the document")

        present = {}
        for s_i, sec in enumerate(comp.get("section") or []):
            loc = f"Composition.section[{s_i}]"
            code = next((c.get("code") for c in (sec.get("code") or {}).get("coding", [])
                         if c.get("system") == "http://loinc.org"), None)
            present[code] = sec
            if not sec.get("title"):
                err("IPS-S1", loc, "section.title is required")
            has_entry = bool(sec.get("entry"))
            if has_entry and sec.get("emptyReason"):
                err("cmp-2", loc, "a section can only have an emptyReason if it is empty")
            if code in REQUIRED_SECTIONS and not has_entry and not sec.get("emptyReason"):
                err("ips-comp-1", loc, "either section.entry or emptyReason must be present")
            div = (sec.get("text") or {}).get("div")
            if not div:
                err("IPS-S2", loc + ".text", "section narrative (text.div) is required")
            else:
                self._check_xhtml(div, loc + ".text.div", issues)
            allowed = SECTION_TARGETS.get(code)
            for e_i, ref in enumerate(sec.get("entry") or []):
                target = index.get(ref.get("reference"), {}).get("resourceType")
                if allowed and target not in allowed:
                    err("IPS-S3", f"{loc}.entry[{e_i}]",
                        f"{target} is not an allowed entry type for section {code}")
        for code, name in REQUIRED_SECTIONS.items():
            if code not in present:
                err("IPS-S0", "Composition.section", f"required IPS section missing: {name} ({code})")

    @staticmethod
    def _check_xhtml(div: str, loc: str, issues: list[Issue]) -> None:
        try:
            root = etree.fromstring(div.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            issues.append(Issue("error", "txt-1", loc, f"narrative is not well-formed XHTML: {exc}"))
            return
        if root.tag != f"{{{XHTML_NS}}}div":
            issues.append(Issue("error", "txt-1", loc, "narrative root must be an XHTML div"))
        for el in root.iter():
            local = etree.QName(el).localname if isinstance(el.tag, str) else ""
            if local in {"script", "object", "applet", "form", "iframe"}:
                issues.append(Issue("error", "txt-1", loc, f"narrative contains forbidden element <{local}>"))
            for attr in el.attrib:
                if attr.lower().startswith("on"):
                    issues.append(Issue("error", "txt-2", loc, f"narrative contains event attribute {attr}"))


class Hl7FhirValidator:
    """Runs the HL7 FHIR validator CLI against the pinned IPS package."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jar = settings.hl7_validator_jar

    def available(self) -> bool:
        return bool(self.jar and Path(self.jar).is_file() and shutil.which("java"))

    def run(self, json_bytes: bytes) -> EngineResult:
        pin = self.settings.ips_package
        ig = f"{pin['ips']['package']}#{pin['ips']['version']}"
        if not self.available():
            return EngineResult("hl7-fhir-validator", "not-run",
                                detail="validator_cli.jar not configured (set SCDPOC_HL7_VALIDATOR_JAR); "
                                       f"CI runs it against {ig}")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ips.json"
            out = Path(tmp) / "outcome.json"
            src.write_bytes(json_bytes)
            cmd = ["java", "-jar", self.jar, str(src), "-version", pin["fhir"]["version"], "-ig", ig,
                   "-profile", pin["ips"]["bundle_profile"], "-output", str(out)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
            if not out.is_file():
                return EngineResult("hl7-fhir-validator", "failed",
                                    detail=f"validator produced no output (exit {proc.returncode}): "
                                           f"{proc.stderr[-500:]}")
            outcome = json.loads(out.read_text(encoding="utf-8"))
        issues = []
        for iss in outcome.get("issue", []):
            loc = ", ".join(iss.get("expression") or iss.get("location") or [])
            msg = (iss.get("details") or {}).get("text") or iss.get("diagnostics", "")
            issues.append(Issue(iss.get("severity", "error"), iss.get("code", ""), loc, msg,
                                engine="hl7-fhir-validator"))
        status = "failed" if any(i.severity in {"error", "fatal"} for i in issues) else "passed"
        return EngineResult("hl7-fhir-validator", status, detail=f"validated against {ig}", issues=issues)


def validate_ips(bundle: dict[str, Any], json_bytes: bytes, settings: Settings) -> ValidationReport:
    pre = BuiltinIpsPreflight(settings).run(bundle)
    ext = Hl7FhirValidator(settings).run(json_bytes)
    publishable = pre.status == "passed" and ext.status != "failed"
    gate = "pre-flight passed; HL7 validator not run" if ext.status == "not-run" else f"HL7 validator {ext.status}"
    if settings.require_hl7_validator and ext.status != "passed":
        publishable = False
        gate = "HL7 FHIR validator is required (SCDPOC_REQUIRE_HL7_VALIDATOR) and did not pass"
    if pre.status != "passed":
        gate = "pre-flight failed: structural errors must be fixed before publication"
    pin = settings.ips_package["ips"]
    d = settings.profile_digest["source"]
    return ValidationReport(
        package=f"{pin['package']}#{pin['version']}",
        preflight_digest_source=f"{d['package']}#{d['version']} (sha256 {d['tarball_sha256'][:16]}…)",
        engines=[pre, ext], publishable=publishable, gate=gate)

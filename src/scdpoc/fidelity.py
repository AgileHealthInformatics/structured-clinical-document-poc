"""Rendition fidelity contract (profile 0.3.0, IPS binding: fidelity matrix and canonical text forms).

Derives the facts a rendition must show *directly from the FHIR resources*, independently of the
renderer's view model (ips/view.py) - reusing the renderer's own view to check the renderer would be
circular. Canonical text forms are implemented here separately from the renderer on purpose.

The check reports four facets (profile REN-02, REN-08, REN-10):
  * coverage  - every populated contract element is visible;
  * accuracy  - it is visible in its canonical text form (a changed value no longer matches);
  * context   - it appears inside its own section and inside its own entry's region;
  * unsupported assertions - codes in a section that belong to another section or to no entry.

Automated text comparison cannot establish full clinical semantic equivalence; results say so.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from html import unescape
from typing import Any

LIMITATION = ("Text-based checking establishes coverage, accuracy of canonical forms, section and entry context, "
              "and absence of foreign codes. It cannot establish complete clinical semantic equivalence.")

SECTION_SYSTEM_PATTERNS = {
    "11450-4": re.compile(r"\b\d{6,18}\b"),                      # SNOMED CT concept ids
    "48765-2": re.compile(r"\b\d{6,18}\b"),
    "10160-0": re.compile(r"\b[A-Z]\d{2}[A-Z]{1,2}\d{0,2}\b"),    # WHO ATC
    "11369-6": re.compile(r"\b[A-Z]\d{2}[A-Z]{1,2}\d{0,2}\b"),
    "30954-2": re.compile(r"\b\d{1,5}-\d\b"),                    # LOINC
}
UNIT_WORDS = {"s": "second", "min": "minute", "h": "hour", "d": "day", "wk": "week", "mo": "month", "a": "year"}


@dataclass
class Fact:
    scope: str           # "document" or a section path "11450-4" / "parent/child"
    entry: int           # entry index within the section, -1 for section/document facts
    element: str
    text: str


@dataclass
class FidelityResult:
    expected: int
    missing: list[dict] = field(default_factory=list)       # coverage / accuracy
    misplaced: list[dict] = field(default_factory=list)     # context (wrong section or wrong entry)
    foreign_codes: list[dict] = field(default_factory=list)  # unsupported assertions
    limitation: str = LIMITATION

    @property
    def passed(self) -> bool:
        return not (self.missing or self.misplaced or self.foreign_codes)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["passed"] = self.passed
        d["facets"] = {"coverageAndAccuracy": "fail" if self.missing else "pass",
                       "context": "fail" if self.misplaced else "pass",
                       "unsupportedAssertions": "fail" if self.foreign_codes else "pass"}
        return d


# ---------------------------------------------------------------- canonical text forms (spec table)

_WHEN = {"MORN": "in the morning", "AFT": "in the afternoon", "EVE": "in the evening", "NIGHT": "at night",
         "HS": "at bedtime", "WAKE": "on waking", "C": "with meals", "AC": "before meals", "PC": "after meals"}


def _num(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def canon_quantity(q: dict | None) -> str:
    if not q or q.get("value") is None:
        return ""
    return f"{_num(q['value'])} {q.get('unit') or q.get('code') or ''}".strip()


def canon_range(r: dict | None) -> str:
    if not r or not r.get("low") or not r.get("high"):
        return ""
    return f"{_num(r['low'].get('value'))} to {canon_quantity(r['high'])}"


def canon_ratio(r: dict | None) -> str:
    if not r or not r.get("numerator") or not r.get("denominator"):
        return ""
    return f"{canon_quantity(r['numerator'])} per {canon_quantity(r['denominator'])}"


def canon_period(p: dict | None) -> str:
    if not p:
        return ""
    return " ".join(x for x in ((f"from {p['start']}" if p.get("start") else ""),
                                (f"to {p['end']}" if p.get("end") else "")) if x)


def canon_timing_parts(t: dict | None) -> list[tuple[str, str]]:
    r = (t or {}).get("repeat") or {}
    out = []
    if r.get("frequency") and r.get("period"):
        f = r["frequency"]
        word = UNIT_WORDS.get(r.get("periodUnit", ""), r.get("periodUnit", ""))
        per = f"per {word}" if _num(r["period"]) == "1" else f"per {_num(r['period'])} {word}s"
        if r.get("frequencyMax"):
            out.append(("timing.repeat.frequency", f"{f} to {r['frequencyMax']} times {per}"))
        else:
            times = "once" if f == 1 else "twice" if f == 2 else f"{f} times"
            out.append(("timing.repeat.frequency", f"{times} {per}"))
    out += [("timing.repeat.when", _WHEN.get(w, w)) for w in r.get("when", [])]
    out += [("timing.repeat.timeOfDay", f"at {x[:5]}") for x in r.get("timeOfDay", [])]
    if r.get("boundsDuration"):
        out.append(("timing.repeat.bounds", f"for {canon_quantity(r['boundsDuration'])}"))
    elif r.get("boundsPeriod"):
        out.append(("timing.repeat.bounds", canon_period(r["boundsPeriod"])))
    return out


def canon_timing(t: dict | None) -> str:
    """Frequency form only (kept for callers of 0.3.0)."""
    parts = dict(canon_timing_parts(t))
    return parts.get("timing.repeat.frequency", "")


def _cc(cc: dict | None) -> tuple[str, str]:
    if not cc:
        return "", ""
    c = (cc.get("coding") or [{}])[0]
    return (cc.get("text") or c.get("display") or c.get("code") or ""), c.get("code", "")


def _code(cc: dict | None) -> str:
    return _cc(cc)[1]


def _plain(div: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", div or "")).split())


def _name(res: dict) -> str:
    if res.get("resourceType") == "Organization":
        return res.get("name", "")
    if res.get("resourceType") == "Device":
        return ((res.get("deviceName") or [{}])[0]).get("name", "")
    n = (res.get("name") or [{}])[0]
    return n.get("text") or " ".join([*n.get("prefix", []), *n.get("given", []), n.get("family", "")]).strip()


def _choice(res: dict, prefix: str) -> str:
    for k, v in res.items():
        if k.startswith(prefix) and k != prefix:
            kind = k[len(prefix):]
            if kind in ("DateTime", "String"):
                return v
            if kind == "Age":
                return canon_quantity(v)
            if kind == "Period":
                return canon_period(v)
    return ""


def _value(obj: dict) -> str:
    if obj.get("valueQuantity"):
        return canon_quantity(obj["valueQuantity"])
    if obj.get("valueCodeableConcept"):
        return _cc(obj["valueCodeableConcept"])[0]
    if "valueString" in obj:
        return obj["valueString"]
    if "valueBoolean" in obj:
        return "yes" if obj["valueBoolean"] else "no"
    if obj.get("valueRange"):
        return canon_range(obj["valueRange"])
    return ""


# ---------------------------------------------------------------- coverage matrix (dispositions R and E)

def _dosage_facts(add, prefix: str, d: dict) -> None:
    add(f"{prefix}.text", d.get("text"))
    for a in d.get("additionalInstruction", []):
        add(f"{prefix}.additionalInstruction", _cc(a)[0])
    add(f"{prefix}.patientInstruction", d.get("patientInstruction"))
    for k in ("route", "site", "method"):
        add(f"{prefix}.{k}", _cc(d.get(k))[0])
    for dr in d.get("doseAndRate", []):
        if dr.get("doseQuantity"):
            q = canon_quantity(dr["doseQuantity"])
            add(f"{prefix}.doseAndRate.doseQuantity", f"dose {q}" if q else "")
        elif dr.get("doseRange"):
            q = canon_range(dr["doseRange"])
            add(f"{prefix}.doseAndRate.doseRange", f"dose {q}" if q else "")
        if dr.get("rateQuantity"):
            add(f"{prefix}.doseAndRate.rateQuantity", f"rate {canon_quantity(dr['rateQuantity'])}")
        elif dr.get("rateRatio"):
            add(f"{prefix}.doseAndRate.rateRatio", f"rate {canon_ratio(dr['rateRatio'])}")
    if d.get("maxDosePerPeriod"):
        add(f"{prefix}.maxDosePerPeriod", f"max {canon_ratio(d['maxDosePerPeriod'])}")
    if d.get("maxDosePerAdministration"):
        add(f"{prefix}.maxDosePerAdministration", f"max {canon_quantity(d['maxDosePerAdministration'])} per dose")
    for el, txt in canon_timing_parts(d.get("timing")):
        add(f"{prefix}.{el}", txt)
    if d.get("asNeededBoolean"):
        add(f"{prefix}.asNeeded", "as needed")
    elif d.get("asNeededCodeableConcept"):
        add(f"{prefix}.asNeeded", f"as needed for {_cc(d['asNeededCodeableConcept'])[0]}")


def entry_facts(res: dict) -> list[tuple[str, str]]:
    """Contract elements for one entry resource: (element, canonical text), only where populated."""
    rt = res["resourceType"]
    out: list[tuple[str, str]] = []

    def add(element: str, value: Any) -> None:
        if value not in (None, ""):
            out.append((f"{rt}.{element}", str(value)))

    def notes() -> None:
        for n in res.get("note", []):
            add("note", n.get("text"))

    if rt == "Condition":
        disp, code = _cc(res.get("code"))
        add("code.display", disp)
        add("code.code", code)
        add("clinicalStatus", _code(res.get("clinicalStatus")))
        add("verificationStatus", _code(res.get("verificationStatus")))
        add("severity", _cc(res.get("severity"))[0])
        for b in res.get("bodySite", []):
            add("bodySite", _cc(b)[0])
        add("onset", _choice(res, "onset"))
        ab = _choice(res, "abatement")
        if ab or res.get("abatementBoolean"):
            add("abatement", f"resolved {ab}".strip())
        notes()
    elif rt == "AllergyIntolerance":
        disp, code = _cc(res.get("code"))
        add("code.display", disp)
        add("code.code", code)
        add("clinicalStatus", _code(res.get("clinicalStatus")))
        add("verificationStatus", _code(res.get("verificationStatus")))
        add("type", res.get("type"))
        for c in res.get("category", []):
            add("category", c)
        add("criticality", res.get("criticality"))
        on = _choice(res, "onset")
        add("onset", f"onset {on}" if on else "")
        for r in res.get("reaction", []):
            if r.get("substance"):
                add("reaction.substance", _cc(r["substance"])[0])
            for m in r.get("manifestation", []):
                add("reaction.manifestation", _cc(m)[0])
            add("reaction.severity", r.get("severity"))
        notes()
    elif rt in ("MedicationStatement", "MedicationRequest"):
        disp, code = _cc(res.get("medicationCodeableConcept"))
        add("medication.display", disp)
        add("medication.code", code)
        add("status", res.get("status"))
        if rt == "MedicationRequest":
            add("intent", res.get("intent"))
            for d in res.get("dosageInstruction", []):
                _dosage_facts(add, "dosageInstruction", d)
            add("authoredOn", res.get("authoredOn"))
        else:
            for d in res.get("dosage", []):
                _dosage_facts(add, "dosage", d)
            add("effective", (res.get("effectivePeriod") or {}).get("start") or res.get("effectiveDateTime"))
        notes()
    elif rt == "Immunization":
        disp, code = _cc(res.get("vaccineCode"))
        add("vaccineCode.display", disp)
        add("vaccineCode.code", code)
        add("status", res.get("status"))
        add("statusReason", _cc(res.get("statusReason"))[0])
        add("occurrence", res.get("occurrenceDateTime") or res.get("occurrenceString"))
        for pa in res.get("protocolApplied", []):
            n = pa.get("doseNumberPositiveInt", pa.get("doseNumberString"))
            if n is not None:
                add("protocolApplied.doseNumber", f"dose {n}")
        notes()
    elif rt == "Observation":
        disp, code = _cc(res.get("code"))
        add("code.display", disp)
        add("code.code", code)
        add("status", res.get("status"))
        add("value", _value(res))
        for i in res.get("interpretation", []):
            add("interpretation", _cc(i)[0])
        add("effective", res.get("effectiveDateTime"))
        for rr in res.get("referenceRange", []):
            if rr.get("text"):
                add("referenceRange", rr["text"])
            elif rr.get("low") and rr.get("high"):
                add("referenceRange", f"ref {_num(rr['low'].get('value'))} to {canon_quantity(rr['high'])}")
        for c in res.get("component", []):
            add("component", f"{_cc(c.get('code'))[0]} {_value(c)}".strip())
        notes()
    else:
        add("text", _plain((res.get("text") or {}).get("div", "")) or rt)
    return out


# Disposition N (coverage matrix): not covered by automated verification (REN-14).
N_ELEMENTS = {
    "Condition": ["stage", "evidence"],
    "AllergyIntolerance": ["lastOccurrence", "reaction.exposureRoute"],
    "MedicationStatement": ["statusReason", "reasonCode", "dosage.timing.repeat.duration", "dosage.timing.repeat.count",
                            "dosage.maxDosePerLifetime"],
    "MedicationRequest": ["dispenseRequest", "substitution", "reasonCode", "dosageInstruction.maxDosePerLifetime"],
    "Immunization": ["lotNumber", "site", "route", "doseQuantity"],
    "Observation": ["method", "bodySite"],
}


def unverified_elements(bundle: dict) -> list[dict]:
    """Populated elements with disposition N, which need the separate assurance of REN-14."""
    out = []
    for e in bundle.get("entry", [])[1:]:
        res = e.get("resource", {})
        for path in N_ELEMENTS.get(res.get("resourceType", ""), []):
            nodes = [res]
            for part in path.split("."):
                nxt = []
                for n in nodes:
                    v = n.get(part) if isinstance(n, dict) else None
                    nxt += v if isinstance(v, list) else [v] if v is not None else []
                nodes = nxt
            if nodes:
                out.append({"resource": res.get("resourceType"), "id": res.get("id"), "element": path})
    return out


def _flatten_sections(sections: list[dict], parent: str = "") -> list[tuple[str, dict]]:
    out = []
    for sec in sections:
        path = f"{parent}/{_code(sec.get('code'))}" if parent else _code(sec.get("code"))
        out.append((path, sec))
        out += _flatten_sections(sec.get("section", []), path)
    return out


def expected_facts(bundle: dict) -> list[Fact]:
    idx = {e["fullUrl"]: e["resource"] for e in bundle.get("entry", [])}
    comp = bundle["entry"][0]["resource"]
    facts: list[Fact] = []

    def doc(element: str, value: Any) -> None:
        if value not in (None, ""):
            facts.append(Fact("document", -1, element, str(value)))

    patient = idx.get(comp["subject"]["reference"], {})
    doc("Bundle.identifier", bundle.get("identifier", {}).get("value"))
    doc("Composition.title", comp.get("title"))
    doc("Composition.status", comp.get("status"))
    doc("Composition.date", comp.get("date"))
    doc("Patient.name", _name(patient))
    doc("Patient.identifier", (patient.get("identifier") or [{}])[0].get("value"))
    doc("Patient.birthDate", patient.get("birthDate"))
    for a in comp.get("author", []):
        doc("Composition.author", _name(idx.get(a.get("reference"), {})))
    for at in comp.get("attester", []):
        doc("Composition.attester.party", _name(idx.get((at.get("party") or {}).get("reference"), {})))
        doc("Composition.attester.mode", at.get("mode"))
        doc("Composition.attester.time", at.get("time"))
    doc("Composition.custodian", _name(idx.get((comp.get("custodian") or {}).get("reference"), {})))
    doc("assurance", "Attested clinical issuance" if comp.get("attester") else
        "Machine-generated preserved snapshot - not clinically attested")

    for path, sec in _flatten_sections(comp.get("section", [])):
        facts.append(Fact(path, -1, "section.title", sec.get("title", "")))
        if sec.get("entry"):
            for i, ref in enumerate(sec["entry"]):
                res = idx.get(ref["reference"])
                if res is not None:
                    facts += [Fact(path, i, el, txt) for el, txt in entry_facts(res)]
        elif sec.get("emptyReason"):
            facts.append(Fact(path, -1, "section.emptyReason", _cc(sec["emptyReason"])[0] or _code(sec["emptyReason"])))
        elif not sec.get("section"):
            facts.append(Fact(path, -1, "section.text", _plain(sec.get("text", {}).get("div", ""))))
    return facts


def _n(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


_PATTERNS: dict[str, re.Pattern] = {}


def _pattern(want: str) -> re.Pattern:
    """Whitespace-tolerant, boundary-aware pattern for a canonical text form.

    Whitespace is optional between characters (renderers wrap lines), but a match must not be part of a longer
    token: "5 mg" does not match inside "25 mg" and "confirmed" does not match inside "unconfirmed". This is what
    makes a changed value fail the accuracy facet instead of matching by accident."""
    p = _PATTERNS.get(want)
    if p is None:
        chars = [c for c in want.lower() if not c.isspace()]
        body = r"\s*".join(re.escape(c) for c in chars)
        pre = r"(?<![0-9a-z])" if chars and chars[0].isalnum() else ""
        post = r"(?![0-9a-z])" if chars and chars[-1].isalnum() else ""
        p = _PATTERNS[want] = re.compile(pre + body + post)
    return p


def _find(hay: str, want: str, start: int = 0) -> int:
    if not want.strip():
        return start
    m = _pattern(want).search(hay, start)
    return m.start() if m else -1


def _has(hay: str, want: str) -> bool:
    return _find(hay, want) >= 0


def check(bundle: dict, page_text: str) -> FidelityResult:
    facts = expected_facts(bundle)
    res = FidelityResult(expected=len(facts))
    text = page_text.lower()
    comp = bundle["entry"][0]["resource"]
    flat = _flatten_sections(comp.get("section", []))

    # Section regions, located in document order by their titles (nested sections follow their parent).
    positions: list[tuple[str, int]] = []
    cursor = 0
    for path, sec in flat:
        pos = _find(text, sec.get("title", ""), cursor)
        if pos < 0:
            res.missing.append({"scope": path, "entry": -1, "element": "section.title", "text": sec.get("title")})
            continue
        positions.append((path, pos))
        cursor = pos + 1
    regions: dict[str, str] = {}
    for i, (path, pos) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        regions[path] = text[pos:end]

    # Entry regions inside each section: anchored on each entry's first fact, in order.
    by_entry: dict[tuple[str, int], list[Fact]] = {}
    for f in facts:
        if f.entry >= 0:
            by_entry.setdefault((f.scope, f.entry), []).append(f)
    entry_regions: dict[tuple[str, int], str] = {}
    for path in {k[0] for k in by_entry}:
        region = regions.get(path, "")
        keys = sorted(k for k in by_entry if k[0] == path)
        anchors, cur = [], 0
        for k in keys:
            a = _find(region, by_entry[k][0].text, cur)
            anchors.append(a)
            if a >= 0:
                cur = a + 1
        for j, k in enumerate(keys):
            if anchors[j] < 0:
                continue
            nxt = next((a for a in anchors[j + 1:] if a >= 0), len(region))
            entry_regions[k] = region[anchors[j]:nxt]

    for f in facts:
        if f.element == "section.title":
            continue
        if f.scope == "document":
            if not _has(text, f.text):
                res.missing.append(asdict(f))
            continue
        region = regions.get(f.scope, "")
        if f.entry >= 0:
            er = entry_regions.get((f.scope, f.entry))
            if er is not None and _has(er, f.text):
                continue
            if _has(region, f.text) or _has(text, f.text):
                res.misplaced.append(asdict(f))
            else:
                res.missing.append(asdict(f))
        elif not _has(region, f.text):
            (res.misplaced if _has(text, f.text) else res.missing).append(asdict(f))

    # Unsupported assertions: codes in a section region that belong to another section or to no entry.
    section_codes: dict[str, set[str]] = {}
    for f in facts:
        if f.scope != "document" and f.element.endswith(".code"):
            section_codes.setdefault(f.scope, set()).add(f.text)
    all_codes = {c: sec for sec, cs in section_codes.items() for c in cs}
    raw_regions: dict[str, str] = {}
    cursor = 0
    raw_pos = []
    for path, sec in flat:
        pos = page_text.find(sec.get("title", ""), cursor)
        if pos >= 0:
            raw_pos.append((path, pos))
            cursor = pos + len(sec.get("title", ""))
    for i, (path, pos) in enumerate(raw_pos):
        end = raw_pos[i + 1][1] if i + 1 < len(raw_pos) else len(page_text)
        raw_regions[path] = page_text[pos:end]
    narrative = {path: _plain((sec.get("text") or {}).get("div", "")) for path, sec in flat}
    for path in regions:
        # Traceability (REN-08): a code is supported by an entry of this section or by this section's source narrative.
        own = section_codes.get(path, set()) | {c for c in all_codes if _has(narrative.get(path, "").lower(), c)}
        flagged = {c for c, other in all_codes.items() if other != path and c not in own and _has(regions[path], c)}
        pat = SECTION_SYSTEM_PATTERNS.get(path.split("/")[-1])
        if pat:
            flagged |= {m for m in pat.findall(raw_regions.get(path, "")) if m not in own
                        and not _has(narrative.get(path, "").lower(), m)}
        res.foreign_codes += [{"scope": path, "code": c} for c in sorted(flagged)]
    return res

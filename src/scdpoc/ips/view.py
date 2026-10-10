"""A single, deterministic clinical view model derived from coded IPS content.

Everything a human sees - the Composition section narratives, the HTML page
and the PDF pages - is produced from this one view model, which is in turn
derived only from the coded FHIR resources. That gives the one-way
rendering rule required by ADR-001 / finding F-004: the visible document
cannot say anything the structured IPS does not say.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any

SECTION_ORDER = [
    ("11450-4", "Problem list"),
    ("48765-2", "Allergies and intolerances"),
    ("10160-0", "Medication summary"),
    ("11369-6", "History of immunizations"),
    ("30954-2", "Results"),
    ("18776-5", "Plan of care"),
]

# Columns cover every element of the rendition fidelity contract (see fidelity.py and
# profile 0.2.0, Annex A). Adding a contract element means adding it here too.
SECTION_COLUMNS = {
    "11450-4": ["Problem", "Code", "Status", "Severity", "Onset", "Details"],
    "48765-2": ["Substance / situation", "Code", "Status", "Criticality", "Reaction", "Details"],
    "10160-0": ["Medicine", "ATC", "Status", "Dosage", "Since", "Details"],
    "11369-6": ["Vaccine", "ATC", "Status", "Date", "Details"],
    "30954-2": ["Test", "LOINC", "Result", "Status", "Date", "Details"],
}
SEP = " · "
# Canonical assurance statements (profile PROV-03): the rendition states which assurance option applies.
ASSURANCE_SNAPSHOT = "Machine-generated preserved snapshot - not clinically attested"
ASSURANCE_ATTESTED = "Attested clinical issuance"

EMPTY_REASON_TEXT = {
    "nilknown": "Nil known",
    "notasked": "Not asked",
    "unavailable": "Unavailable",
    "withheld": "Withheld",
    "notstarted": "Not started",
    "closed": "Closed",
}


@dataclass
class SectionView:
    code: str
    title: str
    columns: list[str]
    rows: list[list[str]] = field(default_factory=list)
    empty_text: str | None = None
    subsections: list[SectionView] = field(default_factory=list)


@dataclass
class SummaryView:
    title: str
    document_id: str
    series_id: str
    issued: str
    language: str
    patient_name: str
    patient_id: str
    birth_date: str
    gender: str
    author: str
    custodian: str
    replaces: str | None
    sections: list[SectionView]
    status: str = ""
    attester: str = ""
    content_time: str = ""
    assurance: str = ""

    def visible_facts(self) -> list[str]:
        """Strings the renderer intends to show. NOT the fidelity check: see fidelity.py,
        which derives expectations from the FHIR resources independently."""
        facts = [self.patient_name, self.patient_id, self.birth_date, self.document_id]
        for s in self.sections:
            for row in s.rows:
                facts.extend(c for c in row if c)
            if s.empty_text:
                facts.append(s.empty_text)
        return facts


def _first_coding(cc: dict[str, Any] | None) -> dict[str, Any]:
    if not cc:
        return {}
    codings = cc.get("coding") or [{}]
    return codings[0]


def _display(cc: dict[str, Any] | None) -> str:
    c = _first_coding(cc)
    return (cc or {}).get("text") or c.get("display") or c.get("code") or ""


def _code(cc: dict[str, Any] | None) -> str:
    return _first_coding(cc).get("code", "")


def _status(cc: dict[str, Any] | None) -> str:
    return _code(cc)


def _join(*parts: str) -> str:
    return SEP.join(p for p in parts if p)


# Canonical text forms (profile 0.4.0, IPS binding, "Canonical text forms"; coverage matrix dispositions R and E).
# The Checker implements the same forms independently in fidelity.py; keep the two in step with the specification,
# not with each other. Elements with disposition N are shown generically under "Not verified automatically".
UNIT_WORDS = {"s": "second", "min": "minute", "h": "hour", "d": "day", "wk": "week", "mo": "month", "a": "year"}
WHEN = {"MORN": "in the morning", "AFT": "in the afternoon", "EVE": "in the evening", "NIGHT": "at night",
        "HS": "at bedtime", "WAKE": "on waking", "C": "with meals", "AC": "before meals", "PC": "after meals"}


def num(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def quantity(q: dict[str, Any] | None) -> str:
    if not q or q.get("value") is None:
        return ""
    return f"{num(q.get('value'))} {q.get('unit') or q.get('code') or ''}".strip()


def rng(r: dict[str, Any] | None) -> str:
    if not r or not r.get("low") or not r.get("high"):
        return ""
    return f"{num(r['low'].get('value'))} to {quantity(r['high'])}"


def ratio(r: dict[str, Any] | None) -> str:
    if not r or not r.get("numerator") or not r.get("denominator"):
        return ""
    return f"{quantity(r['numerator'])} per {quantity(r['denominator'])}"


def period(p: dict[str, Any] | None) -> str:
    if not p:
        return ""
    return " ".join(x for x in (f"from {p['start']}" if p.get("start") else "",
                                f"to {p['end']}" if p.get("end") else "") if x)


def timing(t: dict[str, Any] | None) -> str:
    r = (t or {}).get("repeat") or {}
    parts = []
    if r.get("frequency") and r.get("period"):
        f = r["frequency"]
        word = UNIT_WORDS.get(r.get("periodUnit", ""), r.get("periodUnit", ""))
        per = f"per {word}" if num(r["period"]) == "1" else f"per {num(r['period'])} {word}s"
        if r.get("frequencyMax"):
            parts.append(f"{f} to {r['frequencyMax']} times {per}")
        else:
            parts.append(f"{ {1: 'once', 2: 'twice'}.get(f, f'{f} times') } {per}")
    parts += [WHEN.get(w, w) for w in r.get("when", [])]
    parts += [f"at {t_[:5]}" for t_ in r.get("timeOfDay", [])]
    if r.get("boundsDuration"):
        parts.append(f"for {quantity(r['boundsDuration'])}")
    elif r.get("boundsPeriod"):
        parts.append(period(r["boundsPeriod"]))
    return _join(*parts)


def dosage(d: dict[str, Any]) -> str:
    parts = []
    for dr in d.get("doseAndRate") or []:
        if dr.get("doseQuantity"):
            parts.append(f"dose {quantity(dr['doseQuantity'])}")
        elif dr.get("doseRange"):
            parts.append(f"dose {rng(dr['doseRange'])}")
        if dr.get("rateQuantity"):
            parts.append(f"rate {quantity(dr['rateQuantity'])}")
        elif dr.get("rateRatio"):
            parts.append(f"rate {ratio(dr['rateRatio'])}")
    parts += [_display(d.get(k)) for k in ("route", "site", "method") if d.get(k)]
    parts.append(timing(d.get("timing")))
    if d.get("asNeededBoolean"):
        parts.append("as needed")
    elif d.get("asNeededCodeableConcept"):
        parts.append(f"as needed for {_display(d['asNeededCodeableConcept'])}")
    if d.get("maxDosePerPeriod"):
        parts.append(f"max {ratio(d['maxDosePerPeriod'])}")
    if d.get("maxDosePerAdministration"):
        parts.append(f"max {quantity(d['maxDosePerAdministration'])} per dose")
    parts += [_display(a) for a in d.get("additionalInstruction", [])]
    if d.get("patientInstruction"):
        parts.append(d["patientInstruction"])
    structured = _join(*parts)
    return d.get("text", "") + (f" ({structured})" if structured else "")


def when_form(res: dict[str, Any], prefix: str) -> str:
    """onset[x] / abatement[x] forms."""
    for k, v in res.items():
        if not k.startswith(prefix):
            continue
        kind = k[len(prefix):]
        if kind in ("DateTime", "String"):
            return v
        if kind == "Age":
            return quantity(v)
        if kind == "Period":
            return period(v)
        if kind == "Boolean" and v:
            return ""
    return ""


def notes(res: dict[str, Any]) -> list[str]:
    return [n.get("text", "") for n in res.get("note", []) if n.get("text")]


def value_form(obj: dict[str, Any]) -> str:
    if obj.get("valueQuantity"):
        return quantity(obj["valueQuantity"])
    if obj.get("valueCodeableConcept"):
        return _display(obj["valueCodeableConcept"])
    if "valueString" in obj:
        return obj["valueString"]
    if "valueBoolean" in obj:
        return "yes" if obj["valueBoolean"] else "no"
    if obj.get("valueRange"):
        return rng(obj["valueRange"])
    return ""


def reference_range(rr: dict[str, Any]) -> str:
    if rr.get("text"):
        return rr["text"]
    if rr.get("low") and rr.get("high"):
        return f"ref {num(rr['low'].get('value'))} to {quantity(rr['high'])}"
    return ""


# Elements with disposition N (coverage matrix). Shown generically; their fidelity needs separate assurance.
N_ELEMENTS = {
    "Condition": ["stage", "evidence"],
    "AllergyIntolerance": ["lastOccurrence", "reaction.exposureRoute"],
    "MedicationStatement": ["statusReason", "reasonCode"],
    "MedicationRequest": ["dispenseRequest", "substitution", "reasonCode"],
    "Immunization": ["lotNumber", "site", "route", "doseQuantity"],
    "Observation": ["method", "bodySite"],
}


def n_elements(res: dict[str, Any]) -> list[tuple[str, Any]]:
    out = []
    for path in N_ELEMENTS.get(res.get("resourceType", ""), []):
        head, _, tail = path.partition(".")
        vals = [res.get(head)] if not tail else [x.get(tail) for x in res.get(head, []) if isinstance(x, dict)]
        out += [(path, v) for v in vals if v not in (None, "", [], {})]
    return out


def _generic(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(_generic(x) for x in v)
    if isinstance(v, dict):
        if "coding" in v or "text" in v:
            return _display(v)
        if "summary" in v or "code" in v:
            return _generic(v.get("summary") or v.get("code"))
        if "value" in v:
            return quantity(v)
        return " ".join(_generic(x) for x in v.values())
    return str(v)


def details(res: dict[str, Any]) -> str:
    rt = res["resourceType"]
    parts: list[str] = []
    if rt == "Condition":
        parts += [_display(b) for b in res.get("bodySite", [])]
        ab = when_form(res, "abatement")
        if ab or res.get("abatementBoolean"):
            parts.append(f"resolved {ab}".strip())
    elif rt == "AllergyIntolerance":
        parts += [res["type"]] if res.get("type") else []
        parts += res.get("category", [])
        on = when_form(res, "onset")
        parts += [f"onset {on}"] if on else []
    elif rt == "Immunization":
        if res.get("statusReason"):
            parts.append(_display(res["statusReason"]))
        for pa in res.get("protocolApplied", []):
            n = pa.get("doseNumberPositiveInt", pa.get("doseNumberString"))
            if n is not None:
                parts.append(f"dose {n}")
    elif rt == "Observation":
        parts += [reference_range(r) for r in res.get("referenceRange", [])]
        parts += [f"{_display(c.get('code'))} {value_form(c)}".strip() for c in res.get("component", [])]
    parts += notes(res)
    unverified = n_elements(res)
    if unverified:
        parts.append("Not verified automatically: " + "; ".join(f"{p} {_generic(v)}" for p, v in unverified))
    return _join(*parts)


def row_for(resource: dict[str, Any]) -> list[str]:
    rt = resource["resourceType"]
    if rt == "Condition":
        return [_display(resource.get("code")), _code(resource.get("code")),
                _join(_status(resource.get("clinicalStatus")), _status(resource.get("verificationStatus"))),
                _display(resource.get("severity")) if resource.get("severity") else "",
                when_form(resource, "onset"), details(resource)]
    if rt == "AllergyIntolerance":
        reactions = [_join(", ".join(_display(s) for s in ([r["substance"]] if r.get("substance") else [])),
                           ", ".join(_display(m) for m in r.get("manifestation", [])), r.get("severity", ""))
                     for r in resource.get("reaction", [])]
        return [_display(resource.get("code")), _code(resource.get("code")),
                _join(_status(resource.get("clinicalStatus")), _status(resource.get("verificationStatus"))),
                resource.get("criticality", ""), "; ".join(reactions), details(resource)]
    if rt == "MedicationStatement":
        med = resource.get("medicationCodeableConcept")
        since = resource.get("effectivePeriod", {}).get("start") or resource.get("effectiveDateTime", "")
        return [_display(med), _code(med), resource.get("status", ""),
                "; ".join(dosage(d) for d in resource.get("dosage", [])), since, details(resource)]
    if rt == "MedicationRequest":
        med = resource.get("medicationCodeableConcept")
        return [_display(med), _code(med), _join(resource.get("status", ""), resource.get("intent", ""), "request"),
                "; ".join(dosage(d) for d in resource.get("dosageInstruction", [])), resource.get("authoredOn", ""),
                details(resource)]
    if rt == "Immunization":
        return [_display(resource.get("vaccineCode")), _code(resource.get("vaccineCode")),
                resource.get("status", ""), resource.get("occurrenceDateTime", "") or resource.get("occurrenceString", ""),
                details(resource)]
    if rt == "Observation":
        interp = ", ".join(_display(i) for i in resource.get("interpretation", []))
        return [_display(resource.get("code")), _code(resource.get("code")), _join(value_form(resource), interp),
                resource.get("status", ""), resource.get("effectiveDateTime", ""), details(resource)]
    return [_plain((resource.get("text") or {}).get("div", "")) or rt]


def _index(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["fullUrl"]: e["resource"] for e in bundle.get("entry", []) if "fullUrl" in e}


def section_view(section: dict[str, Any], resolve) -> SectionView:
    code = _code(section.get("code"))
    sv = SectionView(code=code, title=section.get("title", ""), columns=SECTION_COLUMNS.get(code, ["Entry"]))
    sv.subsections = [section_view(sub, resolve) for sub in section.get("section", [])]
    for ref in section.get("entry", []):
        res = resolve(ref["reference"])
        if res is not None:
            sv.rows.append(row_for(res))
    if not sv.rows and not sv.subsections:
        if section.get("emptyReason"):
            er = _code(section.get("emptyReason"))
            sv.empty_text = EMPTY_REASON_TEXT.get(er, _display(section.get("emptyReason")) or "No information")
        else:
            # Narrative-only section: the narrative is the clinical content and must be shown.
            sv.empty_text = _plain(section.get("text", {}).get("div", "")) or "No information"
    return sv


def _plain(div: str) -> str:
    import re
    from html import unescape
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", div or "")).split())


def _person(res: dict[str, Any]) -> str:
    if res.get("resourceType") == "Organization":
        return res.get("name", "")
    if res.get("resourceType") == "Device":
        return ((res.get("deviceName") or [{}])[0]).get("name", "")
    n = (res.get("name") or [{}])[0]
    return n.get("text") or " ".join([*n.get("prefix", []), *n.get("given", []), n.get("family", "")]).strip()


def build_view(bundle: dict[str, Any]) -> SummaryView:
    idx = _index(bundle)
    comp = bundle["entry"][0]["resource"]
    patient = idx[comp["subject"]["reference"]]
    authors = [_person(idx.get(a.get("reference"), {})) for a in comp.get("author", [])]
    attesters = [f"{_person(idx.get((a.get('party') or {}).get('reference'), {}))} ({a.get('mode', '')}, {a.get('time', '')})"
                 for a in comp.get("attester", [])]
    custodian = idx.get(comp.get("custodian", {}).get("reference", ""), {})
    name = patient["name"][0]
    replaces = None
    for rel in comp.get("relatesTo", []):
        if rel.get("code") == "replaces":
            replaces = rel.get("targetIdentifier", {}).get("value")
    order = {c: i for i, (c, _) in enumerate(SECTION_ORDER)}
    ordered = sorted(comp.get("section", []), key=lambda s: order.get(_code(s.get("code")), len(order)))
    sections = [section_view(s, idx.get) for s in ordered]
    return SummaryView(
        title=comp.get("title", ""),
        document_id=bundle["identifier"]["value"],
        series_id=comp.get("identifier", {}).get("value", ""),
        issued=bundle["timestamp"],
        language=comp.get("language", ""),
        patient_name=name.get("text") or " ".join([*name.get("given", []), name.get("family", "")]),
        patient_id=patient["identifier"][0]["value"],
        birth_date=patient.get("birthDate", ""),
        gender=patient.get("gender", ""),
        author=", ".join(x for x in authors if x),
        custodian=custodian.get("name", ""),
        replaces=replaces,
        sections=sections,
        status=comp.get("status", ""),
        attester="; ".join(attesters),
        content_time=comp.get("date", ""),
        assurance=ASSURANCE_ATTESTED if comp.get("attester") else ASSURANCE_SNAPSHOT,
    )


def section_xhtml(sv: SectionView) -> str:
    """FHIR narrative (xhtml) for one section, generated from the same view model."""
    if sv.empty_text:
        return f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{escape(sv.empty_text)}</p></div>'
    head = "".join(f"<th>{escape(c)}</th>" for c in sv.columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in sv.rows)
    return (f'<div xmlns="http://www.w3.org/1999/xhtml"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")

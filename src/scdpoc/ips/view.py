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
]

SECTION_COLUMNS = {
    "11450-4": ["Problem", "Code", "Status", "Onset"],
    "48765-2": ["Substance / situation", "Code", "Criticality", "Reaction"],
    "10160-0": ["Medicine", "ATC", "Dosage", "Since"],
    "11369-6": ["Vaccine", "ATC", "Date"],
    "30954-2": ["Test", "LOINC", "Result", "Date"],
}

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

    def visible_facts(self) -> list[str]:
        """Strings that MUST appear in any faithful human rendition."""
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


def row_for(resource: dict[str, Any]) -> list[str]:
    rt = resource["resourceType"]
    if rt == "Condition":
        return [_display(resource.get("code")), _code(resource.get("code")),
                _status(resource.get("clinicalStatus")), resource.get("onsetDateTime", "")]
    if rt == "AllergyIntolerance":
        reaction = ""
        for r in resource.get("reaction", []):
            reaction = ", ".join(_display(m) for m in r.get("manifestation", []))
        return [_display(resource.get("code")), _code(resource.get("code")),
                resource.get("criticality", ""), reaction]
    if rt == "MedicationStatement":
        med = resource.get("medicationCodeableConcept")
        dosage = "; ".join(d.get("text", "") for d in resource.get("dosage", []))
        since = resource.get("effectivePeriod", {}).get("start") or resource.get("effectiveDateTime", "")
        return [_display(med), _code(med), dosage, since]
    if rt == "Immunization":
        return [_display(resource.get("vaccineCode")), _code(resource.get("vaccineCode")),
                resource.get("occurrenceDateTime", "")]
    if rt == "Observation":
        q = resource.get("valueQuantity", {})
        value = f"{q.get('value')} {q.get('unit', '')}".strip() if q else ""
        return [_display(resource.get("code")), _code(resource.get("code")), value,
                resource.get("effectiveDateTime", "")]
    return [rt]


def _index(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["fullUrl"]: e["resource"] for e in bundle.get("entry", []) if "fullUrl" in e}


def section_view(section: dict[str, Any], resolve) -> SectionView:
    code = _code(section.get("code"))
    sv = SectionView(code=code, title=section.get("title", ""), columns=SECTION_COLUMNS.get(code, ["Entry"]))
    for ref in section.get("entry", []):
        res = resolve(ref["reference"])
        if res is not None:
            sv.rows.append(row_for(res))
    if not sv.rows:
        er = _code(section.get("emptyReason"))
        sv.empty_text = EMPTY_REASON_TEXT.get(er, "No information")
    return sv


def build_view(bundle: dict[str, Any]) -> SummaryView:
    idx = _index(bundle)
    comp = bundle["entry"][0]["resource"]
    patient = idx[comp["subject"]["reference"]]
    author = idx.get(comp["author"][0]["reference"], {})
    custodian = idx.get(comp.get("custodian", {}).get("reference", ""), {})
    name = patient["name"][0]
    replaces = None
    for rel in comp.get("relatesTo", []):
        if rel.get("code") == "replaces":
            replaces = rel.get("targetIdentifier", {}).get("value")
    by_code = {_code(s.get("code")): s for s in comp.get("section", [])}
    sections = [section_view(by_code[c], idx.get) for c, _ in SECTION_ORDER if c in by_code]
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
        author=author.get("name", ""),
        custodian=custodian.get("name", ""),
        replaces=replaces,
        sections=sections,
    )


def section_xhtml(sv: SectionView) -> str:
    """FHIR narrative (xhtml) for one section, generated from the same view model."""
    if sv.empty_text:
        return f'<div xmlns="http://www.w3.org/1999/xhtml"><p>{escape(sv.empty_text)}</p></div>'
    head = "".join(f"<th>{escape(c)}</th>" for c in sv.columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in sv.rows)
    return (f'<div xmlns="http://www.w3.org/1999/xhtml"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")
